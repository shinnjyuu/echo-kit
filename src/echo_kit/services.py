import socket
import subprocess
import time
import urllib.request
import hashlib
from contextlib import nullcontext, contextmanager
from pathlib import Path

from .core import KitError, read, save, safe_name, versions
from .processes import fingerprint, matches, spawn, stop


@contextmanager
def phase(timings, name, service):
    from .operations import activity
    started = time.monotonic()
    activity(name + ':' + service, 'started')
    outcome = 'completed'
    try:
        yield
    except BaseException:
        outcome = 'failed'
        raise
    finally:
        timings[name] = time.monotonic() - started
        activity(name + ':' + service, outcome, duration=timings[name])


def source_snapshot(path):
    """Content fingerprint, including dirty and untracked source, not just dirty=True."""
    path = Path(path)
    p = subprocess.run(['git', '-C', str(path), 'ls-files', '-co', '--exclude-standard', '-z'],
                       capture_output=True, timeout=10)
    if p.returncode:
        raise KitError('Build version checking requires a Git project')
    digest = hashlib.sha256()
    for name in sorted(set(p.stdout.decode('utf-8').split('\0')) - {''}):
        file = path / name
        digest.update(name.encode())
        if file.is_file():
            digest.update(file.read_bytes())
        else:
            digest.update(b'<missing>')
    commit = subprocess.run(['git', '-C', str(path), 'rev-parse', 'HEAD'], capture_output=True, text=True, timeout=5)
    return {'commit': commit.stdout.strip() if commit.returncode == 0 else None, 'sha256': digest.hexdigest()}


def artifact_snapshot(ws, spec):
    result = []
    for value in spec.get('artifacts', []):
        file = (ws.project(spec.get('project')) / value).resolve()
        if not file.is_file():
            raise KitError('Declared build artifact missing: ' + str(file))
        with file.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        result.append({'path': str(file), 'sha256': digest})
    return result


def healthy(ws, spec):
    check = spec.get('ready', {})
    try:
        if 'url' in check:
            with urllib.request.urlopen(check['url'], timeout=2) as r:
                return r.status == check.get('status', 200)
        if 'port' in check:
            with socket.create_connection((check.get('host', '127.0.0.1'), check['port']), timeout=1):
                return True
        if 'command' in check:
            p = subprocess.run(ws.command(check), cwd=ws.project(spec.get('project')), env=ws.env(check),
                               capture_output=True, timeout=check.get('timeout', 3))
            return p.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False
    raise KitError('Service requires an explicit ready check')


