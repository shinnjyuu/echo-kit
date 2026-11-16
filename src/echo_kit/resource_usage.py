"""Read-only, bounded resource inventory and sampling. No application probes."""
import json
import math
import os
import re
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import psutil

from . import __version__, docker_host, workspace_index
from .catalogs import page
from .core import KitError, read
from .operations import Journal, canonical_path, data_root, endpoint
from .processes import process_owner, observed_process as verified_process, parent_snapshot
from .service_registry import Registry, key as service_key

CPU_INTERVAL = .25
DOCKER_BUDGET = 3
STALE_SECONDS = 15
_ERRORS = (OSError, ValueError, TypeError, KeyError, KitError)


def issue(reason, **fields):
    return {'reason': reason, **fields}


def base(kind, identity, name, references=()):
    return {'id': identity, 'kind': kind, 'name': name, 'instance_id': None,
            'state': 'unknown', 'memory_bytes': None, 'cpu_percent': None,
            'process_count': None, 'container_pids': None, 'references': list(references),
            'occupants': [], 'diagnostics': [], '_keys': []}


def registry_records():
    records, diagnostics = [], []
    for path in sorted((data_root() / 'services').glob('*.json')):
        try:
            record = read(path)
            if (not isinstance(record, dict) or not isinstance(record.get('identity'), dict)
                    or record.get('registry_id') != path.stem):
                raise ValueError('Invalid service record')
            records.append(record)
        except _ERRORS:
            diagnostics.append(issue('unreadable_service_registration', file=str(path)))
    return records, diagnostics


