"""Owned native browser sessions; short clients never own persistent reservations."""
import hashlib
import json
import math
import os
import queue
import secrets
import subprocess
import shutil
import sys
import tempfile
import threading
import time
import uuid
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from multiprocessing.connection import Client, Listener, AuthenticationError
from multiprocessing.connection import answer_challenge, deliver_challenge

from filelock import FileLock

from . import __version__, launches
from .core import KitError, Workspace, read, save
from .operations import Journal, data_root
from .processes import fingerprint, matches, kill_tree, alive

MAX_BYTES = 262144


def close_connection(connection):
    try:
        connection.close()
    except OSError:
        pass  # A deadline may close the same handle while authentication unwinds.


def records(ws):
    result = []
    for path in sorted((ws.state / 'native-browsers').glob('*.json')):
        try:
            value = read(path)
            if isinstance(value, dict) and value.get('backend') == 'agent-browser':
                result.append(value)
        except (OSError, ValueError):
            result.append({'backend': 'agent-browser', 'status': 'unknown', 'session_id': path.stem})
    return result


def inspect_record(record):
    try:
        identities = [record['identity'], *record.get('children', [])]
        live = [matches(value) for value in identities]
        state = 'running' if all(live) and record.get('status') == 'ready' else 'stopped' if not any(live) else 'unknown'
        return {**record, 'state': state}
    except Exception:
        return {**record, 'state': 'unknown'}


def session_key(ws):
    text = str(ws.root) + '\0' + ws.environment + '\0' + str(ws.actor or 'default')
    return hashlib.sha256(text.encode()).hexdigest()[:32]


def control(ws, sid):
    if not isinstance(sid, str) or len(sid) != 32 or any(c not in '0123456789abcdef' for c in sid):
        raise KitError('Invalid native session id')
    return data_root() / 'browser-control' / ws.identity / sid


def private_directory(path):
    path.mkdir(parents=True, exist_ok=True)
    if os.name != 'nt':
        path.chmod(0o700)
    else:
        # Restrict tokens/profiles even in a workspace on a broadly readable drive.
        import getpass
        result = subprocess.run(['icacls', str(path), '/inheritance:r', '/grant:r',
                                 getpass.getuser() + ':(OI)(CI)F'], capture_output=True,
                                creationflags=subprocess.CREATE_NO_WINDOW, timeout=10)
        if result.returncode:
            raise KitError('Could not protect native browser control directory')


def _record(ws, sid):
    return read(ws.state / 'native-browsers' / (sid + '.json'), {})


def validate_scope(ws, record):
    if (record.get('workspace') != str(ws.root) or record.get('environment') != ws.environment
            or (record.get('actor') or 'default') != (ws.actor or 'default')):
        raise KitError('Native session belongs to a different workspace, environment or actor')


def receipt(response, operation):
    # Explicit page reads can return private form values. Deliver them to the client,
    # but do not turn their body into a persistent ordinary receipt/log.
    if operation == 'exec':
        return {key: value for key, value in response.items() if key != 'data'}
    return response


class _AuthenticationDeadline:
    """Keep the standard-library wire protocol, with a shared handshake budget."""
    def __init__(self, connection, timeout):
        self.connection = connection
        self.deadline = time.monotonic() + timeout

    def recv_bytes(self, maxlength=None):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0 or not self.connection.poll(remaining):
            raise TimeoutError('Native IPC authentication timed out')
        return self.connection.recv_bytes(maxlength)

    def send_bytes(self, value):
        if time.monotonic() >= self.deadline:
            raise TimeoutError('Native IPC authentication timed out')
        return self.connection.send_bytes(value)


