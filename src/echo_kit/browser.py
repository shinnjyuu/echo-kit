import base64
import hashlib
import json
import math
import os
import socket
import subprocess
import tempfile
import time
import urllib.request
import webbrowser
from contextlib import contextmanager
from functools import partial
from importlib.metadata import version
from importlib.resources import files
from pathlib import Path
from urllib.parse import urlsplit

from . import docker_host, launches
from .core import KitError, read, save

PLAYWRIGHT_VERSION = '1.63.0'
IMAGE = 'mcr.microsoft.com/playwright:v1.63.0-noble'
RUNTIME_VERSION = 1


def docker(*args, timeout=90, log_file=None, host=None, ws=None):
    try:
        command, options = docker_host.command(args, host)
        if ws is None:
            p = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', timeout=timeout, **options)
        else:
            p = launches.run_process(ws, command, text=True, encoding='utf-8', timeout=timeout,
                                     show_tray=args[0] == 'build', **options)
    except (OSError, subprocess.TimeoutExpired) as error:
        if log_file:
            output = getattr(error, 'stdout', None) or b''
            errors = getattr(error, 'stderr', None) or b''
            if isinstance(output, bytes):
                output = output.decode('utf-8', errors='replace')
            if isinstance(errors, bytes):
                errors = errors.decode('utf-8', errors='replace')
            Path(log_file).write_text(output + errors + '\n' + str(error), encoding='utf-8')
        raise KitError('Docker unavailable or timed out') from error
    if log_file:
        Path(log_file).write_text(p.stdout + p.stderr, encoding='utf-8')
    if p.returncode:
        raise KitError('Docker command failed: ' + p.stderr.strip()[-2000:])
    return p.stdout.strip()


def settings(ws):
    cfg = ws.cfg.get('browser', {})
    if not isinstance(cfg, dict):
        raise KitError('browser must be a table')
    if cfg.get('endpoint'):
        if cfg.get('viewer_url') and urlsplit(cfg['viewer_url']).scheme not in ('http', 'https'):
            raise KitError('External viewer_url must use HTTP or HTTPS')
        return cfg
    result = {**cfg}
    for name, default in (('port', 19323), ('viewer_port', 19324)):
        value = cfg.get(name, default)
        if type(value) is not int or not 0 < value < 65536:
            raise KitError('browser.' + name + ' must be a valid integer port')
        result[name] = value
    for name, default in (('headless', False), ('auto_open', os.environ.get('CI', '').lower() not in ('1', 'true', 'yes'))):
        value = cfg.get(name, default)
        if type(value) is not bool:
            raise KitError('browser.' + name + ' must be a boolean')
        result[name] = value
    if not result['headless'] and result['port'] == result['viewer_port']:
        raise KitError('Browser control and viewer ports must differ')
    image = cfg.get('image', IMAGE)
    if not isinstance(image, str) or not image.endswith(':v' + PLAYWRIGHT_VERSION + '-noble'):
        raise KitError('Docker image must match pinned Playwright version')
    result['image'] = image
    for name, default in (('timeout', 90), ('build_timeout', 600)):
        value = cfg.get(name, default)
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise KitError('browser.' + name + ' must be positive')
        result[name] = value
    return result


def build_resources():
    root = files('echo_kit').joinpath('resources', 'browser')
    return {name: root.joinpath(name).read_bytes() for name in ('Dockerfile', 'server.cjs')}


def image_key(base):
    digest = hashlib.sha256(base.encode())
    for name, content in sorted(build_resources().items()):
        digest.update(name.encode())
        digest.update(content)
    return digest.hexdigest()[:24]


