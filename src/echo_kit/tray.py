"""Optional Windows viewing UI. No business commands or resource mutations."""
import ctypes
import functools
import hashlib
import json
import os
import subprocess
import sys
import threading
import time
import traceback
import uuid
import webbrowser
from contextvars import ContextVar
from importlib.resources import files
from pathlib import Path

import psutil
from filelock import FileLock, Timeout

from . import __version__
from .core import KitError, read, save, safe_name
from .operations import data_root
from .processes import fingerprint, matches

PROTOCOL = 1
POLL_SECONDS = 5
IDLE_SECONDS = 30
_scope = ContextVar('tray_start_scope', default=None)


def session_id():
    if sys.platform != 'win32':
        return 'unsupported'
    value = ctypes.c_ulong()
    if not ctypes.windll.kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(value)):
        raise OSError('Cannot identify Windows session')
    return str(value.value)


def root():
    return data_root() / 'tray' / ('session-' + session_id())


def interactive():
    if sys.platform != 'win32' or session_id() == '0':
        return False
    # Do not use isatty: AI tools normally launch CLI commands with pipes.
    user = ctypes.windll.user32
    user.OpenInputDesktop.restype = ctypes.c_void_p
    desktop = user.OpenInputDesktop(0, False, 0x0001)
    if not desktop:
        return False
    user.CloseDesktop.argtypes = [ctypes.c_void_p]
    user.CloseDesktop(desktop)
    return True


def enabled():
    return (os.environ.get('ECHO_KIT_TRAY', '1').lower() not in ('0', 'false', 'no')
            and os.environ.get('CI', '').lower() not in ('1', 'true', 'yes') and interactive())


def register(ws):
    directory = root()
    directory.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256((os.path.normcase(str(ws.root)) + ':' + ws.environment).encode()).hexdigest()
    entry = {'workspace': str(ws.root), 'environment': ws.environment,
             'name': str(ws.cfg.get('name', ws.root.name)), 'owner': ws.identity,
             'services_file': str(ws.state / ('services-' + safe_name(ws.environment) + '.json')),
             'browser_file': str(ws.state / 'browser.json'),
             'services': [n for n, s in ws.cfg.get('services', {}).items() if s.get('mode', 'managed') == 'managed'],
             'browser_managed': not bool(ws.cfg.get('browser', {}).get('endpoint'))}
    with FileLock(str(directory / 'index.lock'), timeout=2):
        index = read(directory / 'projects.json', {})
        index[key] = entry
        save(directory / 'projects.json', index)
    return key


def process_state(identity):
    try:
        current = fingerprint(identity['pid'])
        return 'running' if (current['exe'] == identity['exe'] and current['command'] == identity['command']
                             and abs(current['created'] - identity['created']) < .01
                             and psutil.Process(identity['pid']).status() != psutil.STATUS_ZOMBIE) else 'stopped'
    except psutil.NoSuchProcess:
        return 'stopped'
    except (psutil.Error, KeyError, TypeError):
        return 'unknown'


def container_states():
    result = subprocess.run(['docker', 'ps', '-a', '--no-trunc', '--format', '{{json .}}'],
                            capture_output=True, text=True, encoding='utf-8', timeout=3,
                            **({'creationflags': subprocess.CREATE_NO_WINDOW} if sys.platform == 'win32' else {}))
    if result.returncode:
        raise OSError('Docker state unavailable')
    return {item['ID']: item for item in (json.loads(line) for line in result.stdout.splitlines() if line)}


def safe_viewer(url):
    from urllib.parse import urlsplit
    try:
        parsed = urlsplit(url or '')
        return (parsed.scheme in ('http', 'https') and parsed.hostname in ('127.0.0.1', 'localhost', '::1')
                and not parsed.username and not parsed.password and bool(parsed.port))
    except ValueError:
        return False


