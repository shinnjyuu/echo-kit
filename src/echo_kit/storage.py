"""Explicit disk accounting, frozen reclamation plans and local keep markers."""
import fnmatch
import hmac
import json
import math
import os
import shutil
import stat
import subprocess
import time
import uuid
from contextlib import ExitStack
from pathlib import Path

from filelock import FileLock

from . import __version__, launches, workspace_index
from .catalogs import page
from .core import KitError, Workspace, read, save, safe_name
from .operations import Journal, data_root
from .processes import identity_exited
from .resource_usage import format_memory, registry_records
from .service_registry import Registry
from .storage_config import canonical, roots as configured_roots, token
from .storage_fs import UnsafeStorage, identity, linked, plain_chain, remove, same, read_metadata, child_names, file_identity
from .storage_metadata import KEPT, OWNER, RECLAMATION

ERRORS = (OSError, ValueError, TypeError, KeyError, RuntimeError, KitError)
MAX_ENTRIES = 200_000
SCAN_SECONDS = 30


def home():
    return data_root() / 'storage'


def lock():
    home().mkdir(parents=True, exist_ok=True)
    return FileLock(str(home() / 'metadata.lock'), timeout=5)


def _object(path, limit=2 * 1024 * 1024):
    try:
        info = Path(path).lstat()
    except FileNotFoundError:
        return {}
    if linked(info) or not stat.S_ISREG(info.st_mode) or info.st_size > limit:
        raise UnsafeStorage('unreadable_storage_metadata')
    value = read(path)
    if not isinstance(value, dict):
        raise UnsafeStorage('invalid_storage_metadata')
    return value


def _private_file(folder, identifier):
    if not isinstance(identifier, str) or len(identifier) != 32 or any(c not in '0123456789abcdef' for c in identifier):
        raise KitError('Invalid storage identifier')
    return home() / folder / (identifier + '.json')


def _config_digest(ws):
    return Registry().digest(json.dumps(ws.cfg, sort_keys=True, ensure_ascii=False, default=str))


def _workspace(path, environment):
    ws = Workspace(path)
    return ws if ws.environment == environment else Workspace(path, environment)


def _inside(path, parent):
    return path == parent or path.is_relative_to(parent)


class Budget:
    def __init__(self, maximum, seconds, progress=None):
        if type(maximum) is not int or maximum < 1 or not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or seconds <= 0:
            raise KitError('Storage scan limits must be positive')
        self.maximum, self.deadline, self.count = maximum, time.monotonic() + seconds, 0
        self.progress, self.last_progress = progress, 0

    def take(self):
        self.count += 1
        self.check()
        now = time.monotonic()
        if self.progress and now - self.last_progress >= 1:
            self.progress(self.count)
            self.last_progress = now

    def check(self):
        if self.count > self.maximum or time.monotonic() >= self.deadline:
            raise UnsafeStorage('storage_scan_limit')