def prepare_image(cfg, *, ws=None, host=None):
    from filelock import FileLock
    from .operations import data_root
    key = image_key(cfg['image'])
    tag = 'echo-kit-browser:' + key
    directory = data_root() / 'browser-builds'
    directory.mkdir(parents=True, exist_ok=True)
    command = partial(docker, host=host) if host else docker
    with launches.launch_lock(FileLock(str(directory / (key + '.lock')), timeout=cfg['build_timeout'])):
        try:
            command('image', 'inspect', tag)
            return tag
        except KitError:
            pass
        with tempfile.TemporaryDirectory(prefix='echo-kit-browser-') as temp:
            for name, content in build_resources().items():
                (Path(temp) / name).write_bytes(content)
            try:
                command('build', '--build-arg', 'BASE_IMAGE=' + cfg['image'], '-t', tag, temp,
                        timeout=cfg['build_timeout'], log_file=directory / (key + '.log'), ws=ws)
            except launches.EmergencyInterrupted:
                raise
            except KitError as error:
                failure = KitError('Browser image build failed; no container started. ' + str(error))
                failure.details = {'build_log': str(directory / (key + '.log'))}
                raise failure from error
    return tag


def connect_check(endpoint):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.connect(endpoint, timeout=3000)
        browser.close()


def viewer_check(url):
    """Check both the HTML and the actual WebSocket-to-VNC transport."""
    parsed = urlsplit(url)
    with urllib.request.urlopen(url, timeout=3) as response:
        if response.status != 200:
            raise KitError('Viewer HTTP check failed')
    with socket.create_connection((parsed.hostname, parsed.port), timeout=3) as stream:
        key = base64.b64encode(os.urandom(16)).decode()
        request = (f'GET /websockify HTTP/1.1\r\nHost: {parsed.netloc}\r\n'
                   f'Upgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n'
                   'Sec-WebSocket-Version: 13\r\nSec-WebSocket-Protocol: binary\r\n\r\n')
        stream.sendall(request.encode())
        data = b''
        while b'\r\n\r\n' not in data and len(data) < 16384:
            part = stream.recv(4096)
            if not part:
                raise KitError('Viewer disconnected before handshake')
            data += part
        headers, _, payload = data.partition(b'\r\n\r\n')
        if not headers.startswith(b'HTTP/1.1 101'):
            raise KitError('Viewer WebSocket check failed')
        # The first binary RFB frame contains the protocol banner.
        while len(payload) < 14:
            part = stream.recv(4096)
            if not part:
                raise KitError('Viewer VNC connection closed')
            payload += part
        if payload[0] != 0x82 or payload[1] != 12 or not payload[2:].startswith(b'RFB '):
            raise KitError('Viewer VNC protocol check failed')


def assert_ports_free(cfg):
    ports = [cfg['port']] + ([] if cfg['headless'] else [cfg['viewer_port']])
    for port in ports:
        with socket.socket() as stream:
            try:
                stream.settimeout(.3)
                stream.connect(('127.0.0.1', port))
            except OSError:
                continue
            raise KitError(f'Browser port occupied: {port}; choose another port or stop its owner')


def migration_needed(state, cfg):
    return (state.get('runtime_version') != RUNTIME_VERSION
            or state.get('runtime_key') != image_key(cfg['image'])
            or state.get('headless') != cfg['headless']
            or state.get('port') != cfg['port']
            or (not cfg['headless'] and state.get('viewer_port') != cfg['viewer_port']))


def actual_state(state):
    # Old registration files never prove that a visible desktop exists.
    if state.get('runtime_version') != RUNTIME_VERSION:
        return {**state, 'mode': 'managed', 'headless': True, 'viewer_url': None,
                'viewer_read_only': None, 'legacy': True}
    return {**state, 'mode': 'managed'}


def open_viewer_once(cfg, state, file):
    if not cfg['auto_open'] or not state.get('viewer_url') or state.get('viewer_open_attempted'):
        return
    # Save before calling the OS: closing the viewer never triggers another popup.
    state['viewer_open_attempted'] = True
    save(file, state)
    try:
        opened = webbrowser.open(state['viewer_url'], new=2)
        state['viewer_opened'] = bool(opened)
    except Exception:
        state['viewer_opened'] = False
    if not state['viewer_opened']:
        state['viewer_notice'] = 'Could not open the viewer automatically; open viewer_url manually'
    save(file, state)