def connect_client(address, *, family, authkey, timeout):
    """Bound connection plus authentication; no action has been sent on failure."""
    if not isinstance(timeout, (int, float)) or not 0 < timeout <= 3600:
        raise KitError('Native IPC connection timeout must be positive and at most one hour')
    completed = threading.Event()
    lock = threading.Lock()
    state = {'expired': False}

    def connect():
        connection = None
        try:
            connection = Client(address, family=family, authkey=None)
            with lock:
                if state['expired']:
                    close_connection(connection)
                    return
                state['connection'] = connection
            secured = _AuthenticationDeadline(connection, timeout)
            answer_challenge(secured, authkey)
            deliver_challenge(secured, authkey)
            with lock:
                if state['expired']:
                    close_connection(connection)
                else:
                    state['result'] = connection
        except (OSError, EOFError, AuthenticationError, TimeoutError, ValueError) as error:
            if connection is not None:
                close_connection(connection)
            with lock:
                state['error'] = error
        finally:
            completed.set()

    threading.Thread(target=connect, daemon=True).start()
    if not completed.wait(timeout):
        with lock:
            state['expired'] = True
            connection = state.get('connection')
        if connection is not None:
            close_connection(connection)
        raise KitError('Native IPC connection timed out before sending; no action was executed')
    with lock:
        connection = state.get('result')
    if connection is None:
        raise KitError('Native IPC connection failed before sending; no action was executed')
    return connection


def accept_authenticated(listener, token, pending, *, timeout=5, limit=8):
    """A stalled unauthenticated client must not block future legitimate clients."""
    slots = threading.BoundedSemaphore(limit)

    def authenticate(connection):
        try:
            secured = _AuthenticationDeadline(connection, timeout)
            deliver_challenge(secured, token)
            answer_challenge(secured, token)
            pending.put(connection)
        except (EOFError, OSError, AuthenticationError, TimeoutError, ValueError):
            connection.close()
        finally:
            slots.release()

    while True:
        try:
            connection = listener.accept()
        except (EOFError, OSError):
            return
        if not slots.acquire(blocking=False):
            connection.close()
            continue
        threading.Thread(target=authenticate, args=(connection,), daemon=True).start()


def storage_state(auth):
    from urllib.parse import urlsplit
    state = auth.get('storage_state', auth) if isinstance(auth, dict) else None
    if (not isinstance(state, dict) or set(state) - {'cookies', 'origins'}
            or not isinstance(state.get('cookies'), list) or not isinstance(state.get('origins'), list)):
        raise KitError('Authentication requires supported cookies/localStorage state; opaque tokens are not imported')
    for cookie in state['cookies']:
        if (not isinstance(cookie, dict) or any(not isinstance(cookie.get(key), str) for key in ('name', 'value', 'domain', 'path'))
                or not cookie['name'] or not cookie['domain'] or not cookie['path'].startswith('/')):
            raise KitError('Invalid browser authentication cookie')
        if (any(key in cookie and type(cookie[key]) is not bool for key in ('httpOnly', 'secure'))
                or ('expires' in cookie and (type(cookie['expires']) not in (int, float) or not math.isfinite(cookie['expires'])))
                or cookie.get('sameSite', 'Lax') not in ('Strict', 'Lax', 'None')):
            raise KitError('Invalid browser authentication cookie attributes')
    for origin in state['origins']:
        if not isinstance(origin, dict) or set(origin) - {'origin', 'localStorage'}:
            raise KitError('Only cookies and localStorage authentication are supported')
        if not isinstance(origin.get('origin'), str):
            raise KitError('Invalid browser authentication origin')
        try:
            parsed = urlsplit(origin['origin'])
            parsed.port
        except ValueError:
            raise KitError('Invalid browser authentication origin') from None
        if (parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password
                or parsed.path not in ('', '/') or parsed.query or parsed.fragment):
            raise KitError('Invalid browser authentication origin')
        values = origin.get('localStorage')
        if not isinstance(values, list) or any(not isinstance(v, dict) or set(v) != {'name', 'value'}
                or any(not isinstance(v[key], str) for key in ('name', 'value')) for v in values):
            raise KitError('Invalid browser authentication localStorage')
    return state