def snapshot():
    index = read(root() / 'projects.json', {})
    rows, containers = [], None
    docker_failed = False
    for key, entry in index.items():
        row = {'key': key, 'name': entry['name'], 'environment': entry['environment'],
               'workspace': entry['workspace'], 'services': [], 'browser': None}
        try:
            states = read(entry['services_file'], {})
            for name in entry['services']:
                if name in states:
                    row['services'].append({'name': name, 'state': process_state(states[name]['identity'])})
        except (OSError, ValueError, TypeError, KeyError):
            row['services'].append({'name': '登记文件', 'state': 'unknown'})
        if entry['browser_managed']:
            try:
                state = read(entry['browser_file'], {})
                if state:
                    if containers is None and not docker_failed:
                        try:
                            containers = container_states()
                        except (OSError, ValueError, KeyError, subprocess.TimeoutExpired):
                            docker_failed = True
                    container = (containers or {}).get(state['id'])
                    labels = dict(part.split('=', 1) for part in container.get('Labels', '').split(',') if '=' in part) if container else {}
                    owned = bool(container and labels.get('echo-kit.workspace') == entry['owner'])
                    actual = ('unknown' if docker_failed else 'not_owned' if container and not owned else
                              'running' if owned and container['State'] in ('running', 'restarting', 'paused') else 'stopped')
                    from .browser import RUNTIME_VERSION
                    legacy = state.get('runtime_version') != RUNTIME_VERSION
                    url = state.get('viewer_url')
                    row['browser'] = {'state': actual, 'id': state['id'], 'headless': state.get('headless', True),
                                      'legacy': legacy, 'viewer_url': url if actual == 'running' and not legacy
                                      and not state.get('headless', True) and safe_viewer(url) else None}
            except (OSError, ValueError, KeyError, TypeError):
                row['browser'] = {'state': 'unknown', 'viewer_url': None}
        rows.append(row)
    return rows


def has_resources(rows, include_unknown=False):
    accepted = ('running', 'unknown') if include_unknown else ('running',)
    return any(any(s['state'] in accepted for s in r['services']) or
               (r['browser'] and r['browser']['state'] in accepted) for r in rows)


def instance():
    value = read(root() / 'instance.json', {})
    return value if value and matches(value.get('identity', {})) else None


def stop():
    directory = root()
    directory.mkdir(parents=True, exist_ok=True)
    with FileLock(str(directory / 'start.lock'), timeout=5):
        current = instance()
        save(directory / 'stop.json', {'at': time.time(), 'token': current['token'] if current else None})
    return {'status': 'passed', 'tray': 'stopping' if current else 'stopped', 'business_resources_changed': False}


def launch(token):
    directory = root()
    # A Windows venv python.exe is a redirector: DETACHED_PROCESS on that
    # redirector does not prevent its console Python child allocating a window.
    # Keep the same environment but use its GUI redirector for the entire chain.
    executable = Path(sys.executable).with_name('pythonw.exe')
    if not executable.is_file():
        raise KitError('Windowless tray requires pythonw.exe in the current Python environment')
    env = dict(os.environ)
    for key in ('ECHO_KIT_EXPECTED_VERSION', 'ECHO_KIT_FORWARDED_VERSION', 'ECHO_KIT_SOURCE_VERSION', 'PYTHONPATH'):
        env.pop(key, None)
    # Breakaway may be denied by a restrictive Job. Never silently fall back to an attached process.
    with (directory / 'tray.log').open('ab') as log:
        try:
            return subprocess.Popen([str(executable), '-I', '-m', 'echo_kit.tray', token], env=env,
                                    cwd=directory, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                    creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
                                    | subprocess.CREATE_BREAKAWAY_FROM_JOB, close_fds=True)
        except OSError as error:
            log.write(traceback.format_exc().encode('utf-8'))
            raise KitError('Independent tray launch failed; see ' + str(directory / 'tray.log')) from error