def describe(result):
    from . import __version__
    external = result.get('mode') == 'external'
    viewer = bool(result.get('viewer_url')) and not external
    return {**result, 'tool_version': __version__, 'required_playwright_version': PLAYWRIGHT_VERSION,
            'capabilities': {'manages_image': not external, 'controls_display_mode': not external,
                             'provides_viewer': viewer,
                             'guarantees_read_only': viewer and result.get('viewer_read_only') is True},
            'next_steps': ([
                'External browser.endpoint is an explicit project exception; Kit does not manage its image or display mode, provide a viewer, or guarantee an external viewer is read-only.',
                'For managed Docker viewing, inspect configuration sources and operations list --unfinished; within user authorization remove the endpoint override and run browser up. Never stop, delete or adopt the external container.',
                'Read echo-kit guide echo-workbench and echo-kit updates notes.'
            ] if external else [
                'Open viewer_url when available; headless and legacy containers have no managed viewer.',
                'For mode/runtime changes, finish active operations before explicitly running browser down then browser up.'
            ])}


LEGACY_FIELDS = ('image', 'port', 'viewer_port', 'endpoint', 'viewer_url', 'auto_open', 'build_timeout', 'timeout')


def configuration_sources(ws):
    configuration = getattr(ws, 'configuration', None)
    if configuration is None:
        return []
    result = []
    for scope, path, table in [('shared', 'echo-kit.toml', configuration.shared),
                               ('private', '.echo-kit/local.toml', configuration.local)]:
        for environment, values in [(None, table), (ws.environment, table.get('environments', {}).get(ws.environment, {}))]:
            browser = values.get('browser', {})
            if isinstance(browser, dict) and browser:
                result.append({'path': path, 'scope': scope, 'environment': environment, 'fields': sorted(browser)})
    return result


def selected_backend(ws, override=None):
    cfg = ws.cfg.get('browser', {})
    selected = override or cfg.get('backend')
    if selected is None and any(k in cfg for k in LEGACY_FIELDS):
        error = KitError('Legacy browser configuration requires an explicit backend: agent-browser for native migration, '
                         'or playwright for compatibility. No browser was started.')
        error.details = {'migration_required': True, 'configuration_sources': configuration_sources(ws),
                         'legacy_fields': [key for key in LEGACY_FIELDS if key in cfg]}
        raise error
    selected = selected or 'agent-browser'
    if selected not in ('agent-browser', 'playwright'):
        raise KitError('browser.backend must be agent-browser or playwright')
    return selected


def manage(ws, action, *, backend=None, **options):
    from . import native_sessions
    configured = ws.cfg.get('browser', {}).get('backend')
    if action == 'up' and backend is None and configured == 'playwright':
        backend = configured
    if action == 'status' and backend is None:
        native = native_sessions.manage(ws, 'status', sid=options.get('sid'))
        if configured == 'playwright' or (ws.state / 'browser.json').exists() or ws.cfg.get('browser', {}).get('endpoint'):
            legacy = _legacy_manage(ws, 'status')
            if native['sessions']:
                return {**native, 'legacy': legacy}
            return {**legacy, 'backend': 'playwright'}
        return native
    if action == 'down' and backend is None:
        if (options.get('sid') or any(native_sessions.inspect_record(r)['state'] != 'stopped'
                                     for r in native_sessions.records(ws))):
            return native_sessions.manage(ws, action, sid=options.pop('sid', None))
        if configured == 'playwright' or (ws.state / 'browser.json').exists() or ws.cfg.get('browser', {}).get('endpoint'):
            return {**_legacy_manage(ws, action), 'backend': 'playwright'}
        return {'status': 'stopped', 'backend': 'agent-browser'}
    selected = selected_backend(ws, backend)
    if selected == 'playwright':
        if action == 'up' and any(native_sessions.inspect_record(r)['state'] != 'stopped' for r in native_sessions.records(ws)):
            raise KitError('Finish and close native sessions before switching this workspace to compatibility mode')
        return {**_legacy_manage(ws, action), 'backend': 'playwright'}
    if action == 'up' and (ws.state / 'browser.json').exists():
        old = _legacy_manage(ws, 'status')
        if old.get('status') not in ('stopped', 'missing'):
            raise KitError('Stop the existing owned compatibility browser explicitly before starting native mode')
    # The explicit selection is propagated to the worker without changing project configuration.
    original = ws.cfg.get('browser', {})
    ws.cfg['browser'] = {**original, 'backend': selected}
    try:
        return native_sessions.manage(ws, action, **options)
    finally:
        ws.cfg['browser'] = original


