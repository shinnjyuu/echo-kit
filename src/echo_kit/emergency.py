"""Stop user-local Kit resources without acquiring their business reservations."""

import hashlib
import json
import re
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import psutil
from filelock import FileLock, Timeout

from . import docker_host, launches
from .core import KitError, safe_name, save
from .operations import Journal, data_root
from .processes import alive, identity_exited, kill_tree, process_owner


VERIFY_SECONDS = 5
CONTAINER_ID = re.compile(r'[a-f0-9]{64}\Z')
LIVE_CONTAINER = {'running', 'paused', 'restarting'}
STOPPED_CONTAINER = {'created', 'exited', 'dead'}
RECORD_ERRORS = (KitError, OSError, ValueError, TypeError, KeyError)
DOCKER_ERRORS = (KitError, OSError, ValueError, subprocess.TimeoutExpired)


def object_record(path):
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return None
    if not isinstance(value, dict):
        raise KitError('Invalid registration')
    return value


def docker_call(host, deadline, *args):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise KitError('Docker verification timed out')
    argv, options = docker_host.command(args, host)
    result = subprocess.run(argv, capture_output=True, text=True, encoding='utf-8',
                            timeout=min(remaining, 2), **options)
    if result.returncode:
        raise KitError('Docker command failed')
    return result.stdout


def container_states(host, deadline):
    output = docker_call(host, deadline, 'ps', '--all', '--no-trunc', '--format',
                         '{{.ID}}\t{{.State}}\t{{.Label "echo-kit.workspace"}}')
    states = {}
    for line in output.splitlines():
        fields = line.split('\t')
        if len(fields) != 3 or not CONTAINER_ID.fullmatch(fields[0]):
            raise KitError('Invalid Docker state response')
        states[fields[0]] = (fields[1], fields[2])
    return states


def stop_containers(targets, deadline):
    groups = {}
    fallback = None
    for row, record in targets:
        if 'docker_host' in record:
            host = record['docker_host']
        else:
            if fallback is None:
                try:
                    fallback = docker_host.selected_host(timeout=max(.01, min(2, deadline - time.monotonic())))
                except DOCKER_ERRORS:
                    fallback = ''
            host = fallback
        if not docker_host.is_local(host):
            row.update(status='unconfirmed', reason='local_docker_engine_unconfirmed')
            continue
        groups.setdefault(host, []).append((row, record))

    for host, group in groups.items():
        try:
            states = container_states(host, deadline)
        except DOCKER_ERRORS:
            for row, _ in group:
                row.update(status='unconfirmed', reason='docker_state_unavailable')
            continue
        pending = []
        for row, record in group:
            current = states.get(record['id'])
            if not current:
                if 'docker_host' in record:
                    row['status'] = 'already_exited'
                else:
                    row.update(status='unconfirmed', reason='legacy_engine_unconfirmed')
            elif current[1] != record['owner']:
                row.update(status='unconfirmed', reason='container_ownership_mismatch')
            elif current[0] in STOPPED_CONTAINER:
                row['status'] = 'already_exited'
            elif current[0] in LIVE_CONTAINER:
                pending.append((row, record['id']))
            else:
                row.update(status='unconfirmed', reason='container_state_unconfirmed')
        if not pending:
            continue

        def signal(cid):
            try:
                docker_call(host, deadline, 'kill', '--signal=KILL', cid)
            except DOCKER_ERRORS:
                pass  # Query actual exit even if the response was lost or raced with exit.

        with ThreadPoolExecutor(max_workers=min(4, len(pending))) as workers:
            list(workers.map(signal, (cid for _, cid in pending)))
        while pending and time.monotonic() < deadline:
            try:
                states = container_states(host, deadline)
            except DOCKER_ERRORS:
                break
            still_running = []
            for row, cid in pending:
                if cid not in states or states[cid][0] in STOPPED_CONTAINER:
                    row['status'] = 'stopped'
                else:
                    still_running.append((row, cid))
            pending = still_running
            if pending:
                time.sleep(min(.05, max(0, deadline - time.monotonic())))
        for row, _ in pending:
            row.update(status='unconfirmed', reason='container_exit_unconfirmed')


