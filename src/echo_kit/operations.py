"""Per-user operation journal. File locks guard transactions, never business work."""
import os
import sys
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from urllib.parse import urlsplit

from filelock import FileLock

from .core import KitError, read, save, safe_name
from .processes import fingerprint, matches

_held = ContextVar('echo_operations', default=())


def data_root():
    if os.environ.get('ECHO_KIT_DATA_HOME'):
        return Path(os.environ['ECHO_KIT_DATA_HOME']).resolve()
    if os.name == 'nt':
        base = Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData/Local'))
    elif sys.platform == 'darwin':
        base = Path.home() / 'Library/Application Support'
    else:
        base = Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local/share'))
    return base / 'echo-kit'


def canonical_path(path):
    return os.path.normcase(str(Path(path).resolve()))


def endpoint(url=None, host=None, port=None):
    if url:
        parsed = urlsplit(url)
        host = parsed.hostname
        port = parsed.port or (443 if parsed.scheme in ('https', 'wss') else 80)
    host = (host or '127.0.0.1').lower().strip('[]')
    if host in ('localhost', '127.0.0.1', '::1', '0.0.0.0', '::'):
        host = 'loopback'
    if not port:
        raise KitError('Resource requires a port, URL or resource_id')
    return f'endpoint:{host}:{int(port)}'


def service_names(ws, names):
    result = []
    def visit(name, stack):
        if name in stack:
            raise KitError('Service dependency cycle')
        if name not in ws.cfg.get('services', {}):
            raise KitError('Unknown service: ' + name)
        for dep in ws.cfg['services'][name].get('depends', []):
            visit(dep, stack + [name])
        if name not in result:
            result.append(name)
    for name in names:
        visit(name, [])
    return result


def service_resources(ws, names, builds=True):
    resources = {}
    for name in service_names(ws, names):
        spec = ws.cfg['services'][name]
        ready = spec.get('ready', {})
        keys = []
        if spec.get('port') or ready.get('port'):
            keys.append(endpoint(host=ready.get('host'), port=spec.get('port') or ready['port']))
        if ready.get('url'):
            keys.append(endpoint(url=ready['url']))
        if spec.get('resource_id'):
            keys.append('service:' + spec['resource_id'])
        if not keys:
            raise KitError('Service requires resource_id when no endpoint is declared: ' + name)
        resources.update({key: 'exclusive' for key in keys})
        if builds and spec.get('prepare') and spec.get('mode', 'managed') != 'external':
            resources['build:' + canonical_path(ws.project(spec.get('project')))] = 'exclusive'
    return resources


def browser_resource(ws):
    cfg = ws.cfg.get('browser', {})
    return endpoint(url=cfg['endpoint']) if cfg.get('endpoint') else endpoint(port=cfg.get('port', 19323))


def browser_resources(ws):
    keys = {browser_resource(ws): 'shared'}
    if not ws.cfg.get('browser', {}).get('endpoint'):
        state = read(ws.state / 'browser.json', {})
        if state.get('endpoint'):
            keys[endpoint(url=state['endpoint'])] = 'shared'
        if state.get('id'):
            keys['container:' + state['id']] = 'shared'
    return keys


class ResourceBusy(KitError):
    def __init__(self, blockers):
        super().__init__('Resources occupied; operation not executed')
        self.details = {'reason': 'resource_busy', 'occupants': blockers}