class GitInventory:
    def __init__(self):
        self.cache = {}

    def tracked(self, root):
        key = canonical(root)
        if key in self.cache:
            return self.cache[key]
        parent = Path(root)
        while not parent.is_dir() and parent != parent.parent:
            parent = parent.parent
        def run(*args):
            return subprocess.run(['git', '-C', str(parent), *args], capture_output=True,
                                  timeout=5, **({'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}))
        try:
            repo = run('rev-parse', '--show-toplevel')
            if repo.returncode:
                if any((p / '.git').exists() or ((p / 'HEAD').exists() and (p / 'objects').is_dir())
                       for p in (parent, *parent.parents)):
                    raise OSError('Git state unavailable')
                value = set()
            else:
                base = Path(os.fsdecode(repo.stdout.strip())).resolve()
                files = run('--literal-pathspecs', 'ls-files', '--full-name', '-z', '--', str(Path(root)))
                if files.returncode:
                    raise OSError('Git file state unavailable')
                value = {canonical(base / os.fsdecode(p)) for p in files.stdout.split(b'\0') if p}
        except (OSError, ValueError, subprocess.TimeoutExpired):
            value = None
        self.cache[key] = value
        return value


def _reference(root, ws):
    return {**root, 'config_digest': _config_digest(ws)}


class Scan:
    def __init__(self, ws=None, *, all_workspaces=False, root_ids=(), max_entries=MAX_ENTRIES,
                 timeout=SCAN_SECONDS, progress=None, ignore_operations=()):
        self.ws, self.all = ws, all_workspaces
        self.budget = Budget(max_entries, timeout, progress)
        self.root_ids = set(root_ids)
        self.roots, self.units, self.diagnostics = [], [], []
        self.workspaces, self.raw_roots = {}, []
        self.git, self.seen_files = GitInventory(), set()
        self.pending_workspaces, self.protected_paths = set(), set()
        self.uncertain = False
        try:
            self.operations = [o for o in Journal().list(unfinished=True) if o['id'] not in ignore_operations]
        except ERRORS:
            self.operations = []
            self.uncertain = True
            self.diagnostics.append({'reason': 'operation_state_unverified'})
        self.records, issues = registry_records()
        self.diagnostics.extend(issues)
        if issues:
            self.uncertain = True
        for record in self.records:
            try:
                if identity_exited(record['identity']):
                    continue
                paths = [record.get('log'), *[a['path'] for a in record.get('artifacts', [])]]
                self.protected_paths.update(canonical(p) for p in paths if p)
            except ERRORS:
                self.uncertain = True
                self.diagnostics.append({'reason': 'service_evidence_unverified'})
        with lock():
            self.pins = _object(home() / 'keep.json', 16 * 1024 * 1024)

    def load(self):
        references = []
        for record in self.records:
            references.extend([record.get('origin', {}), *record.get('references', [])])
        hints, problems = workspace_index.known(references)
        if self.all:
            self.diagnostics.extend(problems)
        specs = [(self.ws.root, self.ws.environment)] if self.ws else []
        specs += [(Path(h['workspace']), h['environment']) for h in hints]
        loaded = set()
        for path, environment in specs:
            try:
                self.budget.check()
                key = (canonical(path), environment)
                if key in loaded:
                    continue
                loaded.add(key)
                ws = self.ws if self.ws and key == (canonical(self.ws.root), self.ws.environment) else _workspace(path, environment)
                self.workspaces[key] = ws
                selected = self.all or (self.ws and key == (canonical(self.ws.root), self.ws.environment))
                for root in configured_roots(ws):
                    self.raw_roots.append((_reference(root, ws), selected))
                self._legacy_services(ws)
                # Even a scoped query must merge other environments' protection
                # rules when they point at the same physical root; only selection
                # remains scoped. Payloads in unselected roots are not scanned.
                specs.extend((ws.root, name) for name in {ws.cfg.get('default_environment', 'local'),
                                                         *ws.cfg.get('environments', {})})
            except ERRORS:
                if (Path(path) / 'echo-kit.toml').exists():
                    self.uncertain = True
                if self.all or (self.ws and canonical(path) == canonical(self.ws.root)):
                    self.diagnostics.append({'workspace': str(path), 'reason': 'storage_configuration_unverified'})
        if self.ws:
            workspace_index.remember(self.ws)
        grouped = {}
        for root, selected in self.raw_roots:
            try:
                path = plain_chain(root['path'])
                if path == Path(path.anchor) or path == Path.home().resolve() or path == Path(root['workspace']):
                    raise UnsafeStorage('unsafe_storage_root')
                if root['kind'] == 'cache' and any(_inside(w.root, path) for w in self.workspaces.values()):
                    raise UnsafeStorage('workspace_inside_storage_cache_root')
                if _inside(path, data_root().resolve()) or any(part.casefold() == '.git' for part in path.parts):
                    raise UnsafeStorage('unsafe_storage_root')
                info = path.lstat() if path.exists() else None
                if info and not stat.S_ISDIR(info.st_mode):
                    raise UnsafeStorage('storage_root_not_directory')
                physical = (info.st_dev, info.st_ino) if info else ('missing', canonical(path))
                group = grouped.setdefault(physical, {'id': root['id'], 'kind': root['kind'], 'path': str(path),
                    'name': root['name'], 'references': [], 'selected': False, 'diagnostics': [],
                    'selection': [], 'identity': identity(info) if info else None})
                group['references'].append(root)
                group['selected'] |= selected
                if selected:
                    group['selection'].append([canonical(root['workspace']), root['environment']])
                if group['kind'] != root['kind']:
                    group['diagnostics'].append('storage_root_kind_conflict')
            except ERRORS:
                if selected:
                    self.diagnostics.append({'root_id': root['id'], 'path': root['path'], 'reason': 'storage_root_unverified'})
        groups = list(grouped.values())
        for index, root in enumerate(groups):
            a = Path(root['path'])
            for other in groups[index + 1:]:
                b = Path(other['path'])
                if _inside(a, b) or _inside(b, a):
                    root['diagnostics'].append('overlapping_storage_roots')
                    other['diagnostics'].append('overlapping_storage_roots')
        self.roots = [r for r in groups if r['selected'] and (not self.root_ids or r['id'] in self.root_ids)]
        if self.root_ids - {r['id'] for r in self.roots}:
            raise KitError('Unknown or unverified storage root ID')
        # A cache-only preview still honors pending business state outside its
        # requested root. Inspect only small run metadata, never those payloads.
        if any(r['kind'] == 'cache' for r in self.roots):
            selected_roots = {r['id'] for r in self.roots}
            for root in groups:
                if root['kind'] != 'runs' or root['id'] in selected_roots or root['identity'] is None:
                    continue
                try:
                    for name in child_names(root['path'], '.', root['identity'], {}):
                        self.budget.take()
                        path = Path(root['path']) / name
                        plain_chain(path)
                        if not path.is_dir():
                            continue
                        directory = {name: identity(path.lstat())}
                        record = read_metadata(root['path'], name + '/run.json', root['identity'], directory)
                        if (path / 'active.json').exists() or record.get('cleanup') == 'unconfirmed':
                            self.pending_workspaces.update(canonical(r['workspace']) for r in root['references'])
                except ERRORS:
                    self.uncertain = True
                    self.diagnostics.append({'root_id': root['id'], 'reason': 'pending_business_state_unverified'})

    def _legacy_services(self, ws):
        file = ws.state / ('services-' + safe_name(ws.environment) + '.json')
        try:
            for record in _object(file).values():
                if not isinstance(record, dict):
                    raise ValueError('Invalid service registration')
                if record.get('registry_id') or record.get('mode') == 'external':
                    continue
                if not record.get('identity'):
                    if record.get('pid'):
                        self.uncertain = True
                    continue
                if identity_exited(record['identity']):
                    continue
                paths = [record.get('log'), *[a['path'] for a in record.get('artifacts', [])]]
                self.protected_paths.update(canonical(p) for p in paths if p)
        except ERRORS:
            self.uncertain = True
            self.diagnostics.append({'reason': 'legacy_service_evidence_unverified', 'workspace': str(ws.root)})

    def _control_paths(self, root):
        result = set(self.protected_paths)
        for ref in root['references']:
            ws = self.workspaces[(canonical(ref['workspace']), ref['environment'])]
            result.update(canonical(ws.root / p['path']) for p in ws.configuration.files)
            result.update(canonical(p) for p in (ws.root / 'README.md', ws.root / 'AGENTS.md',
                          ws.root / 'echo/README.md', ws.state / 'private/README.md'))
            for value in ws.cfg.get('documents', {}).values():
                if isinstance(value, str):
                    result.add(canonical(ws.root / value))
        return result

    def _metadata(self, root, path, unit):
        plain_chain(path)
        info = path.lstat()
        if stat.S_ISDIR(info.st_mode):
            unit['_directories'][path.relative_to(root['path']).as_posix()] = identity(info)
        if root['kind'] == 'cache':
            # A copied Kit workspace is not a generic disposable cache unit.
            if path.is_dir() and any((path / name).exists() for name in ('echo-kit.toml', OWNER, 'run.json', 'session.json')):
                unit['reasons'].append('project_or_kit_evidence_in_cache')
            unit['group'] = root['id']
            return
        primary = 'run.json' if root['kind'] == 'runs' else 'session.json'
        record = read_metadata(root['path'], (path / primary).relative_to(root['path']), root['identity'], unit['_directories'])
        owner = read_metadata(root['path'], (path / OWNER).relative_to(root['path']), root['identity'], unit['_directories'])
        if not record:
            raise UnsafeStorage('missing_evidence_metadata')
        environments = {r['environment'] for r in root['references']}
        environment = owner.get('environment') or record.get('environment')
        if environment is None and root['kind'] == 'observations':
            environment = next(iter(environments)) if len(environments) == 1 else None
        if environment not in environments:
            if not self.all and self.ws and environment in {self.ws.cfg.get('default_environment', 'local'),
                                                           *self.ws.cfg.get('environments', {})}:
                unit['_outside_scope'] = True
                return
            raise UnsafeStorage('evidence_environment_unverified')
        op_id = owner.get('operation_id') or record.get('operation_id')
        if owner:
            if (owner.get('format') != 1 or owner.get('id') != path.name or owner.get('kind') != root['kind']
                    or canonical(owner.get('workspace', '')) not in {canonical(r['workspace']) for r in root['references']}):
                raise UnsafeStorage('evidence_owner_mismatch')
            if not owner.get('ended'):
                unit['reasons'].append('evidence_writer_not_finished')
        else:
            if not op_id:
                raise UnsafeStorage('legacy_evidence_owner_unverified')
            operation = Journal()._read(safe_name(op_id))
            if (canonical(operation.get('evidence', '')) != canonical(path)
                    or canonical(operation['workspace']) not in {canonical(r['workspace']) for r in root['references']}):
                raise UnsafeStorage('legacy_evidence_owner_unverified')
            if operation['state'] != 'finished' or operation['held'] or operation.get('requires_resolution'):
                unit['reasons'].append('unfinished_evidence_operation')
            if operation.get('result') == 'manually_resolved' and record.get('cleanup') not in ('confirmed', 'confirmed after run'):
                unit['reasons'].append('resolved_occupancy_is_not_cleanup_evidence')
        workspace = owner.get('workspace') if owner else operation['workspace']
        if [canonical(workspace), environment] not in root['selection']:
            unit['_outside_scope'] = True
            return
        status = record.get('status')
        if status not in ('passed', 'failed', 'blocked', 'unverified', 'interrupted', 'closed', 'emergency_stopped',
                          'starting', 'running', 'ready', 'unconfirmed'):
            raise UnsafeStorage('evidence_status_unverified')
        if status in ('starting', 'running', 'ready', 'unconfirmed'):
            unit['reasons'].append('evidence_not_terminal')
        if root['kind'] == 'observations' and status not in ('closed', 'emergency_stopped'):
            unit['reasons'].append('observation_not_closed')
        if (path / 'active.json').exists() or record.get('cleanup') == 'unconfirmed':
            unit['reasons'].append('business_cleanup_unconfirmed')
            self.pending_workspaces.update(canonical(r['workspace']) for r in root['references'])
        if op_id and owner:
            try:
                operation = Journal()._read(safe_name(op_id))
                if operation.get('result') == 'manually_resolved' and record.get('cleanup') not in ('confirmed', 'confirmed after run'):
                    unit['reasons'].append('resolved_occupancy_is_not_cleanup_evidence')
            except KitError:
                # New ownership includes a terminal marker independently of the
                # operation journal. Missing historical journal is not fabricated.
                if root['kind'] == 'runs':
                    unit['reasons'].append('evidence_operation_unverified')
        unit.update(operation_id=op_id, status=status, environment=environment,
                    workspace=workspace,
                    group=[canonical(workspace), record.get('case'), record.get('variant'), environment]
                    if root['kind'] == 'runs' else [canonical(workspace), environment])
        unit['_ended'] = owner.get('ended') or record.get('finished') or (path / primary).stat().st_mtime
        if not isinstance(unit['_ended'], (int, float)) or not math.isfinite(unit['_ended']):
            raise UnsafeStorage('invalid_evidence_end_time')

    def _walk(self, root, path, unit):
        controls = self._control_paths(root)
        tracked = self.git.tracked(Path(root['path']))
        if tracked is None:
            unit['reasons'].append('git_state_unverified')
        def visit(file):
            self.budget.check()  # Each child was counted by its bounded enumeration.
            info = file.lstat()
            relative = file.relative_to(Path(root['path'])).as_posix()
            if linked(info):
                unit['reasons'].append('linked_storage_entry')
                return
            if file.name.startswith('.echo-kit-reclaim-'):
                unit['reasons'].append('unconfirmed_staging_entry')
                return
            if canonical(file) in controls or (tracked is not None and canonical(file) in tracked):
                unit['reasons'].append('protected_or_tracked_file')
            if file.name in ('active', 'active.json'):
                unit['reasons'].append('active_marker')
            if file.name.lower() in ('.git', 'agents.md', 'agents.override.md', 'echo-kit.toml'):
                unit['reasons'].append('project_control_file')
                if file.name.lower() == '.git':
                    return
            if stat.S_ISDIR(info.st_mode):
                unit['_directories'][relative] = identity(info)
                names = []
                for name in child_names(root['path'], relative, root['identity'], unit['_directories']):
                    self.budget.take()
                    names.append(name)
                for name in sorted(names):
                    visit(file / name)
                if not same(file.lstat(), identity(info), content=True):
                    raise UnsafeStorage('directory_changed_during_scan')
            elif stat.S_ISREG(info.st_mode):
                kept = root['kind'] != 'cache' and file.parent == path and file.name in KEPT
                entry = {'path': relative, 'identity': file_identity(file, info), 'kept': kept}
                unit['_files'].append(entry)
                physical = (info.st_dev, info.st_ino)
                if physical not in self.seen_files:
                    unit['logical_bytes'] += info.st_size
                    self.seen_files.add(physical)
                if not kept:
                    unit['payload_bytes'] += info.st_size
                    unit['latest_modified'] = max(unit['latest_modified'], info.st_mtime)
                if info.st_nlink != 1:
                    unit['reasons'].append('multiply_linked_file')
            else:
                unit['reasons'].append('special_storage_entry')
        visit(path)
        unit['file_count'] = len(unit['_files'])
        unit['latest_modified'] = max(unit['latest_modified'], unit.get('_ended', 0))

    def _policy(self, root, unit):
        if self.uncertain:
            unit['reasons'].append('resource_state_unverified')
        if unit['id'] in self.pins:
            unit['reasons'].append('marked_keep')
        path = Path(unit['path'])
        key = 'storage.unit:' + canonical(path)
        cache_key = 'storage.root:' + canonical(root['path'])
        workspaces = {canonical(r['workspace']) for r in root['references']}
        for op in self.operations:
            evidence = Path(op['evidence']) if op.get('evidence') else None
            associated = (op['id'] == unit.get('operation_id') or
                          (op.get('run_id') == path.name and canonical(op['workspace']) in workspaces))
            if (associated or key in op['held'] or (evidence and (_inside(evidence, path) or _inside(path, evidence)))):
                unit['reasons'].append('active_or_pending_operation')
            if (canonical(op['workspace']) in workspaces and not any(k.startswith('storage.') for k in op['held'])):
                unit['reasons'].append('noncooperating_operation_in_workspace')
            if root['kind'] == 'cache' and (cache_key in op['held'] or canonical(op['workspace']) in workspaces):
                unit['reasons'].append('cache_in_use_or_legacy_operation')
        if root['kind'] == 'cache' and workspaces.intersection(self.pending_workspaces):
            unit['reasons'].append('business_cleanup_unconfirmed')
        days = []
        for ref in self._policies(root, unit):
            policy = ref['policy']
            if root['kind'] == 'cache':
                if not any(fnmatch.fnmatch(Path(unit['path']).name, p) for p in policy['include']):
                    unit['reasons'].append('excluded_by_reference')
                days.append(policy['keep_days'])
            elif root['kind'] == 'runs':
                days.append(policy['passed_days'] if unit.get('status') == 'passed' else policy['other_days'])
            else:
                days.append(policy['keep_days'])
        if unit['latest_modified'] > time.time() - max(days, default=0) * 86400:
            unit['reasons'].append('within_retention_days')

    @staticmethod
    def _policies(root, unit):
        if root['kind'] == 'cache':
            return root['references']
        return [r for r in root['references'] if r['environment'] == unit.get('environment')
                and canonical(r['workspace']) == canonical(unit.get('workspace', ''))]

    def run(self):
        self.load()
        for root in self.roots:
            try:
                self.budget.check()
                if root['diagnostics'] or root['identity'] is None:
                    continue
                path = Path(root['path'])
                children = []
                for name in child_names(root['path'], '.', root['identity'], {}):
                    self.budget.take()
                    children.append(name)
                for name in sorted(children):
                    if name.startswith('.echo-kit-reclaim-'):
                        self.diagnostics.append({'root_id': root['id'], 'reason': 'unconfirmed_staging_entry'})
                        continue
                    if root['kind'] == 'cache' and not any(any(fnmatch.fnmatch(name, p) for p in r['policy']['include']) for r in root['references']):
                        continue
                    unit = {'id': token([root['id'], os.path.normcase(name)]), 'root_id': root['id'],
                            'kind': root['kind'], 'name': name, 'path': str(path / name),
                            'logical_bytes': 0, 'payload_bytes': 0, 'file_count': 0, 'latest_modified': 0,
                            'reasons': [], '_files': [], '_directories': {}, 'complete': False}
                    self.units.append(unit)
                    try:
                        self._metadata(root, path / name, unit)
                        if unit.get('_outside_scope'):
                            self.units.pop()
                            continue
                        self._walk(root, path / name, unit)
                        unit['complete'] = True
                    except ERRORS as error:
                        reason = str(error) if isinstance(error, UnsafeStorage) else 'storage_unit_unverified'
                        unit['reasons'].append(reason)
                    self.budget.check()
            except ERRORS as error:
                root['diagnostics'].append(str(error) if isinstance(error, UnsafeStorage) else 'storage_root_scan_unverified')
        by_root = {r['id']: r for r in self.roots}
        for unit in self.units:
            if by_root[unit['root_id']]['diagnostics']:
                unit['reasons'].append('root_scan_incomplete_or_unverified')
            self._policy(by_root[unit['root_id']], unit)
        groups = {}
        for unit in self.units:
            if unit['complete']:
                groups.setdefault((unit['root_id'], json.dumps(unit.get('group'))), []).append(unit)
        for (root_id, _), units in groups.items():
            count = max((r['policy']['keep_latest'] for r in self._policies(by_root[root_id], units[0])), default=0)
            for unit in sorted(units, key=lambda u: (-u['latest_modified'], u['id']))[:count]:
                unit['reasons'].append('latest_entries_kept')
        for unit in self.units:
            unit['reasons'] = sorted(set(unit['reasons']))
            unit['eligible'] = unit['complete'] and not unit['reasons'] and any(not f['kept'] for f in unit['_files'])
        self.units.sort(key=lambda u: (-u['logical_bytes'], u['id']))
        for root in self.roots:
            self.diagnostics.extend({'root_id': root['id'], 'reason': r} for r in root['diagnostics'])
        self._catalog()
        return self

    def _catalog(self):
        if not self.units:
            return
        roots_by_id = {r['id']: r for r in self.roots}
        with lock():
            catalog = _object(home() / 'catalog.json', 64 * 1024 * 1024)
            for unit in self.units:
                root = roots_by_id[unit['root_id']]
                catalog[unit['id']] = {'root_id': root['id'], 'path': unit['path'],
                                      'references': [{k: r[k] for k in ('workspace', 'environment')} for r in root['references']]}
            save(home() / 'catalog.json', catalog)


def _public_unit(unit):
    return {k: v for k, v in unit.items() if not k.startswith('_')}


def describe(scan, limit=20, offset=0):
    units = [_public_unit(u) for u in scan.units]
    incomplete = scan.diagnostics or any(not u['complete'] or any(r.endswith('unverified') or r == 'storage_scan_limit' for r in u['reasons']) for u in units)
    return {**page(units, limit, offset), 'status': 'unverified' if incomplete else 'passed', 'mode': 'disk',
            'tool_version': __version__, 'scope': 'all_known_workspaces' if scan.all else 'workspace',
            'size_basis': 'deduplicated_logical_bytes_not_guaranteed_free_space',
            'roots': [{k: r[k] for k in ('id', 'name', 'kind', 'path', 'references')} for r in scan.roots],
            'summary': {'logical_bytes': sum(u['logical_bytes'] for u in units),
                        'candidate_bytes': sum(u['payload_bytes'] for u in units if u['eligible']),
                        'candidate_units': sum(u['eligible'] for u in units), 'scanned_entries': scan.budget.count},
            'diagnostics': scan.diagnostics}


def disk(ws=None, *, all_workspaces=False, root_ids=(), limit=20, offset=0,
         max_entries=MAX_ENTRIES, timeout=SCAN_SECONDS, progress=None):
    page([], limit, offset)
    return describe(Scan(ws, all_workspaces=all_workspaces, root_ids=root_ids,
                         max_entries=max_entries, timeout=timeout, progress=progress).run(), limit, offset)


def preview(ws=None, *, all_workspaces=False, root_ids=(), limit=20, offset=0,
            max_entries=MAX_ENTRIES, timeout=SCAN_SECONDS, progress=None):
    page([], limit, offset)
    scan = Scan(ws, all_workspaces=all_workspaces, root_ids=root_ids,
                max_entries=max_entries, timeout=timeout, progress=progress).run()
    identifier = uuid.uuid4().hex
    plan = {'format': 1, 'id': identifier, 'version': __version__, 'created': time.time(),
            'roots': scan.roots, 'units': [u for u in scan.units if u['eligible']],
            'max_entries': max_entries, 'timeout': timeout}
    with lock():
        save(_private_file('plans', identifier), {'plan': plan, 'signature': Registry().digest(plan)})
    return {**describe(scan, limit, offset), 'plan_id': identifier, 'mode': 'preview',
            'next_step': 'resources clean --apply ' + identifier,
            'note': 'Preview only. Logical bytes do not guarantee an equal increase in free disk space.'}


def _coordination(root_id):
    # Keeps pins and reclamation mutually exclusive without holding the metadata
    # transaction lock during a filesystem scan or deletion.
    _private_file('roots', root_id)
    directory = home() / 'locks'
    directory.mkdir(parents=True, exist_ok=True)
    return FileLock(str(directory / (root_id + '.lock')), timeout=5)


def keep(identifier, remove_mark=False):
    _private_file('units', identifier)  # Validate the opaque identifier only.
    with lock():
        entry = _object(home() / 'catalog.json', 64 * 1024 * 1024).get(identifier)
    if not entry:
        raise KitError('Unknown storage unit; run resources disk first')
    with _coordination(entry['root_id']):
        # Pins never confer deletion authority. A stale catalog can retain a pin
        # safely; unpinning also works after a unit has already disappeared.
        with lock():
            pins = _object(home() / 'keep.json', 16 * 1024 * 1024)
            if remove_mark:
                pins.pop(identifier, None)
            else:
                pins[identifier] = {'time': time.time(), 'root_id': entry['root_id']}
            save(home() / 'keep.json', pins)
    return {'status': 'passed', 'unit_id': identifier, 'kept': not remove_mark,
            'note': 'Existing previews are rechecked before execution.'}


def _snapshot(unit):
    return {k: unit.get(k) for k in ('path', 'kind', '_files', '_directories', '_ended', 'operation_id')}


def _root_signature(root):
    return {'path': root['path'], 'kind': root['kind'],
            'identity': {k: root['identity'][k] for k in ('dev', 'ino', 'mode', 'attributes')} if root['identity'] else None,
            'references': sorted(root['references'], key=lambda r: (r['workspace'], r['environment'], r['id']))}


def _space(roots):
    values = {}
    for root in roots:
        path = Path(root['path'])
        try:
            while not path.exists() and path != path.parent:
                path = path.parent
            key = str(path.stat().st_dev)
            values[key] = shutil.disk_usage(path).free
        except OSError:
            values[str(path.anchor)] = None
    return values


def _event(identifier, value):
    # Append-only small processing evidence; do not rewrite an ever-growing list
    # after each file or persist any payload/credential content.
    file = home() / 'receipts' / (identifier + '.events.jsonl')
    file.parent.mkdir(parents=True, exist_ok=True)
    with file.open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(value, ensure_ascii=False) + '\n')
        stream.flush()


def _delete_unit(plan, root, unit):
    path = Path(unit['path'])
    counts = {'deleted_files': 0, 'deleted_logical_bytes': 0, 'already_missing': 0}
    issues = []
    marker = {'state': 'in_progress', 'plan_id': plan['id'], 'time': time.time(), **counts}
    if root['kind'] != 'cache':
        # Keep marker updates anchored to the same verified directory as the
        # deletions. No payload is read or recursively removed.
        from .storage_fs import write_metadata
        write_metadata(root['path'], path.relative_to(root['path']) / RECLAMATION,
                       root['identity'], unit['_directories'], marker)
    files = [f for f in unit['_files'] if not f['kept']]
    interrupted = False
    try:
        for entry in files:
            launches.checkpoint()
            try:
                result, retained = remove(root['path'], entry, root['identity'], unit['_directories'])
                if result == 'deleted':
                    counts['deleted_files'] += 1
                    counts['deleted_logical_bytes'] += entry['identity']['size']
                elif result == 'already_missing':
                    counts['already_missing'] += 1
                else:
                    issues.append({'path': entry['path'], 'status': 'unconfirmed', 'retained': retained})
                _event(plan['id'], {'unit_id': unit['id'], 'path': entry['path'], 'status': result,
                                    'logical_bytes': entry['identity']['size'] if result == 'deleted' else 0})
            except ERRORS as error:
                issues.append({'path': entry['path'], 'status': 'skipped' if isinstance(error, UnsafeStorage) else 'failed',
                               'reason': str(error) if isinstance(error, UnsafeStorage) else type(error).__name__})
        # Remove only frozen empty directories, deepest first; never the root.
        for relative, expected in sorted(unit['_directories'].items(), key=lambda pair: len(Path(pair[0]).parts), reverse=True):
            launches.checkpoint()
            if root['kind'] != 'cache' and relative == path.relative_to(root['path']).as_posix():
                continue
            try:
                result, retained = remove(root['path'], {'path': relative, 'identity': expected},
                                          root['identity'], unit['_directories'])
                if result == 'unconfirmed':
                    issues.append({'path': relative, 'status': result, 'retained': retained})
            except ERRORS as error:
                issues.append({'path': relative, 'status': 'skipped', 'reason': type(error).__name__})
    except (KeyboardInterrupt, launches.EmergencyInterrupted):
        interrupted = True
    marker.update(state='partial' if issues or interrupted else 'reclaimed', time=time.time(), **counts)
    if root['kind'] != 'cache':
        try:
            write_metadata(root['path'], path.relative_to(root['path']) / RECLAMATION,
                           root['identity'], unit['_directories'], marker)
            reports = [f for f in unit['_files'] if f['kept'] and f['path'] == (path.relative_to(root['path']) / 'report.html').as_posix()]
            if reports:
                from .storage_fs import annotate_report
                annotate_report(root['path'], reports[0], root['identity'], unit['_directories'], marker['state'] != 'reclaimed')
        except ERRORS:
            issues.append({'status': 'unconfirmed', 'reason': 'reclamation_marker_not_updated'})
    return {'unit_id': unit['id'], 'status': 'interrupted' if interrupted else 'partial' if issues else 'deleted',
            **counts, 'diagnostics': issues}


def apply(identifier, unit_ids=(), *, limit=20, offset=0, actor=None):
    page([], limit, offset)
    file = _private_file('plans', identifier)
    value = _object(file, 128 * 1024 * 1024)
    plan = value.get('plan')
    if not isinstance(plan, dict) or plan.get('format') != 1 or plan.get('id') != identifier or not hmac.compare_digest(
            str(value.get('signature', '')), Registry().digest(plan)):
        raise KitError('Invalid or changed reclamation plan; generate a new preview')
    selected = set(unit_ids) or {u['id'] for u in plan['units']}
    if selected - {u['id'] for u in plan['units']}:
        raise KitError('Unit is not a deletion candidate in this plan')
    receipt_file = _private_file('receipts', identifier)
    with FileLock(str(file) + '.lock', timeout=1):
        if receipt_file.exists():
            raise KitError('This plan has already been attempted; preview the remaining content again')
        results, before = [], _space(plan['roots'])
        receipt = {'plan_id': identifier, 'status': 'in_progress', 'started': time.time(), 'items': results}
        save(receipt_file, receipt)
        journal, leases = Journal(), []
        try:
            with ExitStack() as guards:
                roots_by_id = {r['id']: r for r in plan['roots']}
                root_ids = sorted({u['root_id'] for u in plan['units'] if u['id'] in selected})
                for rid in root_ids:
                    guards.enter_context(_coordination(rid))
                accepted = []
                for unit in plan['units']:
                    if unit['id'] not in selected:
                        continue
                    root = roots_by_id[unit['root_id']]
                    ref = root['references'][0]
                    try:
                        ws = _workspace(ref['workspace'], ref['environment'])
                        ws.actor = actor
                        keys = {'storage.unit:' + canonical(unit['path']): 'exclusive'}
                        if root['kind'] == 'cache':
                            keys = {'storage.root:' + canonical(root['path']): 'exclusive'}
                        # One cache lease covers all its selected units.
                        if root['kind'] == 'cache' and any(rid == root['id'] for _, rid in leases):
                            accepted.append(unit)
                            continue
                        oid = journal.acquire(ws, 'resources.clean', keys)
                        leases.append((oid, root['id']))
                        accepted.append(unit)
                    except ERRORS:
                        results.append({'unit_id': unit['id'], 'status': 'skipped', 'reason': 'configuration_or_occupancy_changed'})
                fresh = Scan(all_workspaces=True, root_ids=root_ids, max_entries=plan['max_entries'],
                             timeout=plan['timeout'], ignore_operations={oid for oid, _ in leases}).run() if accepted else None
                current_roots = {r['id']: r for r in fresh.roots} if fresh else {}
                current_units = {u['id']: u for u in fresh.units} if fresh else {}
                for unit in accepted:
                    launches.checkpoint()
                    root = roots_by_id[unit['root_id']]
                    current = current_units.get(unit['id'])
                    now_root = current_roots.get(root['id'])
                    # Root directory timestamps change as other units are removed;
                    # identity and configuration, not its mutable size/time, bind it.
                    root_ok = now_root is not None and _root_signature(root) == _root_signature(now_root)
                    if root_ok and current is None:
                        try:
                            plain_chain(unit['path']).lstat()
                        except FileNotFoundError:
                            results.append({'unit_id': unit['id'], 'status': 'already_missing', 'already_missing': len(
                                [f for f in unit['_files'] if not f['kept']])})
                            continue
                    if (not root_ok or current is None or not current['eligible'] or _snapshot(current) != _snapshot(unit)):
                        results.append({'unit_id': unit['id'], 'status': 'skipped', 'reason': 'changed_or_protected_repreview',
                                        'protection': current.get('reasons', []) if current else []})
                        continue
                    # Units already absent are a successful no-op only when their
                    # root/configuration was still verifiable; never expand scope.
                    try:
                        result = _delete_unit(plan, root, unit)
                    except ERRORS as error:
                        result = {'unit_id': unit['id'], 'status': 'failed', 'reason': 'unit_execution_unverified',
                                  'error_type': type(error).__name__}
                    results.append(result)
                    receipt.update(items=results)
                    save(receipt_file, receipt)
                    if result['status'] == 'interrupted':
                        break
        except (KeyboardInterrupt, launches.EmergencyInterrupted):
            results.append({'status': 'interrupted', 'reason': 'interrupted_repreview_remaining'})
        except ERRORS as error:
            results.append({'status': 'unconfirmed', 'reason': 'execution_unverified', 'error_type': type(error).__name__})
        finally:
            for oid, _ in reversed(leases):
                try:
                    journal.release(oid)
                except ERRORS:
                    results.append({'status': 'unconfirmed', 'operation_id': oid, 'reason': 'storage_lease_release_unverified'})
        done = {r.get('unit_id') for r in results}
        results.extend({'unit_id': u, 'status': 'skipped', 'reason': 'not_executed_repreview'} for u in sorted(selected - done))
        after = _space(plan['roots'])
        summary = {k: sum(r.get(k, 0) for r in results) for k in ('deleted_files', 'deleted_logical_bytes', 'already_missing')}
        status = 'interrupted' if any(r['status'] == 'interrupted' for r in results) else 'unverified' if any(
            r['status'] not in ('deleted', 'already_missing') for r in results) else 'passed'
        receipt.update(status=status, finished=time.time(), items=results, summary=summary,
                       free_space_observation={dev: {'before': amount, 'after': after.get(dev),
                           'change': after[dev] - amount if amount is not None and after.get(dev) is not None else None}
                           for dev, amount in before.items()})
        save(receipt_file, receipt)
        return {**page(results, limit, offset), **{k: v for k, v in receipt.items() if k != 'items'}, 'tool_version': __version__,
                'receipt': str(receipt_file), 'note': 'No rollback or automatic retry. Re-preview remaining content.'}


def render(result):
    if 'kept' in result:
        return 'Keep mark ' + ('set: ' if result['kept'] else 'removed: ') + result['unit_id']
    lines = [f"Disk {result.get('mode', 'reclamation')}: {result['status']}"]
    summary = result.get('summary', {})
    if 'logical_bytes' in summary:
        lines.append(f"Observed {format_memory(summary['logical_bytes'])}; candidates {format_memory(summary['candidate_bytes'])} (logical size)")
    else:
        lines.append(f"Deleted {summary.get('deleted_files', 0)} files / {format_memory(summary.get('deleted_logical_bytes', 0))} logical bytes")
    for root in result.get('roots', []):
        lines.append(f"Root {root['id']}  {root['name']}  {root['path']}")
    for item in result.get('items', []):
        size = format_memory(item['logical_bytes']) + '  ' if 'logical_bytes' in item else ''
        lines.append('  ' + item.get('id', item.get('unit_id', '')) + '  ' + size + item.get('path', '') + '  '
                     + (', '.join(item.get('reasons', [])) or item.get('status', 'candidate' if item.get('eligible') else 'retained')))
    if result.get('plan_id'):
        lines.append('Plan: ' + result['plan_id'])
    if result.get('next_step'):
        lines.append(result['next_step'])
    if result.get('diagnostics'):
        lines.append('Some ranges were not verified; see --json diagnostics.')
    return '\n'.join(lines)