def request(ws, sid, payload, timeout=60):
    control(ws, sid)
    record = _record(ws, sid)
    validate_scope(ws, record)
    if inspect_record(record).get('state') != 'running':
        raise KitError('Native session is stopped or unverified; explicitly start a new session')
    private = read(control(ws, sid) / 'connection.json', {})
    if private.get('instance_id') != record.get('instance_id'):
        raise KitError('Native session control identity changed')
    connection = connect_client(private['address'], family=private['family'],
                                authkey=bytes.fromhex(private['token']), timeout=timeout)
    try:
        if not isinstance(payload, dict):
            raise KitError('Native request must be a JSON object')
        message = {'id': uuid.uuid4().hex, **payload}
        encoded = json.dumps(message, ensure_ascii=False, allow_nan=False).encode()
        if len(encoded) > MAX_BYTES:
            raise KitError('Native request exceeds 256 KiB')
        connection.send_bytes(encoded)
        if not connection.poll(timeout):
            raise KitError('Native action outcome_unknown; inspect the session and do not repeat the action')
        response = json.loads(connection.recv_bytes(MAX_BYTES))
        if not isinstance(response, dict) or response.get('id') != message['id'] or response.get('status') not in ('ok', 'blocked', 'closed'):
            raise ValueError('Invalid response')
        return response
    except (EOFError, OSError, ValueError, UnicodeError):
        raise KitError('Native action outcome_unknown; connection closed after sending') from None
    finally:
        connection.close()


@launches.guarded
def start(ws, **options):
    ws.state.mkdir(parents=True, exist_ok=True)
    with launches.launch_lock(FileLock(str(ws.state / 'browser-mode.lock'), timeout=600)):
        return _start(ws, **options)


def _start(ws, *, url='about:blank', services=(), auth=None, auth_state=None, offline=False, visual=None, timeout_ms=10000, session_id=None):
    from .observer_cli import require_private_output
    from .browser import selected_backend
    selected_backend(ws)  # Validate migration before any service/browser side effects.
    if (ws.state / 'browser.json').exists():
        from .browser import _legacy_manage
        if _legacy_manage(ws, 'status').get('status') not in ('stopped', 'missing'):
            raise KitError('Stop the existing owned compatibility browser explicitly before starting native mode')
    from .observation import url as validate_url
    if url != 'about:blank':
        validate_url(url)
    if type(timeout_ms) is not int or not 1 <= timeout_ms <= 120000:
        raise KitError('Native timeout must be 1..120000 milliseconds')
    if type(ws.cfg.get('browser', {}).get('headless', False)) is not bool:
        raise KitError('browser.headless must be a boolean')
    sid = session_id or session_key(ws)
    directory = control(ws, sid)
    old = _record(ws, sid)
    if old:
        validate_scope(ws, old)
        if inspect_record(old)['state'] == 'running':
            if url != 'about:blank' or visual or services or auth or auth_state:
                raise KitError('Session already running; use explicit exec navigation or close it before changing preparation')
            return old
    private_directory(directory)
    with FileLock(str(directory / 'startup.lock'), timeout=60):
        old = _record(ws, sid)
        if old:
            validate_scope(ws, old)
        if old and inspect_record(old)['state'] == 'running':
            if url != 'about:blank' or visual or services or auth or auth_state:
                raise KitError('Session already running; use explicit exec navigation or close it before changing preparation')
            return old
        if old and inspect_record(old)['state'] == 'unknown':
            raise KitError('Previous native session has unconfirmed processes; inspect it before starting again')
        require_private_output(ws)
        settings = {'workspace': str(ws.root), 'environment': ws.environment, 'actor': ws.actor,
                    'session_id': sid, 'instance_id': uuid.uuid4().hex, 'url': url,
                    'services': list(services), 'auth_name': auth, 'offline': offline,
                    'auth_state': auth_state,
                    'visual': visual, 'timeout_ms': timeout_ms, 'generation': launches.admitted_generation() or launches.gate()}
        save(directory / 'settings.json', settings)
        for name in ('ready.json', 'connection.json'):
            (directory / name).unlink(missing_ok=True)
        command = [sys.executable, '-I', '-m', 'echo_kit.native_sessions', '--worker', str(directory / 'settings.json')]
        options = {}
        if os.name == 'nt':
            options['creationflags'] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_BREAKAWAY_FROM_JOB
        else:
            options['start_new_session'] = True
        with (directory / 'worker.log').open('w', encoding='utf-8') as errors:
            with launches.process(ws, 'task', lambda: subprocess.Popen(command, stdin=subprocess.DEVNULL,
                    stdout=errors, stderr=errors, env={**os.environ, 'PYTHONIOENCODING': 'utf-8'}, **options)) as (child, identity):
                deadline = time.monotonic() + 600
                while time.monotonic() < deadline:
                    launches.checkpoint()
                    ready = read(directory / 'ready.json', {})
                    if ready:
                        if ready.get('status') != 'ready':
                            raise KitError(ready.get('error', 'Native browser preparation failed'))
                        return _record(ws, sid)
                    if child.poll() is not None:
                        raise KitError('Native coordinator exited before readiness; inspect its private worker.log')
                    time.sleep(.05)
                raise KitError('Native preparation timed out; inspect its tracked operation')


