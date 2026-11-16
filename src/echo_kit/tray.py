"""Optional Windows viewing and emergency stop UI."""
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

PROTOCOL = 4
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
    from . import workspace_index
    workspace_index.remember(ws)
    directory = root()
    directory.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256((os.path.normcase(str(ws.root)) + ':' + ws.environment).encode()).hexdigest()
    entry = workspace_index.entry(ws)
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


def snapshot(resource_sink=None):
    index = read(root() / 'projects.json', {})
    rows, containers = [], None
    docker_failed = False
    seen_browsers = set()
    seen_native = set()
    for key, entry in index.items():
        row = {'key': key, 'name': entry['name'], 'environment': entry['environment'],
               'workspace': entry['workspace'], 'services': [], 'browser': None}
        try:
            states = read(entry['services_file'], {})
            for name in entry['services']:
                if name in states:
                    if 'registry_id' in states[name]:
                        continue
                    row['services'].append({'name': name, 'state': process_state(states[name]['identity'])})
        except (OSError, ValueError, TypeError, KeyError):
            row['services'].append({'name': '登记文件', 'state': 'unknown'})
        if entry['browser_file'] not in seen_browsers and (entry['browser_managed'] or Path(entry['browser_file']).exists()):
            seen_browsers.add(entry['browser_file'])
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
        from . import workspace_index
        try:
            hint = workspace_index.validate(entry)
            if hint['native_browsers_dir'] in seen_native:
                continue
            seen_native.add(hint['native_browsers_dir'])
            native, problems = workspace_index.native_records(hint)
            for record in native:
                sid = record['session_id']
                rows.append({'key': 'native:' + hint['owner'] + ':' + sid,
                             'name': entry['name'] + ' · ' + (record.get('actor') or sid),
                             'environment': record['environment'], 'workspace': record['workspace'], 'services': [],
                             'browser': {'backend': 'agent-browser', 'state': workspace_index.native_state(record),
                                         'id': record['instance_id'], 'session_id': sid, 'headless': record['headless'],
                                         'viewer_url': None}})
            if problems:
                rows.append({'key': 'native-unknown:' + hint['owner'], 'name': entry['name'] + ' · 原生浏览器登记',
                             'environment': entry['environment'], 'workspace': entry['workspace'], 'services': [],
                             'browser': {'backend': 'agent-browser', 'state': 'unknown', 'headless': True, 'viewer_url': None}})
        except (KitError, OSError, ValueError, TypeError, KeyError):
            rows.append({'key': 'native-unknown:' + key, 'name': entry['name'] + ' · 原生浏览器登记',
                         'environment': entry['environment'], 'workspace': entry['workspace'], 'services': [],
                         'browser': {'backend': 'agent-browser', 'state': 'unknown', 'headless': True, 'viewer_url': None}})
    from .service_registry import Registry, process_state as shared_state
    from .operations import Journal
    shared = []
    operations = Journal().list(unfinished=True)
    if resource_sink is None:
        records = Registry().all()
    else:
        from .resource_usage import registry_records
        records, registration_problems = registry_records()
    for record in records:
        state = shared_state(record['identity'])
        shared.append({'name': record.get('resource_id') or record['origin']['name'],
                       'instance_id': record['instance_id'], 'state': state if state in ('running', 'stopped') else 'unknown',
                       'references': record['references'],
                       'users': [{'workspace': op['workspace'], 'environment': op['environment'], 'operation_id': op['id']}
                                 for op in operations if op['run_id'] and set(op['held']).intersection(record['resource_keys'])]})
    if shared:
        rows.append({'key': 'shared-services', 'kind': 'shared_services', 'name': '共享微服务',
                     'environment': '', 'workspace': '', 'services': shared, 'browser': None})
    tasks = [{'name': Path(op['workspace']).name + ' · ' + op['action'], 'state': 'running'}
             for op in operations if op['children_alive']]
    if tasks:
        rows.append({'key': 'local-tasks', 'kind': 'tasks', 'name': '本地任务',
                     'environment': '', 'workspace': '', 'services': tasks, 'browser': None})
    if resource_sink is not None:
        resource_sink(operations, records if not registration_problems else None)
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


def notify(ws, started=None):
    from .workspace_index import remember
    remember(ws)
    scope = _scope.get()
    if scope is None and started is not None:
        # Detached native workers do not inherit the caller's ContextVar. Keep
        # its original operation time so a user's dismissal still wins here.
        scope = {'started': started}
    if scope is None or scope.get('unavailable'):
        return
    from .launches import EmergencyInterrupted, checkpoint
    try:
        checkpoint()
        if not enabled():
            return
        result = start(ws, scope['started'])
        if result['status'] != 'passed':
            raise KitError(str(result))
    except EmergencyInterrupted:
        return
    except Exception:
        # Optional UI failure must not alter a service/Verify verdict.
        scope['unavailable'] = True
        if not scope.get('warned'):
            scope['warned'] = True
            print('Echo Kit: tray unavailable; debugging continues. Use browser status for the owned session or viewer; see local tray.log.', file=sys.stderr)
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
    if row and row['browser'] and row['browser'].get('backend') == 'agent-browser':
        from . import workspace_index
        hint = {'workspace': row['workspace'], 'environment': row['environment']}
        records, _ = workspace_index.native_records(hint)
        record = next((r for r in records if r['session_id'] == row['browser'].get('session_id')), None)
        if not record or workspace_index.native_state(record) != 'running' or record.get('headless'):
            raise KitError('No verified owned native browser window is available')
        return focus_native_window(record)
    url = row['browser'].get('viewer_url') if row and row['browser'] else None
    if not url:
        raise KitError('No owned running browser viewer is available')
    if not webbrowser.open(url, new=2):
        raise KitError('System browser could not open the viewer')