def _legacy_manage(ws, action):
    previous = ws.cfg.get('browser', {})
    ws.cfg['browser'] = {**previous, 'backend': 'playwright'}
    if action in ('status', 'down') and (ws.state / 'browser.json').exists():
        # A later endpoint override cannot hide an already owned container.
        ws.cfg['browser'].pop('endpoint', None)
    try:
        if action == 'up':
            from filelock import FileLock
            from .native_sessions import records, inspect_record
            ws.state.mkdir(parents=True, exist_ok=True)
            with launches.launch_lock(FileLock(str(ws.state / 'browser-mode.lock'), timeout=600)):
                if any(inspect_record(r)['state'] != 'stopped' for r in records(ws)):
                    raise KitError('Finish and close native sessions before switching this workspace to compatibility mode')
                return _tray_up(ws)
        return _manage_operation(ws, action)
    finally:
        ws.cfg['browser'] = previous


from .tray import startup_scope, notify


@launches.guarded
@startup_scope
def _tray_up(ws):
    return _manage_operation(ws, 'up')


def _manage_operation(ws, action):
    from .operations import operation, browser_resource, browser_resources
    if action == 'status':
        return describe(_manage(ws, action))
    if action == 'up':
        settings(ws)
    key = browser_resource(ws)
    resources = {key: 'exclusive' if action == 'down' else 'shared' for key in browser_resources(ws)}
    if action == 'up':
        resources['browser-start:' + key] = 'exclusive'
        resources['browser-state:' + str(ws.state.resolve())] = 'exclusive'
    with operation(ws, 'browser.' + action, resources):
        return describe(_manage(ws, action))


def start_container(ws, state, command):
    target = {'id': state['id'], 'owner': ws.identity}
    if 'docker_host' in state:
        target['docker_host'] = state['docker_host']
    with launches.pending(ws, 'browser', browser=target) as attempt:
        timed_out = False
        try:
            command('start', state['id'])
        except KitError as error:
            timed_out = isinstance(error.__cause__, subprocess.TimeoutExpired)
            attempt.keep = timed_out
            raise
        finally:
            try:
                launches.checkpoint()
            except launches.EmergencyInterrupted:
                from .emergency import stop_registered_browser
                confirmed = stop_registered_browser(target, ws.identity)
                attempt.keep = timed_out or not confirmed
                raise