def discover(ws=None, names=None, *, operations=None, records=None):
    """ws=None means all known workspaces. Never instantiate their configurations."""
    items, diagnostics, hints = {}, [], []
    occupancy_verified = True
    selected = list(dict.fromkeys(names or []))
    if ws:
        specs = ws.cfg.get('services', {})
        for name in selected:
            if name not in specs:
                raise KitError('Unknown service: ' + name)
        selected = selected or list(specs)
    if operations is None:
        try:
            operations = Journal().list(unfinished=True)
        except _ERRORS:
            operations = []
            occupancy_verified = False
            diagnostics.append(issue('operation_occupancy_unverified'))

    def service(record, identity, name, references=()):
        if (not isinstance(name, str) or not isinstance(record.get('instance_id', identity), str)
                or not isinstance(record.get('identity'), dict) or not isinstance(references, list)
                or any(not isinstance(r, dict) or any(not isinstance(r.get(k), str)
                       for k in ('workspace', 'environment', 'name')) for r in references)
                or not isinstance(record.get('resource_keys', []), list)
                or any(not isinstance(k, str) for k in record.get('resource_keys', []))):
            raise ValueError('Invalid service registration metadata')
        item = items.setdefault('service:' + identity, base('service', 'service:' + identity, name, references))
        item['instance_id'] = record.get('instance_id', identity)
        item['_identity'] = record.get('identity')
        # An instance's saved resource set includes dependencies. Match its own
        # identity so an operation using only a dependency is not called its user.
        item['_keys'] = ['instance:' + identity]
        for ref in references:
            if ref not in item['references']:
                item['references'].append(ref)
        return item

    def state_file(entry, selected_names=None):
        root = Path(entry['workspace'])
        file = Path(entry['services_file'])
        try:
            if not file.resolve().is_relative_to(root):
                raise ValueError('Linked registration')
            state = read(file, {})
            if not isinstance(state, dict):
                raise ValueError('Invalid registration')
            for name, record in state.items():
                if selected_names is not None and name not in selected_names:
                    continue
                if not isinstance(record, dict):
                    raise ValueError('Invalid registration')
                if 'registry_id' in record:
                    continue
                identity = f"legacy:{canonical_path(root)}:{entry['environment']}:{name}"
                ref = {'workspace': str(root), 'environment': entry['environment'], 'name': name}
                item = service(record, identity, name, [ref])
                item['legacy'] = True
                item['_legacy_occupancy_unknown'] = ws is None
                # Legacy operations used endpoint keys as well as instance keys.
                if ws:
                    spec = ws.cfg['services'].get(name, {})
                    ready = spec.get('ready', {})
                    if spec.get('resource_id'):
                        item['_keys'].append('service:' + spec['resource_id'])
                    if spec.get('port') or ready.get('port'):
                        item['_keys'].append(endpoint(host=ready.get('host'), port=spec.get('port') or ready['port']))
                    if ready.get('url'):
                        item['_keys'].append(endpoint(url=ready['url']))
        except _ERRORS:
            diagnostics.append(issue('unreadable_service_registration', file=str(file)))

    if ws:
        entry = workspace_index.validate(workspace_index.entry(ws))
        hints = [entry]
        state_file(entry, selected)
        for name in selected:
            spec = specs[name]
            rid = service_key(ws, name)
            if spec.get('mode', 'managed') == 'external':
                item = base('service', 'external:' + rid, name)
                item['state'] = 'external'
                items[item['id']] = item
                continue
            if any(i.get('legacy') and i['name'] == name for i in items.values()):
                continue
            try:
                record = Registry().get(rid)
                ref = {'workspace': str(ws.root), 'environment': ws.environment, 'name': name}
                if record is None:
                    item = base('service', 'service:' + rid, name, [ref])
                    item.update(state='not_registered', memory_bytes=0, cpu_percent=0, process_count=0)
                    items[item['id']] = item
                elif (isinstance(record, dict) and isinstance(record.get('identity'), dict)
                      and record.get('registry_id') == rid):
                    service(record, rid, name, record.get('references', [ref]))
                else:
                    raise ValueError('Invalid record')
            except _ERRORS:
                item = base('service', 'service:' + rid, name)
                item['diagnostics'].append(issue('unreadable_service_registration'))
                items[item['id']] = item
    else:
        if records is None:
            records, problems = registry_records()
            diagnostics.extend(problems)
            for problem in problems:
                identity = 'service:' + Path(problem['file']).stem
                item = base('service', identity, 'Unreadable service registration')
                item['diagnostics'].append(problem)
                items[identity] = item
        references = []
        for record in records:
            try:
                rid = record['registry_id']
                Registry().file(rid)  # Validate the identifier without writing.
                refs = record['references']
                if not isinstance(refs, list):
                    raise ValueError('Invalid references')
                references.extend([record['origin'], *refs])
                service(record, rid, record.get('resource_id') or record['origin']['name'], refs)
            except _ERRORS:
                diagnostics.append(issue('invalid_service_registration'))
        hints, problems = workspace_index.known(references)
        diagnostics.extend(problems)
        for hint in hints:
            state_file(hint)

    if not names:
        seen_native = set()
        for hint in hints:
            native_directory = hint['native_browsers_dir']
            if native_directory not in seen_native:
                seen_native.add(native_directory)
                native, problems = workspace_index.native_records(hint)
                diagnostics.extend(problems)
                for record in native:
                    if ws and record['environment'] != ws.environment:
                        continue
                    sid = record['session_id']
                    identity = 'native-browser:' + hint['owner'] + ':' + sid
                    reference = {'workspace': record['workspace'], 'environment': record['environment'], 'name': sid}
                    item = base('native_browser', identity, Path(record['workspace']).name + ' / ' + sid, [reference])
                    item.update(backend='agent-browser', session_id=sid, instance_id=record['instance_id'],
                                state=workspace_index.native_state(record), _identity=record['identity'],
                                _identities=[record['identity'], *record['children']], _keys=[identity])
                    if item['state'] == 'unknown':
                        item['diagnostics'].append(issue('native_browser_identity_unverified'))
                    items[identity] = item
            file = Path(hint['browser_file'])
            if hint.get('browser_managed') is False and not file.exists():
                item = base('browser', 'external-browser:' + canonical_path(hint['workspace']), 'browser')
                item['state'] = 'external'
                items[item['id']] = item
                continue
            try:
                if not file.resolve().is_relative_to(Path(hint['workspace'])):
                    raise ValueError('Linked browser registration')
                state = read(file, {})
                if not isinstance(state, dict):
                    raise ValueError('Invalid browser registration')
                if not state:
                    continue
                cid = state['id']
                if not isinstance(cid, str) or not re.fullmatch('[0-9a-f]{64}', cid):
                    raise ValueError('Invalid container ID')
                host = state.get('docker_host') or None
                identity = 'browser:' + str(host) + ':' + cid
                ref = {'workspace': hint['workspace'], 'environment': hint['environment'], 'name': 'browser'}
                item = items.setdefault(identity, base('browser', identity, Path(hint['workspace']).name + ' / browser'))
                if ref not in item['references']:
                    item['references'].append(ref)
                item.update(instance_id=cid, _docker_host=host, _owner=hint['owner'])
                item['_keys'] = ['container:' + cid]
                for field in ('endpoint', 'viewer_url'):
                    if state.get(field):
                        item['_keys'].append(endpoint(url=state[field]))
            except _ERRORS:
                problem = issue('unreadable_browser_registration', file=str(file))
                identity = 'browser-registration:' + canonical_path(hint['workspace'])
                item = base('browser', identity, Path(hint['workspace']).name + ' / browser')
                item['diagnostics'].append(problem)
                items[identity] = item

    for item in items.values():
        item['occupancy_verified'] = occupancy_verified and not item.get('_legacy_occupancy_unknown', False)
        if not item['occupancy_verified']:
            item['diagnostics'].append(issue('operation_occupancy_unverified'))
        keys = set(item['_keys'])
        for op in operations:
            if keys.intersection(op['held']):
                if (any(not isinstance(op.get(k), str) for k in ('id', 'workspace', 'environment', 'action', 'state'))
                        or any(op.get(k) is not None and not isinstance(op[k], str) for k in ('actor', 'run_id'))):
                    item['occupancy_verified'] = False
                    item['diagnostics'].append(issue('operation_occupancy_unverified'))
                    continue
                item['occupants'].append({k: op.get(k) for k in
                    ('id', 'actor', 'workspace', 'environment', 'action', 'state', 'run_id', 'owner_alive', 'children_alive')})
    return {'items': list(items.values()), 'diagnostics': diagnostics,
            'scope': {'kind': 'workspace' if ws else 'all_known_workspaces',
                      'workspace': str(ws.root) if ws else None,
                      'environment': ws.environment if ws else None, 'services': names or None}}