class Services:
    def __init__(self, ws):
        self.ws = ws
        self.file = ws.state / ('services-' + safe_name(ws.environment) + '.json')

    def _save_item(self, name, value):
        from filelock import FileLock
        self.file.parent.mkdir(parents=True, exist_ok=True)
        with FileLock(str(self.file) + '.lock', timeout=5):
            state = read(self.file, {})
            if value is None:
                state.pop(name, None)
            else:
                state[name] = value
            save(self.file, state)

    def status(self):
        state = read(self.file, {})
        from .operations import Journal, service_resources
        operations = Journal().list()
        return {name: {'mode': spec.get('mode', 'managed'),
                       'owned': name in state and matches(state[name]['identity']),
                       'ready': healthy(self.ws, spec),
                       'instance': state.get(name),
                       'source_changed_since_start': self._source_changed(spec, state.get(name)),
                       'occupants': [op for op in operations if set(op['held']).intersection(
                           service_resources(self.ws, [name], builds=False))]}
                for name, spec in self.ws.cfg.get('services', {}).items()}

    def _source_changed(self, spec, instance):
        if not instance or not instance.get('source'):
            return None
        try:
            return source_snapshot(self.ws.project(spec.get('project'))) != instance['source']
        except (KitError, OSError, subprocess.TimeoutExpired):
            return 'unknown'

    def up(self, names, jobs=None):
        from .operations import operation, service_resources
        names, jobs = self._plan(names, jobs)
        with operation(self.ws, 'services.up', service_resources(self.ws, names)):
            return self._up_all(names, jobs)

    def restart(self, names, jobs=None):
        from .operations import operation, service_resources
        selected = list(names)
        names, jobs = self._plan(names, jobs)
        with operation(self.ws, 'services.restart', service_resources(self.ws, names)):
            self._down(selected)
            return self._up_all(names, jobs)

    def _plan(self, names, jobs):
        from .startup import validate
        return validate(self.ws, names, jobs)

    def _up_all(self, names, jobs):
        from .startup import Startup
        return Startup(self, names, jobs).run()

    def _up(self, name, cancel, timings, build_lock):
        safe_name(name)
        specs = self.ws.cfg.get('services', {})
        if name not in specs:
            raise KitError('Unknown service: ' + name)
        spec = specs[name]
        state = read(self.file, {})
        own = name in state and matches(state[name]['identity'])
        if spec.get('mode', 'managed') == 'external':
            with phase(timings, 'ready', name):
                if not healthy(self.ws, spec):
                    raise KitError('External service not ready: ' + name)
            return 'reused'
        if cancel.is_set():
            raise KitError('Startup interrupted')
        queued = time.monotonic()
        with build_lock if not own else nullcontext():
            timings['queue'] += time.monotonic() - queued
            if cancel.is_set():
                raise KitError('Startup interrupted')
            self._launch(name, spec, state, own, cancel, timings)
        with phase(timings, 'ready', name):
            self._wait_ready(name, spec, state, cancel)
        return 'reused' if own else 'ready'

    def _launch(self, name, spec, state, own, cancel, timings):
        if not own:
            port = spec.get('port', spec.get('ready', {}).get('port'))
            if port:
                try:
                    with socket.create_connection(('127.0.0.1', port), timeout=.3):
                        raise KitError('Port occupied; declare external explicitly to reuse: ' + name)
                except (ConnectionError, TimeoutError, OSError):
                    pass
            if healthy(self.ws, spec):
                raise KitError('Unowned endpoint already available: ' + name)
            if 'prepare' in spec:
                with phase(timings, 'build', name):
                    before = source_snapshot(self.ws.project(spec.get('project')))
                    prepare = spec['prepare']
                    log = self.ws.state / 'logs' / (name + '-prepare.log')
                    log.parent.mkdir(parents=True, exist_ok=True)
                    with log.open('ab') as handle:
                        p = spawn(self.ws.command(prepare), self.ws.project(spec.get('project')), self.ws.env(prepare), handle)
                        identity = fingerprint(p.pid)
                        from .operations import track_child
                        track_child(identity)
                        try:
                            deadline = time.monotonic() + prepare.get('timeout', 120)
                            while p.poll() is None:
                                if cancel.wait(.05):
                                    raise KeyboardInterrupt
                                if time.monotonic() >= deadline:
                                    raise subprocess.TimeoutExpired(p.args, prepare.get('timeout', 120))
                        except (subprocess.TimeoutExpired, KeyboardInterrupt) as error:
                            stop(identity)
                            p.wait(timeout=10)
                            if isinstance(error, KeyboardInterrupt):
                                raise
                            raise KitError('Prepare interrupted or timed out: ' + name)
                    if p.returncode:
                        raise KitError('Prepare command failed: ' + name)
                    if source_snapshot(self.ws.project(spec.get('project'))) != before:
                        raise KitError('Source changed during build; artifact not started: ' + name)
            else:
                before = None
            if cancel.is_set():
                raise KitError('Startup interrupted')
            with phase(timings, 'start', name):
                artifacts = artifact_snapshot(self.ws, spec)
                log = self.ws.state / 'logs' / (name + '.log')
                log.parent.mkdir(parents=True, exist_ok=True)
                with log.open('ab') as handle:
                    process = spawn(self.ws.command(spec), self.ws.project(spec.get('project')), self.ws.env(spec), handle)
                try:
                    identity = fingerprint(process.pid)
                except Exception:
                    raise KitError('Service exited at startup: ' + name)
                try:
                    state[name] = {'identity': identity, 'log': str(log), 'started': time.time(), 'versions': versions(self.ws),
                                   'source': before, 'artifacts': artifacts,
                                   'version_scope': 'build_artifact' if artifacts else 'source_or_hot_reload_unpinned'}
                    self._save_item(name, state[name])
                except BaseException:
                    from .operations import track_child
                    track_child(identity)
                    stop(identity)
                    raise

    def _wait_ready(self, name, spec, state, cancel):
        deadline = time.monotonic() + spec.get('timeout', 30)
        while time.monotonic() < deadline:
            if cancel.is_set():
                raise KitError('Startup interrupted')
            if not matches(state[name]['identity']):
                raise KitError('Service exited: ' + name)
            if healthy(self.ws, spec):
                return
            cancel.wait(.1)
        raise KitError('Service readiness timed out: ' + name)

    def down(self, names):
        from .operations import operation, service_resources
        names = names or list(read(self.file, {}))
        with operation(self.ws, 'services.down', service_resources(self.ws, names, builds=False)):
            return self._down(names)

    def _down(self, names):
        state = read(self.file, {})
        selected = set(names or state)
        unknown = selected - self.ws.cfg.get('services', {}).keys() - state.keys()
        if unknown:
            raise KitError('Unknown services: ' + ', '.join(sorted(unknown)))
        for name in state:
            if name not in selected and selected.intersection(self.ws.cfg.get('services', {}).get(name, {}).get('depends', [])):
                raise KitError('A running dependent service was not selected: ' + name)
        pending = set(selected)
        while pending:
            leaves = [n for n in pending if not any(n in self.ws.cfg.get('services', {}).get(x, {}).get('depends', []) for x in pending)]
            if not leaves:
                raise KitError('Service dependency cycle')
            for name in leaves:
                if name in state:
                    if self.ws.cfg.get('services', {}).get(name, {}).get('mode') == 'external':
                        pending.remove(name)
                        continue
                    stop(state[name]['identity'])
                    del state[name]
                    self._save_item(name, None)
                pending.remove(name)
        return self.status()

    def logs(self, name):
        value = read(self.file, {}).get(name)
        if not value:
            raise KitError('No owned log for ' + name)
        from pathlib import Path
        return {'service': name, 'text': Path(value['log']).read_text(encoding='utf-8', errors='replace')[-16000:]}