def _manage(ws, action):
    cfg = settings(ws) if action == 'up' else ws.cfg.get('browser', {})
    file = ws.state / 'browser.json'
    state = read(file, {})
    if cfg.get('endpoint'):
        result = describe({'mode': 'external', 'endpoint': cfg['endpoint'], 'status': 'available',
                           'headless': None, 'viewer_url': cfg.get('viewer_url'), 'viewer_read_only': None})
        if action != 'down':
            try:
                connect_check(cfg['endpoint'])
            except Exception as cause:
                error = KitError('External browser connection failed: ' + str(cause))
                error.details = {**result, 'status': 'blocked'}
                raise error from cause
        return result
    if not state and action != 'up':
        return {'status': 'stopped', 'viewer_url': None}
    host = state.get('docker_host')
    command = partial(docker, host=host) if docker_host.is_local(host) else docker
    if action == 'up':
        client_version = version('playwright')
        if client_version != PLAYWRIGHT_VERSION:
            error = KitError(f'Playwright client version mismatch: required {PLAYWRIGHT_VERSION}, installed {client_version}')
            error.details = {'required_playwright_version': PLAYWRIGHT_VERSION}
            raise error
    if state:
        actual = actual_state(state)
        try:
            instance = json.loads(command('inspect', state['id']))[0]
        except KitError:
            if action == 'status':
                return {**actual, 'status': 'missing'}
            raise KitError('Saved browser container missing; inspect state before replacement')
        if instance['Config'].get('Labels', {}).get('echo-kit.workspace') != ws.identity:
            raise KitError('Browser ownership mismatch')
        if action == 'down':
            command('rm', '-f', state['id'])
            file.unlink()
            return {'status': 'stopped', 'viewer_url': None}
        if action == 'status':
            actual['status'] = instance['State']['Status']
            try:
                actual['migration_required'] = migration_needed(state, settings(ws))
            except KitError as error:
                actual['configuration_error'] = str(error)
            return actual
        if migration_needed(state, cfg):
            error = KitError('Browser configuration/runtime changed; after active operations finish, run browser down then browser up')
            error.details = {'browser': describe(actual), 'migration_required': True}
            raise error
        if not instance['State']['Running']:
            start_container(ws, state, command)
    else:
        assert_ports_free(cfg)
        try:
            host = docker_host.selected_host()
        except (KitError, OSError, subprocess.TimeoutExpired):
            host = None
        host = host if docker_host.is_local(host) else None
        command = partial(docker, host=host) if host else docker
        image = prepare_image(cfg, ws=ws, host=host)
        launches.checkpoint()
        args = ['create', '--init', '--shm-size=512m', '--label', 'echo-kit.workspace=' + ws.identity,
                '--label', 'echo-kit.browser-runtime=' + str(RUNTIME_VERSION),
                '-p', f"127.0.0.1:{cfg['port']}:3000", '--add-host', 'host.docker.internal:host-gateway',
                '-e', 'ECHO_BROWSER_HEADLESS=' + str(cfg['headless']).lower()]
        if not cfg['headless']:
            args += ['-p', f"127.0.0.1:{cfg['viewer_port']}:6080"]
        cid = command(*args, image, ws=ws)
        state = {'id': cid, 'endpoint': f"ws://127.0.0.1:{cfg['port']}/", 'image': image,
                 'docker_host': host or '',
                 'base_image': cfg['image'], 'runtime_version': RUNTIME_VERSION,
                 'runtime_key': image_key(cfg['image']), 'headless': cfg['headless'], 'port': cfg['port'],
                 'viewer_port': None if cfg['headless'] else cfg['viewer_port'],
                 'viewer_url': None if cfg['headless'] else f"http://127.0.0.1:{cfg['viewer_port']}/vnc.html?autoconnect=true&resize=scale&view_only=true",
                 'viewer_read_only': None if cfg['headless'] else True}
        try:
            save(file, state)
        except BaseException:
            command('rm', '-f', cid)
            raise
        start_container(ws, state, command)
    notify(ws)
    deadline = time.monotonic() + cfg['timeout']
    reason = ''
    while time.monotonic() < deadline:
        launches.checkpoint()
        try:
            connect_check(state['endpoint'])
            if state['viewer_url']:
                viewer_check(state['viewer_url'])
            launches.checkpoint()
            open_viewer_once(cfg, state, file)
            return {**actual_state(state), 'status': 'ready'}
        except launches.EmergencyInterrupted:
            raise
        except Exception as error:
            reason = str(error)
            time.sleep(.5)
    error = KitError('Browser readiness timed out; container retained for diagnosis: ' + reason)
    error.details = {'browser': describe(actual_state(state)), 'logs_command': 'docker logs ' + state['id']}
    raise error


@contextmanager
def session(endpoint, output, storage_state=None):
    """Project adapter helper. Traces can contain business data; share deliberately."""
    from pathlib import Path
    from playwright.sync_api import sync_playwright
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.connect(endpoint)
        context = browser.new_context(storage_state=storage_state)
        context.tracing.start(screenshots=True, snapshots=True, sources=False)
        try:
            yield context
        finally:
            try:
                context.tracing.stop(path=str(output / 'trace.zip'))
            finally:
                context.close()
                browser.close()