def native_frame(items):
    """One read per unique process; nearest registered root owns nested children."""
    frame = {'time': time.monotonic(), 'values': {}, 'members': {}, 'states': {}, 'problems': {}}
    roots, processes, parents = {}, {}, {}
    owner = process_owner(psutil.Process())
    for item in items:
        if item['kind'] not in ('service', 'native_browser') or '_identity' not in item:
            continue
        iid = item['id']
        frame['members'][iid], frame['problems'][iid] = set(), []
        frame['states'][iid] = 'stopped'
        for identity_record in item.get('_identities', [item['_identity']]):
            try:
                root = verified_process(identity_record)
                if root is None:
                    continue
                if process_owner(root) != owner:
                    raise KitError('Different owner')
                identity = (root.pid, root.create_time())
                # Register children too: they remain discoverable after an owner crash.
                roots.setdefault(identity, iid)
                processes[identity] = root
                if frame['states'][iid] != 'unknown':
                    frame['states'][iid] = 'running'
            except psutil.NoSuchProcess:
                if frame['states'][iid] == 'running':
                    frame['problems'][iid].append(issue('process_tree_changed'))
            except (psutil.Error, *_ERRORS):
                frame['states'][iid] = 'unknown'
                frame['problems'][iid].append(issue('process_identity_or_tree_unverified'))
        if item['kind'] == 'native_browser' and item['state'] == 'unknown':
            frame['states'][iid] = 'unknown'
            frame['problems'][iid].append(issue('native_browser_identity_unverified'))
    parent_pids = {}
    if roots:
        try:
            parent_pids = parent_snapshot()
            for identity, target in roots.items():
                if identity[0] not in parent_pids:
                    frame['problems'][target].append(issue('process_parent_snapshot_incomplete'))
            children = {}
            for pid, ppid in parent_pids.items():
                children.setdefault(ppid, []).append(pid)
            by_pid = {key[0]: key for key in processes}
            pending = list(by_pid)
            seen = set()
            while pending:
                pid = pending.pop()
                if pid in seen:
                    continue
                seen.add(pid)
                for child_pid in children.get(pid, []):
                    try:
                        if child_pid not in by_pid:
                            child = psutil.Process(child_pid)
                            identity = (child_pid, child.create_time())
                            if identity[1] < by_pid[pid][1]:
                                raise KitError('Parent PID reused')
                            processes[identity] = child
                            by_pid[child_pid] = identity
                        pending.append(child_pid)
                    except (psutil.Error, *_ERRORS):
                        for target in frame['problems']:
                            frame['problems'][target].append(issue('process_tree_changed'))
            parents = {key: by_pid.get(parent_pids.get(key[0])) for key in processes}
        except (psutil.Error, *_ERRORS):
            for target in frame['problems']:
                frame['problems'][target].append(issue('process_parent_snapshot_unavailable'))
    for key, process in processes.items():
        ancestor, seen = key, set()
        while ancestor not in roots and ancestor is not None and ancestor not in seen:
            seen.add(ancestor)
            ancestor = parents.get(ancestor)
        iid = roots.get(ancestor)
        if iid is None:
            for target in frame['problems']:
                frame['problems'][target].append(issue('process_parent_unverified'))
            continue
        try:
            with process.oneshot():
                if process_owner(process) != owner or (os.name != 'nt' and process.status() == psutil.STATUS_ZOMBIE):
                    raise KitError('Process unavailable')
                memory = process.memory_info().rss
                cpu = process.cpu_times()
                value = {'rss': memory, 'cpu': cpu.user + cpu.system}
            if not process.is_running() or psutil.Process(key[0]).create_time() != key[1]:
                raise KitError('PID changed during sample')
            frame['values'][key] = value
            frame['members'][iid].add(key)
        except (psutil.Error, *_ERRORS):
            frame['problems'][iid].append(issue('process_sample_incomplete'))
    for item in items:
        iid = item['id']
        if frame['states'].get(iid) == 'running':
            try:
                if not any(verified_process(identity) is not None
                           for identity in item.get('_identities', [item['_identity']])):
                    raise KitError('Root exited during sample')
            except (psutil.Error, *_ERRORS):
                frame['states'][iid] = 'unknown'
                frame['problems'][iid].append(issue('process_identity_changed_during_sample'))
    return frame