def start(ws, started=None):
    if sys.platform != 'win32':
        return {'status': 'unverified', 'reason': 'unsupported_platform', 'tray': 'unsupported'}
    if not enabled():
        return {'status': 'passed', 'tray': 'disabled', 'reason': 'No interactive desktop, CI or ECHO_KIT_TRAY=0'}
    started = started if started is not None else time.time()
    register(ws)
    directory = root()
    with FileLock(str(directory / 'start.lock'), timeout=5):
        if read(directory / 'stop.json', {}).get('at', 0) >= started:
            return {'status': 'passed', 'tray': 'dismissed'}
        current = instance()
        stopped = read(directory / 'stop.json', {})
        if current and stopped.get('token') == current['token']:
            deadline = time.monotonic() + 5
            while current and time.monotonic() < deadline:
                time.sleep(.05)
                current = instance()
            if current:
                raise KitError('Previous tray is still exiting; retry tray start shortly')
        if current:
            if current.get('protocol') != PROTOCOL:
                return {'status': 'unverified', 'tray': 'incompatible', 'next_step': 'Run tray stop, then tray start.'}
            return {'status': 'passed', 'tray': 'running', 'version': current['version']}
        if not has_resources(snapshot()):
            return {'status': 'passed', 'tray': 'idle'}
        with FileLock(str(directory / 'daemon.lock'), timeout=5):
            pass  # Wait for an exiting UI to release its lifetime lock.
        token = uuid.uuid4().hex
        process = launch(token)
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            current = instance()
            if current and current['token'] == token:
                return {'status': 'passed', 'tray': 'running', 'version': current['version']}
            if process.poll() is not None:
                break
            time.sleep(.05)
        # Cancel only this pending UI process through its own protocol, never process-tree killing.
        save(directory / 'stop.json', {'at': time.time(), 'token': token})
        raise KitError('Tray did not become ready; see ' + str(directory / 'tray.log'))


def status():
    if sys.platform != 'win32':
        return {'status': 'unverified', 'reason': 'unsupported_platform', 'tray': 'unsupported'}
    current = instance()
    return {'status': 'passed', 'tray': 'running' if current else 'stopped',
            'version': current.get('version') if current else None,
            'log': str(root() / 'tray.log'), 'projects': snapshot()}


def notify(ws):
    scope = _scope.get()
    if scope is None or scope.get('unavailable'):
        return
    try:
        if not enabled():
            return
        result = start(ws, scope['started'])
        if result['status'] != 'passed':
            raise KitError(str(result))
    except Exception:
        # Optional UI failure must not alter a service/Verify verdict.
        scope['unavailable'] = True
        if not scope.get('warned'):
            scope['warned'] = True
            print('Echo Kit: tray unavailable; debugging continues. Use viewer_url; see local tray.log.', file=sys.stderr)
        try:
            directory = root()
            directory.mkdir(parents=True, exist_ok=True)
            with (directory / 'tray.log').open('a', encoding='utf-8') as log:
                traceback.print_exc(file=log)
        except OSError:
            pass


def startup_scope(function=None, *, verify_only=False):
    if function is None:
        return lambda fn: startup_scope(fn, verify_only=verify_only)
    @functools.wraps(function)
    def wrapped(first, *args, **kwargs):
        if verify_only and kwargs.get('kind', args[1] if len(args) > 1 else 'lab') != 'verify':
            return function(first, *args, **kwargs)
        token = None
        if _scope.get() is None:
            token = _scope.set({'started': time.time()})
        ws = getattr(first, 'ws', first)
        try:
            return function(first, *args, **kwargs)
        finally:
            try:
                notify(ws)
            finally:
                if token is not None:
                    _scope.reset(token)
    return wrapped


def open_viewer(key):
    row = next((r for r in snapshot() if r['key'] == key), None)
    url = row['browser'].get('viewer_url') if row and row['browser'] else None
    if not url:
        raise KitError('No owned running browser viewer is available')
    if not webbrowser.open(url, new=2):
        raise KitError('System browser could not open the viewer')


class IdleTimer:
    def __init__(self):
        self.since = None

    def expired(self, rows, now):
        if has_resources(rows, include_unknown=True):
            self.since = None
        elif self.since is None:
            self.since = now
        return self.since is not None and now - self.since >= IDLE_SECONDS