class Sweep:
    def __init__(self):
        caller = psutil.Process()
        self.owner = process_owner(caller)
        self.protected = {caller.pid, *(parent.pid for parent in caller.parents())}
        self.targets = []
        self.workspaces = {}
        self.issues = set()
        self.processes = {}
        self.signalled = {}
        self.waiting = []
        self.browsers = {}
        self.legacy = []
        self.operation_targets = {}
        self.finished_launches = []

    def row(self, kind, name, workspace=None):
        row = {'kind': kind, 'target': name, 'status': 'unconfirmed'}
        if workspace:
            row['workspace'] = str(workspace)
        self.targets.append(row)
        return row

    def issue(self, name, reason='registration_unreadable', workspace=None):
        key = (str(name), reason)
        if key not in self.issues:
            self.issues.add(key)
            self.row('registration', str(name), workspace).update(reason=reason)

    def workspace(self, record):
        if not isinstance(record, dict):
            raise KitError('Invalid workspace registration')
        workspace = record.get('workspace')
        if not workspace or workspace in self.workspaces:
            return
        root = Path(workspace)
        if not root.is_absolute():
            raise KitError('Invalid recorded workspace')
        self.workspaces[workspace] = root.resolve()

    def process(self, kind, identity, name, workspace=None):
        key = (identity['pid'], identity['created'])
        known = self.processes.get(key)
        if known and (known[0]['status'] != 'unconfirmed' or known[1] == identity):
            return known[0]
        row = self.row(kind, name, workspace)
        try:
            targets, incomplete = kill_tree(identity, self.signalled, self.protected, self.owner)
        except psutil.AccessDenied:
            row['reason'] = 'permission_denied'
        except (psutil.Error, *RECORD_ERRORS):
            row['reason'] = 'process_ownership_unconfirmed'
        else:
            row['status'] = 'checking' if targets else 'already_exited'
            if incomplete:
                row.update(status='unconfirmed', reason='process_tree_unconfirmed')
            elif targets:
                self.waiting.append((row, targets))
        self.processes[key] = (row, identity)
        return row

    def browser(self, state, owner, workspace):
        if not isinstance(state, dict) or not isinstance(owner, str):
            raise KitError('Invalid browser registration')
        if state.get('backend') == 'agent-browser':
            self.native_browser(state, workspace)
            return
        cid = state.get('id')
        if not isinstance(cid, str) or not CONTAINER_ID.fullmatch(cid):
            raise KitError('Missing full browser container ID')
        key = (state.get('docker_host'), cid, owner)
        if key not in self.browsers:
            row = self.row('browser', cid, workspace)
            self.browsers[key] = (row, {**state, 'owner': owner})

    def native_browser(self, record, workspace):
        """Signal verified native roots, including orphans after a worker crash."""
        sid = safe_name(record['session_id'])
        identities = [record['identity'], *record.get('children', [])]
        if any(not isinstance(identity, dict) for identity in identities):
            raise KitError('Invalid native browser identities')
        for identity in identities:
            row = self.process('native_browser', identity, sid, workspace)
            if record.get('operation_id'):
                self.operation_targets.setdefault(record['operation_id'], []).append(row)

    def pending(self, paths):
        unresolved = {}
        for path in paths:
            try:
                record = object_record(path)
                if not record:
                    continue
                kind = record['kind']
                operations = record.get('operations', [])
                if kind not in ('task', 'service', 'browser', 'native_browser') or not isinstance(operations, list) or not all(
                        isinstance(oid, str) for oid in operations):
                    raise KitError('Invalid launch registration')
                self.workspace(record)
                if kind == 'browser':
                    browser = record['browser']
                    self.browser(browser, browser['owner'], record.get('workspace'))
                    unresolved[path] = record
                elif record.get('identity'):
                    identity = record['identity']
                    row = self.process(kind, identity, str(identity['pid']), record.get('workspace'))
                    for oid in operations:
                        self.operation_targets.setdefault(oid, []).append(row)
                    if record.get('uncertain'):
                        unresolved[path] = record
                    elif identity_exited(record.get('owner', {})):
                        self.finished_launches.append((path, record, row))
                elif 'identity' not in record:
                    unresolved[path] = record
            except RECORD_ERRORS:
                self.issue(path)
        return unresolved

    def registered_services(self):
        for path in (data_root() / 'services').glob('*.json'):
            try:
                record = object_record(path)
                if record is None:
                    continue
                if record['registry_id'] != path.stem:
                    raise KitError('Invalid service registration')
                origin = record['origin']
                self.process('service', record['identity'], origin['name'], origin.get('workspace'))
                self.workspace(origin)
                for reference in record.get('references', []):
                    self.workspace(reference)
            except RECORD_ERRORS:
                self.issue(path)

    def registered_workspaces(self):
        from . import workspace_index
        hints, problems = workspace_index.known()
        for hint in hints:
            self.workspace(hint)
        for problem in problems:
            self.issue(problem.get('file', 'workspace-index'), problem['reason'])
        for path in (data_root() / 'tray').glob('session-*/projects.json'):
            try:
                for entry in (object_record(path) or {}).values():
                    try:
                        self.workspace(entry)
                    except RECORD_ERRORS:
                        self.issue(path)
            except RECORD_ERRORS:
                self.issue(path)
        for root in dict.fromkeys(self.workspaces.values()):
            state = root / '.echo-kit'
            try:
                if not state.resolve().is_relative_to(root):
                    raise KitError('Registration outside workspace')
                registrations = sorted(state.iterdir())
            except FileNotFoundError:
                continue
            except RECORD_ERRORS:
                self.issue(state)
                continue
            for path in registrations:
                if not path.name.startswith('services-') or path.suffix != '.json':
                    continue
                try:
                    safe_name(path.stem.removeprefix('services-'))
                    if path.is_symlink():
                        raise KitError('Linked registration')
                    for name, item in (object_record(path) or {}).items():
                        if isinstance(item, dict) and 'registry_id' in item:
                            continue
                        try:
                            identity = item['identity']
                            row = self.process('service', identity, name, root)
                            self.legacy.append((path, name, identity, row))
                        except RECORD_ERRORS:
                            self.issue(path)
                except RECORD_ERRORS:
                    self.issue(path)
            path = state / 'browser.json'
            try:
                if path.is_symlink():
                    raise KitError('Linked registration')
                browser = object_record(path)
                if browser:
                    owner = hashlib.sha256(str(root).encode()).hexdigest()[:20]
                    self.browser(browser, owner, root)
            except RECORD_ERRORS:
                self.issue(path)
            native, problems = workspace_index.native_records({'workspace': str(root), 'environment': 'local'})
            for problem in problems:
                self.issue(problem.get('file', root / '.echo-kit/native-browsers'), problem['reason'], root)
            for record in native:
                try:
                    self.native_browser(record, root)
                except RECORD_ERRORS:
                    self.issue(root / '.echo-kit/native-browsers', 'native_browser_ownership_unconfirmed', root)

    def operations(self, journal):
        unfinished = []
        for path in journal.root.glob('*.json'):
            try:
                record = journal._read(path.stem)
                self.workspace(record)
                if record['state'] == 'finished' and not record['held']:
                    continue
                unfinished.append(record)
                for identity in record.get('children', []):
                    try:
                        row = self.process('task', identity, str(identity['pid']), record.get('workspace'))
                        self.operation_targets.setdefault(record['id'], []).append(row)
                    except RECORD_ERRORS:
                        self.issue(path)
            except RECORD_ERRORS:
                self.issue(path)
        return unfinished

    def verify_processes(self):
        waiting = []
        for row, targets in self.waiting:
            try:
                if any(alive(process) for process in targets):
                    waiting.append((row, targets))
                else:
                    row['status'] = 'stopped'
            except psutil.Error:
                row.update(status='unconfirmed', reason='process_exit_unconfirmed')
        self.waiting = waiting

    def retire_legacy(self):
        for path, name, identity, row in self.legacy:
            if row['status'] not in ('stopped', 'already_exited'):
                continue
            try:
                with FileLock(str(path) + '.lock', timeout=0):
                    record = object_record(path) or {}
                    item = record.get(name, {})
                    if 'registry_id' not in item and item.get('identity') == identity:
                        del record[name]
                        save(path, record)
            except (Timeout, *RECORD_ERRORS):
                self.issue(path, 'legacy_registration_not_retired')

    def retire_abandoned_launches(self):
        for path, record, row in self.finished_launches:
            if row['status'] in ('stopped', 'already_exited'):
                try:
                    if object_record(path) == record:
                        path.unlink(missing_ok=True)
                except RECORD_ERRORS:
                    self.issue(path, 'launch_registration_not_retired')