def _docker(args, host, deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError('Docker sampling budget exceeded')
    argv, options = docker_host.command(args, host)
    result = subprocess.run(argv, capture_output=True, text=True, encoding='utf-8',
                            timeout=remaining, **options)
    if result.returncode and not (args[0] == 'inspect' and result.stdout.lstrip().startswith('[')):
        raise OSError('Docker sampling command failed')
    return result.stdout


def memory_bytes(value):
    match = re.fullmatch(r'\s*([\d.]+)\s*(B|kB|KB|MB|GB|TB|KiB|MiB|GiB|TiB)\s*', value)
    if not match:
        raise ValueError('Invalid Docker memory unit')
    units = {'B': 1, 'kB': 1000, 'KB': 1000, 'MB': 1000**2, 'GB': 1000**3, 'TB': 1000**4,
             'KiB': 1024, 'MiB': 1024**2, 'GiB': 1024**3, 'TiB': 1024**4}
    number = float(match[1])
    if not math.isfinite(number) or number < 0:
        raise ValueError('Invalid Docker memory')
    return int(number * units[match[2]])


def docker_sample(items):
    result, groups = {}, {}
    deadline = time.monotonic() + DOCKER_BUDGET
    for item in items:
        if item['kind'] == 'browser' and item.get('instance_id'):
            groups.setdefault(item['_docker_host'], []).append(item)
    for host, targets in groups.items():
        try:
            if host is None:
                host = docker_host.selected_host(timeout=max(.01, min(1, deadline - time.monotonic())))
            if not docker_host.is_local(host):
                raise ValueError('Nonlocal Docker transport')
            containers = json.loads(_docker(['inspect', *[i['instance_id'] for i in targets]], host, deadline))
            containers = {c['Id']: c for c in containers}
            running = []
            for item in targets:
                cid = item['instance_id']
                container = containers.get(cid)
                config = container.get('Config') if isinstance(container, dict) else None
                labels = config.get('Labels') if isinstance(config, dict) else None
                if not isinstance(labels, dict) or labels.get('echo-kit.workspace') != item['_owner']:
                    result[item['id']] = {'state': 'unknown', 'diagnostics': [issue('container_ownership_unverified')]}
                    continue
                state = container['State']['Status']
                result[item['id']] = {'state': state, 'diagnostics': []}
                if state in ('exited', 'dead', 'created'):
                    result[item['id']].update(memory_bytes=0, cpu_percent=0, container_pids=0)
                else:
                    running.append(item)
            if running:
                output = _docker(['stats', '--no-stream', '--no-trunc', '--format', '{{json .}}',
                                  *[i['instance_id'] for i in running]], host, deadline)
                stats = {s['ID']: s for s in (json.loads(line) for line in output.splitlines() if line)}
                for item in running:
                    try:
                        stat = stats[item['instance_id']]
                        cpu = float(stat['CPUPerc'].rstrip('%'))
                        if not math.isfinite(cpu) or cpu < 0:
                            raise ValueError('Invalid CPU')
                        pids = str(stat.get('PIDs', ''))
                        result[item['id']].update(memory_bytes=memory_bytes(stat['MemUsage'].split('/')[0]),
                                                 cpu_percent=cpu, container_pids=int(pids) if pids.isdigit() else None)
                    except _ERRORS:
                        result[item['id']]['diagnostics'].append(issue('container_stats_incomplete'))
        except (subprocess.TimeoutExpired, TimeoutError, *_ERRORS):
            for item in targets:
                previous = result.get(item['id'], {})
                if previous.get('memory_bytes') is None:
                    result[item['id']] = {**previous, 'diagnostics': [issue('docker_stats_unavailable')]}
    return result


class Sampler:
    def __init__(self):
        self.previous = None

    def sample(self, inventory, *, wait=True):
        started = time.monotonic()
        items = inventory['items']
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(docker_sample, items) if any(i['kind'] == 'browser' and i.get('instance_id') for i in items) else None
            previous = native_frame(items) if wait else self.previous
            if wait and previous['values']:
                time.sleep(CPU_INTERVAL)
            current = native_frame(items)
            self.previous = current
            containers = future.result() if future else {}
        for item in items:
            iid = item['id']
            if item['kind'] == 'browser':
                item['memory_basis'] = 'docker_cli'
                item['cpu_basis'] = 'docker_cli'
                if iid in containers:
                    item.update({k: v for k, v in containers[iid].items() if k != 'diagnostics'})
                    item['diagnostics'].extend(containers[iid]['diagnostics'])
            else:
                item['memory_basis'] = 'rss_working_set'
                item['cpu_basis'] = 'one_core_100_percent'
                if iid in current['states']:
                    item['state'] = current['states'][iid]
                    item['diagnostics'].extend(current['problems'][iid])
                    members = current['members'][iid]
                    if not current['problems'][iid]:
                        item['memory_bytes'] = sum(current['values'][p]['rss'] for p in members)
                        item['process_count'] = len(members)
                        if item['state'] == 'stopped':
                            item['cpu_percent'] = 0
                        elif (previous and iid in previous['members'] and previous['members'][iid] == members
                              and not previous['problems'][iid] and current['time'] > previous['time']):
                            delta = sum(current['values'][p]['cpu'] - previous['values'][p]['cpu'] for p in members)
                            if delta >= 0:
                                item['cpu_percent'] = round(delta / (current['time'] - previous['time']) * 100, 2)
                        if item['cpu_percent'] is None:
                            item['diagnostics'].append(issue('cpu_interval_unavailable'))
            item['verified'] = not item['diagnostics'] and (item['state'] == 'external' or item['memory_bytes'] is not None)
        public = [{k: v for k, v in item.items() if not k.startswith('_')} for item in items]
        for item in public:
            item['references'] = [{k: ref.get(k) for k in ('workspace', 'environment', 'name')}
                                  for ref in item['references']]
        public.sort(key=lambda i: (i['memory_bytes'] is None, -(i['memory_bytes'] or 0), i['kind'], i['id']))
        summary = {}
        for kind, label in [('service', 'native'), ('browser', 'containers'), ('native_browser', 'native_browsers')]:
            selected = [i for i in public if i['kind'] == kind and i['state'] != 'external']
            known = [i for i in selected if i['memory_bytes'] is not None]
            summary[label] = {'memory_bytes': sum(i['memory_bytes'] for i in known), 'count': len(selected),
                              'measured_count': len(known), 'unknown_count': len(selected) - len(known)}
        return {'status': 'unverified' if inventory['diagnostics'] or any(not i['verified'] for i in public) else 'passed',
                'tool_version': __version__, 'scope': inventory['scope'], 'sampled_at': time.time(),
                'sample_interval_ms': round((current['time'] - previous['time']) * 1000, 2) if previous else None,
                'elapsed_ms': round((time.monotonic() - started) * 1000, 2),
                'summary': summary, 'items': public, 'diagnostics': inventory['diagnostics']}


def status(ws=None, names=None, limit=20, offset=0):
    page([], limit, offset)
    result = Sampler().sample(discover(ws, names))
    return {**result, **{k: v for k, v in page(result['items'], limit, offset).items() if k != 'status'}}


def format_memory(value):
    if value is None:
        return '未知'
    for unit in ('B', 'KiB', 'MiB', 'GiB', 'TiB'):
        if value < 1024 or unit == 'TiB':
            return f'{value:.1f} {unit}'
        value /= 1024


def summary_text(result, now=None):
    if result is None:
        return ['资源用量：等待采样']
    age = max(0, (time.time() if now is None else now) - result['sampled_at'])
    lines = ['资源用量' + ('（旧数据）' if age > STALE_SECONDS else '') + f' · {int(age)} 秒前']
    if result['status'] != 'passed':
        lines.append('部分指标未验证；请查看 CLI 诊断')
    for key, label in [('native', '服务 RSS 观测合计'), ('native_browsers', '原生浏览器 RSS 观测合计'), ('containers', '容器内存')]:
        value = result['summary'].get(key, {'memory_bytes': 0, 'measured_count': 0, 'count': 0, 'unknown_count': 0})
        text = format_memory(value['memory_bytes']) if value['measured_count'] or not value['count'] else '未知'
        lines.append(label + '：' + text + (f"；{value['unknown_count']} 项未知" if value['unknown_count'] else ''))
    return lines


def render(result):
    lines = summary_text(result)
    lines.append('名称 | 状态 | 内存 | CPU（单核100%／容器口径） | 进程数／容器PIDS | 引用 / 占用')
    for item in result['items']:
        cpu = '未知' if item['cpu_percent'] is None else f"{item['cpu_percent']:.1f}%"
        count = item['container_pids'] if item['kind'] == 'browser' else item['process_count']
        refs = ', '.join(f"{r['workspace']} [{r['environment']}]" for r in item['references']) or '—'
        occupants = ', '.join(f"{o['id']} ({o.get('actor') or o['action']}, {o['state']})" for o in item['occupants'])
        occupants = occupants or ('无 Kit 占用' if item.get('occupancy_verified', True) else '占用状态未知')
        lines.append(f"{item['name']} | {item['state']} | {format_memory(item['memory_bytes'])} | {cpu} | "
                     f"{count if count is not None else '未知'} | {refs} / {occupants}")
        if item['diagnostics']:
            lines.append('  ' + ', '.join(p['reason'] for p in item['diagnostics']))
    lines.append(f"共 {result['total']} 项；汇总覆盖完整查询范围。RSS 合计不代表可释放内存；无 Kit 占用不代表无人使用。")
    if result['next_offset'] is not None:
        lines.append(result['next_step'])
    for problem in result['diagnostics']:
        lines.append(problem['reason'])
    return '\n'.join(lines)


class BackgroundUsage:
    """One bounded worker; never perform I/O from a menu callback or queue rounds."""
    def __init__(self):
        self.lock, self.wake, self.done = threading.Lock(), threading.Event(), threading.Event()
        self.pending, self.busy, self.latest = None, False, None
        self.sampler = Sampler()
        self.thread = threading.Thread(target=self._run, name='tray-resources', daemon=True)

    def start(self):
        self.thread.start()

    def request(self, operations, records):
        with self.lock:
            if self.busy or self.pending is not None or self.done.is_set():
                return False
            self.pending = (operations, records)
        self.wake.set()
        return True

    def snapshot(self):
        with self.lock:
            return self.latest

    def close(self):
        self.done.set()
        self.wake.set()

    def _run(self):
        while not self.done.is_set():
            self.wake.wait()
            self.wake.clear()
            with self.lock:
                if self.done.is_set():
                    return
                operations, records = self.pending
                self.pending, self.busy = None, True
            try:
                result = self.sampler.sample(discover(operations=operations, records=records), wait=False)
                with self.lock:
                    self.latest = result
            except Exception:
                # Keep the previous timestamp so menus honestly mark stale data.
                with self.lock:
                    if self.latest is None:
                        self.latest = {'status': 'unverified', 'sampled_at': 0, 'items': [],
                                       'summary': {k: {'count': 0, 'measured_count': 0, 'unknown_count': 0,
                                                       'memory_bytes': None} for k in ('native', 'containers')},
                                       'diagnostics': [issue('resource_sampling_unavailable')]}
            finally:
                with self.lock:
                    self.busy = False