class NativeTarget:
    def __init__(self, ws, output, control_dir, runtime, timeout_ms=10000, headless=False):
        self.ws, self.output, self.directory, self.runtime = ws, output, control_dir, runtime
        self.timeout_ms, self.headless = timeout_ms, headless
        self.viewport_size = {'width': 1440, 'height': 900}
        self.processes = []
        self.identities = []

    def _alive(self):
        launches.checkpoint()
        if any(proc.poll() is not None for proc in self.processes) or not all(matches(i) for i in self.identities):
            raise KitError('Native browser or daemon stopped; automatic recreation is disabled')

    def call(self, action, **params):
        self._alive()
        response = self.connection.request(action, **params)
        if not response.get('success'):
            error = KitError('Native action failed: ' + str(response.get('code') or 'browser_action_failed'))
            error.details = {'native_error': response.get('code'), 'outcome_unknown': response.get('code') == 'outcome_unknown'}
            raise error
        return response

    def evaluate(self, script):
        data = self.call('evaluate', script=script).get('data', {})
        if data.get('unserializable'):
            raise KitError('Evaluation did not return a JSON value')
        return data.get('result')

    def command(self, command):
        from .native_transport import translate
        params = translate(command)
        action = params.pop('action')
        if action in ('screenshot', 'trace_stop', 'diff_screenshot'):
            name = 'output' if action == 'diff_screenshot' else 'path'
            proposed = Path(params.get(name, str(self.output / (uuid.uuid4().hex + '.png'))))
            if not proposed.resolve().is_relative_to(self.output.resolve()):
                raise KitError('Native artifacts must remain in the owned observation directory')
            params[name] = str(proposed)
            if action == 'diff_screenshot':
                baseline = Path(params['baseline'])
                if not baseline.resolve().is_relative_to(self.output.resolve()) or not baseline.is_file():
                    raise KitError('Diff baseline must belong to this observation session')
        return self.call(action, **params)

    def screenshot(self, path, selector=None):
        path = Path(path)
        if not path.resolve().is_relative_to(self.output.resolve()):
            raise KitError('Screenshot must remain inside this observation unit')
        result = self.call('screenshot', path=str(path), selector=selector, fullPage=False, annotate=False, format='png')
        if not path.is_file():
            raise KitError('Native screenshot was not written')
        return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'native': result.get('data')}

    def set_viewport(self, value):
        self.call('viewport', width=value['width'], height=value['height'], deviceScaleFactor=1, mobile=False)
        self.viewport_size = dict(value)

    def _spawn(self, arguments, **options):
        child = subprocess.Popen(arguments, stdin=subprocess.DEVNULL, **options)
        # A newly exec'd Linux process can briefly expose empty argv while its
        # runtime initializes. Never publish that transient fingerprint as ownership.
        deadline = time.monotonic() + 5
        previous = None
        try:
            while child.poll() is None and time.monotonic() < deadline:
                launches.checkpoint()
                identity = fingerprint(child.pid)
                if identity.get('command') == arguments and identity == previous:
                    return child
                previous = identity
                time.sleep(.02)
            raise KitError('Native process did not establish its expected launch identity')
        except BaseException:
            child.kill()
            child.wait(timeout=5)
            raise

    @contextmanager
    def running(self, url):
        from .native_transport import AgentConnection
        self.directory.mkdir(parents=True, exist_ok=True)
        profile = self.directory / 'profile'
        profile.mkdir(exist_ok=True)
        arguments = [self.runtime['chrome'], '--remote-debugging-address=127.0.0.1', '--remote-debugging-port=0',
                     '--user-data-dir=' + str(profile), '--no-first-run', '--no-default-browser-check',
                     '--window-size=1440,900', '--disable-background-mode',
                     '--disable-background-networking', '--disable-component-update', '--disable-sync',
                     '--disable-background-timer-throttling', '--disable-backgrounding-occluded-windows',
                     '--disable-renderer-backgrounding', '--disable-stack-profiler', 'about:blank']
        if self.headless:
            arguments.append('--headless=new')
        if os.name != 'nt' and getattr(os, 'geteuid', lambda: 1)() == 0:
            arguments.append('--no-sandbox')
        options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
        with ExitStack() as stack:
            errors = stack.enter_context((self.directory / 'engine.log').open('w', encoding='utf-8'))
            chrome, identity = stack.enter_context(launches.process(self.ws, 'task', lambda: self._spawn(
                arguments, stdout=errors, stderr=errors, **options)))
            self.processes.append(chrome); self.identities.append(identity)
            try:
                deadline = time.monotonic() + 30
                port_file = profile / 'DevToolsActivePort'
                while True:
                    launches.checkpoint()
                    if chrome.poll() is not None or time.monotonic() > deadline:
                        raise KitError('Chrome did not expose its private debugging endpoint; check desktop availability or headless mode')
                    try:
                        parts = port_file.read_text().splitlines()
                        port = int(parts[0])
                        if len(parts) >= 2 and 0 < port < 65536 and parts[1].startswith('/devtools/browser/'):
                            break
                    except (OSError, ValueError, IndexError):
                        pass  # Windows can observe the new file before Chrome releases its write lock.
                    time.sleep(.03)
                self.endpoint = 'ws://127.0.0.1:' + str(port) + parts[1]
                socket_dir = Path(tempfile.gettempdir()) / ('echo-ab-' + uuid.uuid4().hex[:20])
                private_directory(socket_dir)
                # Keep Unix socket paths short; directory is owner-only user data.
                session = 'kit' + uuid.uuid4().hex[:16]
                env = {key: value for key, value in os.environ.items() if not key.startswith('AGENT_BROWSER_')}
                env.update(AGENT_BROWSER_DAEMON='1', AGENT_BROWSER_SESSION=session,
                           AGENT_BROWSER_SOCKET_DIR=str(socket_dir),
                           AGENT_BROWSER_CDP=self.endpoint, AGENT_BROWSER_PIN_TAB='1',
                           AGENT_BROWSER_NO_AUTO_DIALOG='1', AGENT_BROWSER_IDLE_TIMEOUT_MS='0')
                save(self.directory / 'agent-config.json', {})
                env['AGENT_BROWSER_CONFIG'] = str(self.directory / 'agent-config.json')
                daemon, identity = stack.enter_context(launches.process(self.ws, 'task', lambda: self._spawn(
                    [self.runtime['agent_browser']], stdout=errors, stderr=errors, env=env, **options)))
                self.processes.append(daemon); self.identities.append(identity)
                self.connection = AgentConnection(socket_dir, session, self.timeout_ms / 1000)
                deadline = time.monotonic() + 20
                while True:
                    self._alive()
                    try:
                        attached = self.connection.request('launch', cdpUrl=self.endpoint)
                        if attached.get('success'):
                            break
                        if time.monotonic() > deadline:
                            raise KitError('Pinned daemon attach failed: ' + str(attached.get('code') or attached.get('error')))
                        time.sleep(.05)
                    except (OSError, KitError):
                        if time.monotonic() > deadline:
                            raise KitError('Pinned agent-browser daemon did not attach to the owned Chrome') from None
                        time.sleep(.05)
                self.set_viewport(self.viewport_size)
                self.call('navigate', url=url)
                self.call('trace_start')
                yield self
            finally:
                try:
                    if len(self.processes) > 1 and all(p.poll() is None for p in self.processes):
                        self.call('trace_stop', path=str(self.output / ('trace-' + self.directory.name + '.json')))
                finally:
                    failures = []
                    for proc, identity in reversed(list(zip(self.processes, self.identities))):
                        try:
                            if proc.poll() is None:
                                targets, incomplete = kill_tree(identity, {})
                                proc.wait(timeout=5)
                                end = time.monotonic() + 5
                                while time.monotonic() < end and any(alive(target) for target in targets):
                                    time.sleep(.03)
                                if incomplete or any(alive(target) for target in targets):
                                    failures.append(identity.get('pid'))
                        except Exception:
                            failures.append(identity.get('pid'))
                    # Only delete this instance's private profile, after every owned process exited.
                    resolved = profile.resolve()
                    if (all(p.poll() is not None for p in self.processes) and profile.exists()
                            and not profile.is_symlink() and resolved.is_relative_to(self.directory.resolve())):
                        try:
                            shutil.rmtree(resolved)
                        except OSError:
                            pass  # Retain the private profile when Chrome still has a file open.
                    if failures:
                        error = KitError('Native process exit unconfirmed; retained owned process identities')
                        error.details = {'cleanup_unconfirmed': True}
                        raise error


