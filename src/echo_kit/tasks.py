"""Project-selected versions and explicit task lifetimes across CLI calls."""
import sys
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from importlib.resources import files

from filelock import FileLock

from . import __version__, updates
from .core import KitError, read, safe_name, save
from .documentation import PACKAGE, digest, tool_info
from .operations import Journal, canonical_path

LOCKFILE = 'echo-kit.lock.json'
LAUNCHER = 'echo-kit.py'
CURRENT_TASK = ContextVar('echo_kit_task', default=None)


def control_lock(ws):
    ws.state.mkdir(parents=True, exist_ok=True)
    return FileLock(str(ws.state / 'toolchain.lock'), timeout=1)


def project_version(ws, required=True):
    value = read(ws.root / LOCKFILE)
    if value is None and not required:
        return None
    if not isinstance(value, dict) or value.get('schema_version') != 1 or value.get('package') != PACKAGE:
        raise KitError('Managed entry missing or invalid; run updates setup first')
    updates.release_version(value.get('version'))
    if value.get('policy') not in updates.POLICIES:
        raise KitError('Invalid project update policy')
    return value


def setup(ws, policy=None):
    with control_lock(ws):
        existing = project_version(ws, required=False)
        if existing is None and any(pending(ws).values()):
            raise KitError('Finish existing operations and runs before setting up the managed entry')
        launcher = ws.root / LAUNCHER
        lockfile = ws.root / LOCKFILE
        template = files('echo_kit').joinpath('resources/launcher.py').read_bytes()
        if launcher.is_symlink() or lockfile.is_symlink():
            raise KitError('Managed entry paths must not be symbolic links')
        if launcher.exists() and digest(launcher.read_bytes()) not in (
                digest(template), (existing or {}).get('launcher_digest')):
            raise KitError('Project-owned echo-kit.py differs; preserve it and resolve the entrypoint conflict')
        if policy is not None and policy not in updates.POLICIES:
            raise KitError('Unknown update policy')
        value = {**(existing or {}), 'schema_version': 1, 'package': PACKAGE,
                 'version': (existing or {}).get('version', __version__),
                 'policy': policy or (existing or {}).get('policy', 'patch'), 'launcher_digest': digest(template)}
        if not launcher.exists() or launcher.read_bytes() != template:
            launcher.write_bytes(template)
        save(lockfile, value)
    return {'status': 'passed', **value, 'entrypoint': str(launcher),
            'next_command': [sys.executable, str(launcher), '--json', 'task', 'start', '--label', 'TASK_DESCRIPTION']}


def task_path(ws, tid):
    return ws.state / 'tasks' / (safe_name(tid) + '.json')


def load_task(ws, tid, open_only=False):
    value = read(task_path(ws, tid))
    if (not isinstance(value, dict) or value.get('schema_version') != 1 or value.get('id') != tid
            or not isinstance(value.get('workspace'), str) or not isinstance(value.get('environment'), str)
            or value.get('state') not in ('open', 'finished') or not isinstance(value.get('tool'), dict)
            or value['tool'].get('version') != value.get('version')
            or canonical_path(value['workspace']) != canonical_path(ws.root)):
        raise KitError('Unknown or invalid task: ' + tid)
    updates.release_version(value.get('version'))
    if open_only and value.get('state') != 'open':
        raise KitError('Task is finished; start a new task')
    return value


def list_tasks(ws):
    return [load_task(ws, path.stem) for path in sorted((ws.state / 'tasks').glob('*.json'))]


def pending(ws):
    tasks = [t['id'] for t in list_tasks(ws) if t.get('state') == 'open']
    operations = [r['id'] for r in Journal().list() if canonical_path(r['workspace']) == canonical_path(ws.root)
                  and (r.get('held') or r.get('state') in ('running', 'needs_cleanup'))]
    active = [p.parent.name for p in ws.runs.glob('*/active.json')]
    return {'tasks': tasks, 'operations': operations, 'runs': active}