class Journal:
    def __init__(self):
        self.root = data_root() / 'operations'
        self.root.mkdir(parents=True, exist_ok=True)

    def lock(self):
        return FileLock(str(self.root / 'journal.lock'), timeout=5)

    def file(self, oid):
        return self.root / (safe_name(oid) + '.json')

    def show(self, oid):
        value = read(self.file(oid))
        if value is None:
            raise KitError('Unknown operation: ' + oid)
        value['owner_alive'] = matches(value['identity'])
        value['children_alive'] = any(matches(child) for child in value.get('children', []))
        if value['held'] and not value['owner_alive']:
            value['state'] = 'needs_cleanup'
        value['duration'] = (value.get('finished') or time.time()) - value['started']
        value['query'] = 'echo-kit operations show ' + oid
        return value

    def list(self):
        return [self.show(p.stem) for p in sorted(self.root.glob('*.json'))]

    def acquire(self, ws, action, resources, run_id=None, evidence=None):
        from . import __version__
        with self.lock():
            blockers = [r for r in self.list() if r['held'] and any(
                key in r['held'] and ('exclusive' in (mode, r['held'][key]))
                for key, mode in resources.items())]
            if blockers:
                raise ResourceBusy(blockers)
            oid = uuid.uuid4().hex
            record = {'id': oid, 'actor': getattr(ws, 'actor', None), 'workspace': str(ws.root),
                      'task_id': getattr(ws, 'task_id', None), 'tool_version': __version__,
                      'environment': ws.environment, 'action': action, 'resources': resources,
                      'held': resources.copy(), 'started': time.time(), 'identity': fingerprint(os.getpid()),
                      'run_id': run_id, 'evidence': str(evidence) if evidence else None,
                      'state': 'running', 'events': [{'time': time.time(), 'action': action, 'state': 'started'}]}
            save(self.file(oid), record)
            return oid

    def update(self, oid, **fields):
        with self.lock():
            record = read(self.file(oid))
            record.update(fields)
            save(self.file(oid), record)

    def release_builds(self, oid):
        with self.lock():
            record = read(self.file(oid))
            record['held'] = {key: mode for key, mode in record['held'].items() if not key.startswith('build:')}
            save(self.file(oid), record)
        for current, held in _held.get():
            if current == oid:
                for key in list(held):
                    if key.startswith('build:'):
                        del held[key]

    def event(self, oid, action, state):
        with self.lock():
            record = read(self.file(oid))
            record['events'].append({'time': time.time(), 'action': action, 'state': state})
            save(self.file(oid), record)

    def release(self, oid, result='completed', retain=False):
        with self.lock():
            record = read(self.file(oid))
            retain = retain or any(matches(child) for child in record.get('children', []))
            record.update(state='needs_cleanup' if retain else 'finished', result=result)
            record['events'].append({'time': time.time(), 'action': record['action'], 'state': record['state']})
            if not retain:
                record.update(held={}, finished=time.time())
            save(self.file(oid), record)

    def resolve(self, oid, note):
        if not note or not note.strip():
            raise KitError('A confirmed cleanup note is required')
        with self.lock():
            record = self.show(oid)
            if record['owner_alive'] or record['children_alive']:
                raise KitError('Operation process is still alive; cannot resolve')
            if not record['held']:
                raise KitError('Operation is already released')
            record.update(held={}, state='finished', finished=time.time(), resolution_note=note,
                          result='manually_resolved')
            record['events'].append({'time': time.time(), 'action': 'manual_resolution', 'state': 'finished'})
            save(self.file(oid), record)
        return record

    def claim_cleanup(self, oid, ws, path):
        with self.lock():
            record = self.show(oid)
            if (canonical_path(record['workspace']) != canonical_path(ws.root) or record['evidence'] != str(path)
                    or record['environment'] != ws.environment):
                raise KitError('Cleanup does not belong to this run')
            if record['owner_alive'] or record['children_alive']:
                raise KitError('Operation process is still alive')
            if not record['held']:
                raise KitError('Operation already released')
            record.update(identity=fingerprint(os.getpid()), state='running')
            record['events'].append({'time': time.time(), 'action': 'cleanup', 'state': 'started'})
            save(self.file(oid), record)


def check_legacy(ws):
    for file in ws.runs.glob('*/active.json'):
        record = read(file.parent / 'run.json', {})
        if not record.get('operation_id'):
            raise KitError('Unfinished legacy run requires cleanup: ' + file.parent.name)


def track_child(identity):
    journal = Journal()
    for oid, _ in _held.get():
        with journal.lock():
            record = read(journal.file(oid))
            record.setdefault('children', []).append(identity)
            save(journal.file(oid), record)


@contextmanager
def bind_operation(oid):
    if not oid:
        yield
        return
    token = _held.set((*_held.get(), (oid, Journal().show(oid)['held'])))
    try:
        yield
    finally:
        _held.reset(token)


def activity(action, state):
    if _held.get():
        Journal().event(_held.get()[-1][0], action, state)


@contextmanager
def operation(ws, action, resources, run_id=None, evidence=None):
    resources = {**resources, 'toolchain:' + canonical_path(ws.root): 'shared'}
    try:
        check_legacy(ws)
    except KitError as error:
        if evidence:
            from .runs import report
            record = read(Path(evidence) / 'run.json')
            record.update(status='blocked', reason=str(error), finished=time.time())
            save(Path(evidence) / 'run.json', record)
            report(evidence)
        raise
    journal = Journal()
    # Nested service preparation is covered by the enclosing verify reservation.
    for oid, held in reversed(_held.get()):
        if resources and all(key in held and (held[key] == 'exclusive' or mode == 'shared')
                             for key, mode in resources.items()):
            journal.event(oid, action, 'started')
            try:
                yield oid
            except BaseException:
                journal.event(oid, action, 'failed')
                raise
            else:
                journal.event(oid, action, 'completed')
            return
    resources = dict(resources)
    try:
        oid = journal.acquire(ws, action, resources, run_id, evidence)
    except ResourceBusy as error:
        if evidence:
            from .runs import report
            record = read(Path(evidence) / 'run.json')
            record.update(status='blocked', finished=time.time(), **error.details)
            save(Path(evidence) / 'run.json', record)
            report(evidence)
        raise
    token = _held.set((*_held.get(), (oid, resources)))
    result = 'completed'
    try:
        yield oid
    except BaseException:
        result = 'failed'
        raise
    finally:
        _held.reset(token)
        active = evidence is not None and (Path(evidence) / 'active.json').exists()
        if evidence:
            result = read(Path(evidence) / 'run.json', {}).get('status', result)
        journal.release(oid, result, retain=active)
