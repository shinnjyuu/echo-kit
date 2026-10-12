import socket
import subprocess
import time
import urllib.request
import hashlib
from contextlib import nullcontext, contextmanager, ExitStack
from pathlib import Path

from .core import KitError, read, save, safe_name, versions
from .processes import fingerprint, matches, spawn, stop
from .tray import startup_scope, notify


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

    def instances(self):
        from .service_registry import Registry, key
        registry = Registry()
        local = read(self.file, {})
        result = {}
        for name, spec in self.ws.cfg.get('services', {}).items():
            if spec.get('mode', 'managed') == 'external':
                continue
            value = registry.get(key(self.ws, name))
            if name in local and 'identity' in local[name]:
                result[name] = {**local[name], 'legacy': True}
            elif value:
                result[name] = value
        return result

    def _save_item(self, name, value):
        from .service_registry import Registry
        if value is not None:
            Registry().create(self.ws, name, value)
            notify(self.ws)

    def status(self, probe=True):
        from .service_registry import process_state
        state = self.instances()
        from .operations import Journal, service_resources
        operations = Journal().list()
        result = {}
        for name, spec in self.ws.cfg.get('services', {}).items():
            instance = state.get(name)
            actual = process_state(instance['identity']) if instance else None
            occupants = [op for op in operations if set(op['held']).intersection(
                service_resources(self.ws, [name], builds=False))]
            result[name] = {'mode': spec.get('mode', 'managed'),
                           'owned': actual == 'running',
                           'process_state': actual,
                           'ready': healthy(self.ws, spec) if probe else None,
                           'instance': instance,
                           'origin': instance.get('origin') if instance else None,
                           'references': instance.get('references', []) if instance else [],
                           'migration_required': bool(instance and instance.get('legacy')),
                           'source_changed_since_start': self._source_changed(spec, instance),
                           'occupants': occupants,
                           'users': [{'operation_id': o['id'], 'workspace': o['workspace'],
                                      'environment': o['environment'], 'state': o['state']}
                                     for o in occupants if o['run_id']]}
        return result

    @contextmanager
    def reservation(self, names, action, access='shared', run_id=None, evidence=None, extra=None, force=False):
        from filelock import FileLock, Timeout
        from .service_registry import Registry, key, process_state
        from .operations import operation, service_resources, Journal, service_names
        if access not in ('shared', 'exclusive'):
            raise KitError('service_access must be shared or exclusive')
        names = service_names(self.ws, names)
        registry = Registry()
        with ExitStack() as startup_locks:
            registry.root.mkdir(parents=True, exist_ok=True)
            for identity in sorted({key(self.ws, n) for n in names
                                    if self.ws.cfg['services'][n].get('mode', 'managed') != 'external'}):
                try:
                    startup_locks.enter_context(FileLock(str(registry.root / (identity + '.startup.lock')), timeout=30))
                except Timeout as cause:
                    error = KitError('Shared service startup wait timed out; inspect operations list')
                    if evidence:
                        from .runs import report
                        record = read(Path(evidence) / 'run.json')
                        record.update(status='blocked', reason=str(error), finished=time.time())
                        save(Path(evidence) / 'run.json', record)
                        report(Path(evidence))
                    raise error from cause
            resources = dict(extra or {})
            for name in names:
                spec = self.ws.cfg['services'][name]
                record = registry.get(key(self.ws, name)) if spec.get('mode', 'managed') != 'external' else None
                mode = 'exclusive' if force or (spec.get('mode', 'managed') != 'external' and
                                                (not record or process_state(record['identity']) != 'running')) else access
                for resource, value in service_resources(self.ws, [name], builds=mode == 'exclusive', access=mode).items():
                    resources[resource] = 'exclusive' if 'exclusive' in (value, resources.get(resource)) else 'shared'
            from .operations import _held
            outer = {oid for oid, _ in _held.get()}
            with operation(self.ws, action, resources, run_id, evidence) as oid:
                def prepared():
                    if oid not in outer:
                        Journal().downgrade_services(oid, service_resources(self.ws, names, builds=False, access=access))
                    startup_locks.close()
                yield oid, prepared

    def _source_changed(self, spec, instance):
        if not instance or not instance.get('source'):
            return None
        try:
            return source_snapshot(self.ws.project(spec.get('project'))) != instance['source']
        except (KitError, OSError, subprocess.TimeoutExpired):
            return 'unknown'

    @startup_scope
    def up(self, names, jobs=None):
        names, jobs = self._plan(names, jobs)
        with self.reservation(names, 'services.up') as (_, prepared):
            result = self._up_all(names, jobs)
            prepared()
            return result

    @startup_scope
    def restart(self, names, jobs=None):
        selected = list(names) if names else self.started_here()
        names, jobs = self._plan(selected, jobs)
        with self.reservation(names, 'services.restart', force=True) as (_, prepared):
            self._down(selected)
            result = self._up_all(names, jobs)
            prepared()
            return result

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
        from .service_registry import Registry, key, process_state
        registry = Registry()
        if spec.get('mode', 'managed') != 'external':
            registry.migrate(self.ws, name, read(self.file, {}).get(name))
        state = self.instances()
        own = name in state and process_state(state[name]['identity']) == 'running'
        if name in state:
            if not state[name].get('retired'):
                registry.compatible(self.ws, name, state[name])
            if process_state(state[name]['identity']) not in ('running', 'stopped'):
                raise KitError('Service identity is uncertain; inspect registration: ' + name)
            if own and not healthy(self.ws, spec):
                raise KitError('Registered service is unhealthy; explicit restart required: ' + name)
            registry.reference(self.ws, name, key(self.ws, name))
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

    def started_here(self):
        return [name for name, record in self.instances().items()
                if record.get('origin', {}).get('workspace') == str(self.ws.root)
                and record.get('origin', {}).get('environment') == self.ws.environment]

    def down(self, names):
        from .operations import operation, service_resources
        selected = list(names) if names else self.started_here()
        with operation(self.ws, 'services.down', service_resources(self.ws, selected, builds=False)):
            return self._down(selected)

    def _down(self, names):
        from .service_registry import Registry, key
        registry = Registry()
        state = self.instances()
        pending = set(names)
        if pending - self.ws.cfg.get('services', {}).keys():
            raise KitError('Unknown selected service')
        for name in pending:
            if state.get(name, {}).get('legacy'):
                raise KitError('Legacy service requires verified registration via services up before stopping')
            if name in state and self.ws.cfg['services'][name].get('mode', 'managed') != 'external':
                local = read(self.file, {}).get(name, {})
                if local.get('registry_id') != key(self.ws, name):
                    raise KitError('Register this workspace reference with services up before stopping')
        selected_ids = {key(self.ws, n) for n in pending}
        from .service_registry import process_state
        for record in registry.all():
            if (record['registry_id'] not in selected_ids and selected_ids.intersection(record.get('dependencies', []))
                    and process_state(record['identity']) != 'stopped'):
                raise KitError('A registered dependent service was not selected')
        while pending:
            leaves = [n for n in pending if not any(key(self.ws, n) in state.get(x, {}).get('dependencies', []) for x in pending)]
            if not leaves:
                raise KitError('Registered service dependency cycle')
            for name in leaves:
                if name in state and self.ws.cfg['services'][name].get('mode', 'managed') != 'external':
                    registry.stop_record(state[name])
                pending.remove(name)
        return self.status()

    def logs(self, name):
        from .service_registry import process_state
        value = self.instances().get(name)
        if not value or value.get('legacy') or process_state(value['identity']) in ('unknown', 'identity_mismatch'):
            raise KitError('No confirmed owned log for ' + name)
        return {'service': name, 'text': Path(value['log']).read_text(encoding='utf-8', errors='replace')[-16000:]}