def stop_registered_browser(state, owner):
    """Undo a browser start that returned after its launch generation was cancelled."""
    sweep = Sweep()
    try:
        sweep.browser(state, owner, None)
        deadline = time.monotonic() + VERIFY_SECONDS
        stop_containers(sweep.browsers.values(), deadline)
        while sweep.waiting and time.monotonic() < deadline:
            sweep.verify_processes()
            if sweep.waiting:
                time.sleep(.05)
        return not sweep.waiting and all(row['status'] in ('stopped', 'already_exited') for row in sweep.targets)
    except RECORD_ERRORS:
        return False


def stop_all():
    if sys.platform != 'win32' and not sys.platform.startswith('linux'):
        return {'status': 'unverified', 'reason': 'unsupported_platform'}
    launches.directory().mkdir(parents=True, exist_ok=True)
    lock = launches.stop_lock()
    try:
        lock.acquire()
    except Timeout:
        return {'status': 'blocked', 'reason': 'emergency_in_progress'}
    generation = uuid.uuid4().hex
    try:
        launches.set_gate(generation, True)
        sweep = Sweep()
        # Look at launch records before their final registries so a just-published
        # child is visible on one side of the handoff.
        pending = sweep.pending(list(launches.pending_files()))
        sweep.registered_services()
        journal = Journal()
        unfinished = sweep.operations(journal)
        sweep.registered_workspaces()
        deadline = time.monotonic() + VERIFY_SECONDS
        with ThreadPoolExecutor(max_workers=1) as workers:
            containers = workers.submit(stop_containers, list(sweep.browsers.values()), deadline)
            while True:
                pending = sweep.pending(pending)
                sweep.verify_processes()
                if (not pending and not sweep.waiting and containers.done()) or time.monotonic() >= deadline:
                    break
                time.sleep(min(.05, max(0, deadline - time.monotonic())))
            containers.result()
        for row, _ in sweep.waiting:
            row.update(status='unconfirmed', reason='process_exit_unconfirmed')
        uncertain_operations = {oid for oid, rows in sweep.operation_targets.items()
                                if any(row['status'] == 'unconfirmed' for row in rows)}
        for path, record in pending.items():
            sweep.issue(path, record.get('uncertain', 'launch_in_flight'), record.get('workspace'))
            uncertain_operations.update(record.get('operations', []))
        sweep.retire_legacy()
        sweep.retire_abandoned_launches()
        unresolved = journal.release_after_emergency(unfinished, uncertain_operations)
        for record in unfinished:
            if (not record.get('launch_generation') and not identity_exited(record['identity'])
                    and record['action'] in ('services.up', 'services.restart', 'browser.up', 'lab.run', 'verify.run')):
                try:
                    if journal._read(record['id'])['state'] != 'finished':
                        sweep.issue(record['id'], 'older_launch_unconfirmed', record.get('workspace'))
                except RECORD_ERRORS:
                    sweep.issue(record['id'])
        counts = {state: sum(row['status'] == state for row in sweep.targets)
                  for state in ('stopped', 'already_exited', 'unconfirmed')}
        return {'status': 'unverified' if counts['unconfirmed'] else 'passed', 'counts': counts,
                'targets': sweep.targets, 'pending_operations': [
                    {'id': record['id'], 'run_id': record.get('run_id'), 'workspace': record['workspace']}
                    for record in unresolved]}
    finally:
        try:
            launches.set_gate(generation, False)
        finally:
            lock.release()