def worker(path):
    settings = read(path)
    if settings.get('auth_state') is not None:
        save(path, {key: value for key, value in settings.items() if key != 'auth_state'})
    ws = Workspace(settings['workspace'])
    if settings['environment'] != ws.environment:
        ws = Workspace(settings['workspace'], settings['environment'])
    ws.actor, ws.startup_progress = settings.get('actor'), False
    directory = Path(path).parent
    sid = settings['session_id']
    public_file = ws.state / 'native-browsers' / (sid + '.json')
    record = {'backend': 'agent-browser', 'session_id': sid, 'instance_id': settings['instance_id'],
              'workspace': str(ws.root), 'environment': ws.environment, 'actor': ws.actor,
              'identity': fingerprint(os.getpid()), 'children': [], 'status': 'starting',
              'version': __version__, 'headless': bool(ws.cfg.get('browser', {}).get('headless', False)),
              'trace_format': 'chrome-trace-events', 'endpoint': None, 'viewer_url': None}
    from .browser import configuration_sources, LEGACY_FIELDS
    record['configuration_sources'] = configuration_sources(ws)
    record['unused_configuration_fields'] = [key for key in LEGACY_FIELDS if key in ws.cfg.get('browser', {})]
    save(public_file, record)
    confirmed = False
    output = None
    try:
        from .native_runtime import prepare
        from .services import Services
        from .auth import authenticate
        from .operations import native_browser_resource
        with launches.scope(settings['generation']):
            runtime = prepare(offline=settings['offline'])
            manager = Services(ws)
            names, jobs = manager._plan(settings['services'], None)
            with manager.reservation(names, 'browser.native', extra={native_browser_resource(ws, sid): 'shared'}) as (oid, prepared):
                record['operation_id'] = oid
                output = ws.state / 'observations' / oid
                output.mkdir(parents=True, exist_ok=False)
                record['output_dir'] = str(output)
                from .storage_metadata import own
                Journal().protect_evidence(oid, output)
                own(ws, output, 'observations', oid)
                if names:
                    manager._up_all(names, jobs)
                auth = authenticate(ws, settings['auth_name'])[1] if settings['auth_name'] else settings.get('auth_state')
                engine = NativeTarget(ws, output, directory / settings['instance_id'] / 'target', runtime, settings['timeout_ms'], record['headless'])
                with engine.running(settings['url']):
                    if auth:
                        state = storage_state(auth)
                        private_state = directory / 'authentication.json'
                        save(private_state, state)
                        try:
                            loaded = engine.call('state_load', path=str(private_state)).get('data', {})
                            if not loaded.get('loaded') or loaded.get('skippedOrigins'):
                                raise KitError('Browser authentication state was not completely restored')
                            # The pinned dependency visits each localStorage origin while loading.
                            engine.call('navigate', url=settings['url'])
                        finally:
                            private_state.unlink(missing_ok=True)
                    prepared()
                    record.update(status='ready', children=engine.identities, cdp_endpoint=engine.endpoint,
                                  agent_browser_version=runtime['agent_browser_version'], chrome_version=runtime['chrome_version'])
                    save(public_file, record); save(output / 'session.json', record)
                    from .workspace_index import remember
                    from .tray import notify
                    remember(ws); notify(ws)
                    family = 'AF_PIPE' if os.name == 'nt' else 'AF_UNIX'
                    ipc_directory = Path(tempfile.gettempdir()) / ('echo-ipc-' + uuid.uuid4().hex[:20])
                    private_directory(ipc_directory)
                    address = r'\\.\pipe\echo-kit-' + uuid.uuid4().hex if os.name == 'nt' else str(ipc_directory / 'control.sock')
                    token = secrets.token_bytes(32)
                    listener = Listener(address, family=family, authkey=None)
                    save(directory / 'connection.json', {'address': address, 'family': family, 'token': token.hex(), 'instance_id': record['instance_id']})
                    visual = None
                    if settings.get('visual'):
                        from .native_visual import NativeVisual
                        @contextmanager
                        def reference_factory(url):
                            reference = NativeTarget(ws, output, directory / ('reference-' + uuid.uuid4().hex[:10]), runtime,
                                                     settings['timeout_ms'], record['headless'])
                            with reference.running(url):
                                yield reference
                        visual = NativeVisual(ws, output, engine, reference_factory, timeout_ms=settings['timeout_ms'],
                                              **settings['visual'])
                        record['sources'] = visual.sources
                        save(public_file, record)
                        save(output / 'session.json', record)
                    pending = queue.Queue()
                    threading.Thread(target=accept_authenticated, args=(listener, token, pending), daemon=True).start()
                    save(directory / 'ready.json', {'status': 'ready'})
                    seen_requests = set()
                    try:
                        while True:
                            engine._alive()
                            try:
                                connection = pending.get(timeout=.2)
                            except queue.Empty:
                                continue
                            try:
                                try:
                                    if not connection.poll(5):
                                        continue
                                    message = json.loads(connection.recv_bytes(MAX_BYTES))
                                    if not isinstance(message, dict):
                                        raise ValueError()
                                except (EOFError, OSError, ValueError, UnicodeError):
                                    continue
                                oid_image = uuid.uuid4().hex
                                began = time.monotonic()
                                response = {'id': message.get('id'), 'protocol': 1, 'version': __version__,
                                            'session_id': sid, 'operation_id': oid, 'observation_id': oid_image,
                                            'backend': 'agent-browser', 'trace_format': 'chrome-trace-events',
                                            'time': datetime.now(timezone.utc).isoformat(),
                                            'processes': [{'pid': item['pid'], 'created': item['created']} for item in engine.identities],
                                            'status': 'ok', 'images': []}
                                closing = False
                                try:
                                    from .observation import validate, request_id
                                    if message.get('op') == 'exec':
                                        if not request_id(message) or message.get('page', 'target') not in ('target', 'reference'):
                                            raise KitError('Invalid native execution request')
                                    else:
                                        validate(message)
                                    if message['id'] in seen_requests:
                                        raise KitError('Request id already used; inspect the earlier result, do not replay the action')
                                    if len(seen_requests) >= 10000 and message['op'] != 'close':
                                        raise KitError('Session request limit reached; explicitly close and start a new session')
                                    seen_requests.add(message['id'])
                                    if message.get('op') == 'close':
                                        closing = True
                                        response['status'] = 'closed'
                                    elif message.get('op') in ('inspect', 'reference', 'compare'):
                                        if visual is None:
                                            from .native_visual import NativeVisual
                                            visual = NativeVisual(ws, output, engine, None, timeout_ms=settings['timeout_ms'])
                                        response.update(visual.dispatch(message, oid_image))
                                    elif message.get('op') == 'observe':
                                        if visual is None:
                                            from .native_visual import NativeVisual
                                            visual = NativeVisual(ws, output, engine, None, timeout_ms=settings['timeout_ms'])
                                        response.update(visual.observe(message, oid_image))
                                    elif message.get('op') in ('navigate', 'reload'):
                                        response['data'] = engine.call('navigate', url=message['url'])['data'] if message['op'] == 'navigate' else engine.call('reload')['data']
                                    else:
                                        command = message.get('command', [])
                                        with ExitStack() as command_stack:
                                            active = engine
                                            if message.get('page') == 'reference':
                                                if visual is None:
                                                    raise KitError('This session has no reference source')
                                                active = command_stack.enter_context(visual.reference_page(keep=True))
                                            if command and command[0] == 'screenshot':
                                                from .native_transport import translate
                                                picture = output / (oid_image + '-actual.png')
                                                parsed = translate(command + [str(picture)])
                                                response['data'] = active.call(**parsed).get('data')
                                                response['images'] = [str(picture)]
                                            else:
                                                response['data'] = active.command(command).get('data')
                                except Exception as error:
                                    if (getattr(error, 'details', {}).get('outcome_unknown')
                                            or getattr(error, 'details', {}).get('cleanup_unconfirmed') or 'outcome_unknown' in str(error)):
                                        Journal().update(oid, requires_resolution=True)
                                        record['cleanup_unconfirmed'] = True
                                    response.update(status='blocked', error=str(error) if isinstance(error, KitError) else 'Native request failed', images=[])
                                response['elapsed_ms'] = round((time.monotonic() - began) * 1000, 2)
                                encoded = json.dumps(response, ensure_ascii=False, allow_nan=False).encode()
                                if len(encoded) > MAX_BYTES:
                                    response = {'id': message.get('id'), 'status': 'blocked', 'images': [], 'error': 'Response too large; narrow the request'}
                                    encoded = json.dumps(response).encode()
                                save(output / 'latest.json', receipt(response, message.get('op')))
                                try:
                                    connection.send_bytes(encoded)
                                except (EOFError, OSError):
                                    Journal().update(oid, requires_resolution=True)
                                if closing:
                                    break
                            finally:
                                connection.close()
                    finally:
                        listener.close()
                        if visual and hasattr(visual, 'close'):
                            visual.close()
                confirmed = not record.get('cleanup_unconfirmed', False)
    except BaseException as error:
        save(directory / 'ready.json', {'status': 'failed', 'error': str(error) if isinstance(error, KitError) else 'Native coordinator failed'})
        raise
    finally:
        record['status'] = 'closed' if confirmed else 'unconfirmed'
        save(public_file, record)
        if output:
            save(output / 'session.json', record)
            from .storage_metadata import own
            own(ws, output, 'observations', record.get('operation_id'), {**record, 'finished': time.time()})
        (directory / 'connection.json').unlink(missing_ok=True)


def wait_exit(record, timeout=15):
    identities = [record['identity'], *record.get('children', [])]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and any(matches(value) for value in identities):
        time.sleep(.05)
    if any(matches(value) for value in identities):
        raise KitError('Native process exit unconfirmed; inspect operation before releasing resources')


def manage(ws, action, *, sid=None, **options):
    sid = sid or session_key(ws)
    if action == 'up':
        return start(ws, session_id=sid, **options)
    if action == 'status':
        values = [inspect_record(value) for value in records(ws)]
        selected = next((v for v in values if v.get('session_id') == sid), None)
        if selected:
            validate_scope(ws, selected)
        return {**(selected or {}), 'backend': 'agent-browser', 'status': selected['state'] if selected else 'stopped',
                'session_id': sid, 'sessions': values}
    if action == 'down':
        record = _record(ws, sid)
        if record:
            validate_scope(ws, record)
        if not record or inspect_record(record)['state'] == 'stopped':
            return {'status': 'stopped', 'session_id': sid}
        result = request(ws, sid, {'op': 'close'})
        wait_exit(record)
        return result
    raise KitError('Unknown native browser action')


if __name__ == '__main__':
    worker(sys.argv[2])