def serve(token):
    # Import GUI dependencies only in the Windows UI child, never in core/CI imports.
    import pystray
    from PIL import Image
    directory = root()
    with FileLock(str(directory / 'daemon.lock'), timeout=0):
        done, refresh = threading.Event(), threading.Event()
        rows = []
        pending = []
        guard = threading.Lock()
        idle = IdleTimer()
        text = {'running': '进程运行中', 'stopped': '已停止', 'unknown': '状态未知', 'not_owned': '归属不符'}

        def quit_ui(*unused):
            with guard:
                pending.append(None)
            refresh.set()

        def request_open(key):
            def callback(*unused):
                with guard:
                    pending.append(key)
                refresh.set()
            return callback

        def menu_items():
            with guard:
                current = list(rows)
            items = []
            for row in current:
                if not has_resources([row], include_unknown=True):
                    continue
                label = row['name'] + ' [' + row['environment'] + ']'
                if sum(r['name'] == row['name'] and r['environment'] == row['environment'] for r in current) > 1:
                    label += ' — ' + row['workspace']
                children = [pystray.MenuItem(s['name'] + ': ' + text[s['state']], None, enabled=False) for s in row['services']]
                browser = row['browser']
                if browser:
                    label_browser = ('旧容器，无观看入口' if browser.get('legacy') else
                                     '无头模式，无观看入口' if browser.get('headless') else '浏览器')
                    children.append(pystray.MenuItem(label_browser + ': ' + text[browser['state']], None, enabled=False))
                    children.append(pystray.MenuItem('打开调试画面', request_open(row['key']), enabled=bool(browser.get('viewer_url'))))
                items.append(pystray.MenuItem(label, pystray.Menu(*children)))
            if not items:
                items.append(pystray.MenuItem('暂无运行中的托管资源', None, enabled=False))
            items.append(pystray.MenuItem('退出托盘（保留服务）', quit_ui))
            return items

        def worker():
            nonlocal rows
            try:
                while not done.is_set():
                    if read(directory / 'stop.json', {}).get('token') == token:
                        break
                    try:
                        current = snapshot()
                        with guard:
                            rows = current
                            queued, pending[:] = list(pending), []
                        for key in queued:
                            if key is None:
                                stop()
                                return
                            try:
                                open_viewer(key)
                            except Exception as error:
                                icon.notify(str(error), 'Echo Kit')
                        icon.update_menu()
                        if idle.expired(current, time.monotonic()):
                            # Serialize idle exit with start() so resource registration cannot be lost.
                            with FileLock(str(directory / 'start.lock'), timeout=5):
                                if not has_resources(snapshot(), include_unknown=True):
                                    save(directory / 'instance.json', {})
                                    break
                    except Exception:
                        traceback.print_exc()
                        idle.since = None
                    refresh.wait(POLL_SECONDS)
                    refresh.clear()
            finally:
                done.set()
                icon.stop()

        def ready(icon):
            icon.visible = True
            save(directory / 'instance.json', {'protocol': PROTOCOL, 'token': token,
                                               'identity': fingerprint(os.getpid()), 'version': __version__})
            thread.start()

        with files('echo_kit').joinpath('resources', 'tray.png').open('rb') as resource:
            image = Image.open(resource).copy()
        icon = pystray.Icon('echo-kit', image, 'Echo Kit 调试', pystray.Menu(menu_items))
        thread = threading.Thread(target=worker, name='tray-state', daemon=True)
        try:
            icon.run(setup=ready)
        finally:
            done.set()
            refresh.set()
            if thread.is_alive():
                thread.join(timeout=5)
            current = read(directory / 'instance.json', {})
            if current.get('token') == token:
                save(directory / 'instance.json', {})


if __name__ == '__main__':
    if sys.platform != 'win32':
        raise SystemExit('Tray requires Windows')
    try:
        serve(sys.argv[1])
    except Timeout:
        pass  # Another instance owns the desktop session.