def focus_native_window(record):
    """Foreground an existing owned Chrome window; never navigate or recreate it."""
    if sys.platform != 'win32':
        raise KitError('Native window activation is supported only on Windows')
    pids = {identity['pid'] for identity in record.get('children', [])
            if Path(identity.get('exe', '')).name.lower() in ('chrome.exe', 'chromium.exe') and matches(identity)}
    if not pids:
        raise KitError('Native browser process identity could not be confirmed')
    user = ctypes.windll.user32
    windows = []
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_ssize_t)
    user.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    user.IsWindowVisible.argtypes = [ctypes.c_void_p]
    user.EnumWindows.argtypes = [callback_type, ctypes.c_ssize_t]
    user.IsIconic.argtypes = [ctypes.c_void_p]
    user.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
    user.SetForegroundWindow.argtypes = [ctypes.c_void_p]
    def candidate(hwnd, _):
        pid = ctypes.c_ulong()
        user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids and user.IsWindowVisible(hwnd):
            windows.append((hwnd, pid.value))
        return True
    callback = callback_type(candidate)
    user.EnumWindows(callback, 0)
    if not windows:
        raise KitError('No visible window belongs to the verified native browser; it may have been closed')
    hwnd, pid = windows[0]
    # Check ownership a second time immediately before activating the selected handle.
    if not any(i['pid'] == pid and matches(i) for i in record.get('children', [])):
        raise KitError('Native browser identity changed before window activation')
    if user.IsIconic(ctypes.c_void_p(hwnd)):
        user.ShowWindow(ctypes.c_void_p(hwnd), 9)
    if not user.SetForegroundWindow(ctypes.c_void_p(hwnd)):
        raise KitError('Windows did not allow bringing this native browser to the foreground')


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
    from .resource_usage import BackgroundUsage, format_memory, summary_text
    directory = root()
    with FileLock(str(directory / 'daemon.lock'), timeout=0):
        done, refresh = threading.Event(), threading.Event()
        urgent, stopping = threading.Event(), threading.Event()
        rows = []
        pending = []
        guard = threading.Lock()
        idle = IdleTimer()
        usage = BackgroundUsage()
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

        def request_emergency(*unused):
            with guard:
                if not done.is_set() and not stopping.is_set():
                    stopping.set()
                    urgent.set()

        def emergency_worker():
            from .emergency import stop_all
            while True:
                urgent.wait()
                urgent.clear()
                if done.is_set():
                    return
                try:
                    result = stop_all()
                    counts = result.get('counts')
                    if counts is None:
                        message = '另一处急停正在执行。'
                    else:
                        message = (f"已停止 {counts['stopped']}，原已退出 {counts['already_exited']}，"
                                   f"未确认 {counts['unconfirmed']}。")
                        if result['pending_operations']:
                            message += f"有 {len(result['pending_operations'])} 条操作待处理。"
                    icon.notify(message, 'Echo Kit 急停')
                except Exception:
                    icon.notify('急停结果未确认。可用命令查看各目标结果。', 'Echo Kit 急停')
                finally:
                    stopping.clear()
                    refresh.set()

        def menu_items():
            with guard:
                current = list(rows)
            sampled = usage.snapshot()
            summaries = [pystray.MenuItem(line, None, enabled=False) for line in summary_text(sampled)]
            if sampled:
                leaders = [i for i in sampled['items'] if i['kind'] == 'service' and i['memory_bytes'] is not None
                           and i['state'] == 'running'][:3]
                summaries += [pystray.MenuItem(i['name'] + ': ' + format_memory(i['memory_bytes']), None, enabled=False)
                              for i in leaders]
            items = [pystray.MenuItem('资源用量', pystray.Menu(*summaries)), pystray.Menu.SEPARATOR]

            def measured(kind, instance_id=None, workspace=None, environment=None, name=None):
                if not sampled:
                    return []
                found = next((i for i in sampled['items'] if i['kind'] == kind and
                              ((instance_id is not None and i['instance_id'] == instance_id) or
                               (workspace is not None and any(r['workspace'] == workspace and
                                (environment is None or r['environment'] == environment) and
                                (name is None or r['name'] == name) for r in i['references'])))), None)
                if not found:
                    return []
                stale = '（旧数据）' if time.time() - sampled['sampled_at'] > 15 else ''
                cpu = '未知' if found['cpu_percent'] is None else f"{found['cpu_percent']:.1f}%"
                count = found['container_pids'] if kind == 'browser' else found['process_count']
                label = '容器 PIDS' if kind == 'browser' else '进程数'
                return [pystray.MenuItem('内存：' + format_memory(found['memory_bytes']) + stale, None, enabled=False),
                        pystray.MenuItem('CPU：' + cpu + stale, None, enabled=False),
                        pystray.MenuItem(label + '：' + (str(count) if count is not None else '未知'), None, enabled=False)]
            for row in current:
                if not has_resources([row], include_unknown=True):
                    continue
                if row.get('kind') == 'shared_services':
                    services = []
                    for service in row['services']:
                        if service['state'] == 'stopped':
                            continue
                        details = [pystray.MenuItem('引用：' + ref['workspace'] + ' [' + ref['environment'] + ']', None, enabled=False)
                                   for ref in service['references']]
                        details += measured('service', instance_id=service['instance_id'])
                        details += [pystray.MenuItem('使用中：' + user['workspace'] + ' [' + user['environment'] + ']', None, enabled=False)
                                    for user in service['users']]
                        if not service['users']:
                            details.append(pystray.MenuItem('当前无 Kit 实验占用', None, enabled=False))
                        services.append(pystray.MenuItem(service['name'] + ': ' + text[service['state']], pystray.Menu(*details)))
                    items.append(pystray.MenuItem('共享微服务', pystray.Menu(*services)))
                    continue
                if row.get('kind') == 'tasks':
                    tasks = [pystray.MenuItem(task['name'], None, enabled=False) for task in row['services']]
                    items.append(pystray.MenuItem(row['name'], pystray.Menu(*tasks)))
                    continue
                label = row['name'] + ' [' + row['environment'] + ']'
                if sum(r['name'] == row['name'] and r['environment'] == row['environment'] for r in current) > 1:
                    label += ' — ' + row['workspace']
                children = [pystray.MenuItem(s['name'] + ': ' + text[s['state']],
                            pystray.Menu(*measured('service', workspace=row['workspace'], environment=row['environment'], name=s['name']))
                            if sampled else None) for s in row['services']]
                browser = row['browser']
                if browser:
                    native = browser.get('backend') == 'agent-browser'
                    label_browser = ('原生无头浏览器' if native and browser.get('headless') else '原生浏览器' if native else
                                     '旧容器，无观看入口' if browser.get('legacy') else
                                     '无头模式，无观看入口' if browser.get('headless') else '浏览器')
                    children.append(pystray.MenuItem(label_browser + ': ' + text[browser['state']], None, enabled=False))
                    children += measured('native_browser' if native else 'browser', instance_id=browser.get('id'))
                    children.append(pystray.MenuItem('定位浏览器窗口' if native else '打开调试画面', request_open(row['key']),
                                    enabled=browser['state'] == 'running' and not browser.get('headless') if native else bool(browser.get('viewer_url'))))
                items.append(pystray.MenuItem(label, pystray.Menu(*children)))
            if len(items) == 2:
                items.append(pystray.MenuItem('暂无运行中的托管资源', None, enabled=False))
            items.append(pystray.Menu.SEPARATOR)
            items.append(pystray.MenuItem('急停所有工作区', request_emergency, enabled=not stopping.is_set()))
            items.append(pystray.MenuItem('退出托盘（保留服务）', quit_ui))
            return items

        def worker():
            nonlocal rows
            try:
                while not done.is_set():
                    if read(directory / 'stop.json', {}).get('token') == token:
                        break
                    try:
                        current = snapshot(resource_sink=usage.request)
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
                        sampled = usage.snapshot()
                        if sampled:
                            native = sampled['summary']['native']
                            containers = sampled['summary']['containers']
                            browsers = sampled['summary'].get('native_browsers', {'memory_bytes': 0, 'unknown_count': 0})
                            def amount(part):
                                return format_memory(part['memory_bytes']) if not part['unknown_count'] else '部分未知'
                            stale = '（旧）' if time.time() - sampled['sampled_at'] > 15 else ''
                            icon.title = f"Echo Kit{stale} · 服务 {amount(native)} · 浏览器 {amount(browsers)} · 容器 {amount(containers)}"
                        icon.update_menu()
                        if not stopping.is_set() and idle.expired(current, time.monotonic()):
                            # Serialize idle exit with start() so resource registration cannot be lost.
                            with FileLock(str(directory / 'start.lock'), timeout=5):
                                if not has_resources(snapshot(), include_unknown=True):
                                    with guard:
                                        if not stopping.is_set():
                                            done.set()
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
            urgent_thread.start()
            usage.start()
            thread.start()

        with files('echo_kit').joinpath('resources', 'tray.png').open('rb') as resource:
            image = Image.open(resource).copy()
        icon = pystray.Icon('echo-kit', image, 'Echo Kit 调试', pystray.Menu(menu_items))
        thread = threading.Thread(target=worker, name='tray-state', daemon=True)
        urgent_thread = threading.Thread(target=emergency_worker, name='tray-emergency', daemon=True)
        try:
            icon.run(setup=ready)
        finally:
            done.set()
            refresh.set()
            urgent.set()
            usage.close()
            if urgent_thread.is_alive():
                urgent_thread.join(timeout=5)
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