def _select(ws, selection, target, no_network=False):
    target = updates.release_version(target)
    blockers = pending(ws)
    if any(blockers.values()):
        error = KitError('Finish open tasks and pending operations before changing the project version')
        error.details = {'reason': 'upgrade_deferred', 'pending': blockers}
        raise error
    journal = Journal()
    oid = journal.acquire(ws, 'updates.apply', {'toolchain:' + canonical_path(ws.root): 'exclusive'})
    outcome = 'failed'
    try:
        info = updates.probe(target, ws, no_network)
        value = {**selection, 'version': target, 'previous_version': selection['version'],
                 'selected_at': time.time(), 'documentation_digest': info['documentation_digest']}
        # An editor does not take our lock: refuse to overwrite a concurrent edit.
        if project_version(ws) != selection:
            raise KitError('Project version policy changed during preparation; retry after reviewing the change')
        save(ws.root / LOCKFILE, value)
        outcome = 'completed'
        return value, info
    finally:
        journal.release(oid, outcome)


def apply(ws, target=None, no_network=False):
    with control_lock(ws):
        selection = project_version(ws)
        discovery = updates.check(selection['version'], selection['policy'], refresh=True, no_network=no_network)
        if target is None:
            target = discovery['candidate_version']
            if discovery['stale'] or target is None:
                return {'status': 'passed', 'changed': False, 'version': selection['version'], 'update': discovery,
                        'next_step': 'No fresh candidate permitted by policy. An explicit --version may select a release.'}
        if updates.release_version(target) == selection['version']:
            return {'status': 'passed', 'changed': False, 'version': selection['version'], 'update': discovery}
        selected, info = _select(ws, selection, target, no_network)
        return {'status': 'passed', 'changed': True, **selected, 'tool': info, 'update': discovery}


def start(ws, label=None, no_network=False):
    with control_lock(ws):
        selection = project_version(ws)
        discovery = updates.check(selection['version'], selection['policy'], refresh=True, no_network=no_network)
        info = None
        adoption = {'state': 'unchanged'}
        candidate = discovery['candidate_version']
        if candidate and not discovery['stale']:
            try:
                selection, info = _select(ws, selection, candidate, no_network)
                adoption = {'state': 'upgraded', 'version': candidate}
            except KitError as error:
                adoption = {'state': 'deferred' if getattr(error, 'details', {}) else 'failed',
                            'reason': str(error), **getattr(error, 'details', {})}
        if info is None:
            info = updates.probe(selection['version'], ws, no_network)
        tid = uuid.uuid4().hex
        value = {'schema_version': 1, 'id': tid, 'state': 'open', 'workspace': str(ws.root),
                 'environment': ws.environment, 'label': label or getattr(ws, 'actor', None),
                 'version': selection['version'], 'tool': info, 'started_at': time.time(),
                 'update': {**discovery, 'selected_version': selection['version']}, 'adoption': adoption}
        save(task_path(ws, tid), value)
    return {'status': 'passed', **value,
            'command_prefix': [sys.executable, str(ws.root / LAUNCHER), '--task', tid],
            'finish_command': [sys.executable, str(ws.root / LAUNCHER), '--json', 'task', 'finish', tid]}


@contextmanager
def bind(ws, tid):
    path = task_path(ws, tid)
    with FileLock(str(path) + '.lock', timeout=1):
        value = load_task(ws, tid, open_only=True)
        if value['version'] != __version__:
            raise KitError('Task must be executed by its pinned tool version')
        if value['environment'] != ws.environment:
            raise KitError('Task environment differs; use the environment recorded at task start')
        if value['tool'].get('documentation_digest') != tool_info()['documentation_digest']:
            raise KitError('Task documentation changed; preserve pending work and prepare a new task with the matching package')
        token = CURRENT_TASK.set(value)
        try:
            yield value
        finally:
            CURRENT_TASK.reset(token)


def finish(ws, tid):
    with control_lock(ws), FileLock(str(task_path(ws, tid)) + '.lock', timeout=1):
        value = load_task(ws, tid)
        if value['state'] == 'finished':
            return {'status': 'passed', **value}
        operations = [r['id'] for r in Journal().list() if r.get('task_id') == tid
                      and (r.get('held') or r.get('state') in ('running', 'needs_cleanup'))]
        runs = [p.parent.name for p in ws.runs.glob('*/active.json')
                if read(p.parent / 'run.json', {}).get('task_id') == tid]
        if operations or runs:
            error = KitError('Task has pending work; clean up its runs before finishing')
            error.details = {'operations': operations, 'runs': runs}
            raise error
        value.update(state='finished', finished_at=time.time())
        save(task_path(ws, tid), value)
        return {'status': 'passed', **value}
