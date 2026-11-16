import copy
import json
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import psutil
import pytest

from echo_kit import cli, resource_usage as usage, workspace_index
from echo_kit.core import KitError, Workspace, init_workspace, read, save
from echo_kit.operations import Journal, data_root
from echo_kit.processes import fingerprint
from echo_kit.service_registry import Registry, key


@pytest.fixture
def ws(tmp_path):
    init_workspace(tmp_path / '项目 空间')
    ws = Workspace(tmp_path / '项目 空间')
    ws.cfg['services'] = {name: {'resource_id': name, 'command': [sys.executable, '-c', 'pass']}
                          for name in ('api', 'worker')}
    return ws


def register(ws, name='api', pid=None):
    return Registry().create(ws, name, {'identity': fingerprint(pid or os.getpid())})


def inventory(*items):
    return {'items': list(items), 'scope': {'kind': 'test'}, 'diagnostics': []}


def native(name='api', pid=101):
    item = usage.base('service', name, name)
    item['_identity'] = {'pid': pid, 'created': 1.0}
    return item


class Process:
    def __init__(self, pid, parent=0, rss=100, cpu=1.0):
        self.pid, self.parent, self.rss, self.cpu = pid, parent, rss, cpu
        self.descendants = []
    def create_time(self): return 1.0
    def ppid(self): return self.parent
    def children(self, recursive): return self.descendants
    def oneshot(self): return self
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def status(self): return 'running'
    def memory_info(self): return SimpleNamespace(rss=self.rss)
    def cpu_times(self): return SimpleNamespace(user=self.cpu, system=0)
    def is_running(self): return True


def fake_processes(monkeypatch, processes):
    mapping = {p.pid: p for p in processes}
    monkeypatch.setattr(usage.psutil, 'Process', lambda pid=None: mapping[pid or processes[0].pid])
    monkeypatch.setattr(usage, 'process_owner', lambda p: 'current-user')
    monkeypatch.setattr(usage, 'verified_process', lambda identity: mapping[identity['pid']])
    monkeypatch.setattr(usage, 'parent_snapshot', lambda: {p.pid: p.parent for p in processes})


def test_single_global_cpu_wait_and_nearest_registered_root(monkeypatch):
    parent, nested, child = Process(101, rss=10), Process(102, parent=101, rss=20), Process(103, parent=102, rss=30)
    parent.descendants = [nested, child]
    nested.descendants = [child]
    fake_processes(monkeypatch, [parent, nested, child])
    clock, waits = [10.0], []
    snapshots = []
    def parent_map():
        snapshots.append(True)
        return {p.pid: p.parent for p in (parent, nested, child)}
    monkeypatch.setattr(usage, 'parent_snapshot', parent_map)
    monkeypatch.setattr(usage.time, 'monotonic', lambda: clock[0])
    def sleep(interval):
        waits.append(interval)
        clock[0] += interval
        for p in (parent, nested, child): p.cpu += .25
    monkeypatch.setattr(usage.time, 'sleep', sleep)
    result = usage.Sampler().sample(inventory(native('parent'), native('nested', 102)))
    rows = {i['name']: i for i in result['items']}
    assert waits == [.25]
    assert len(snapshots) == 2
    assert rows['parent']['memory_bytes'] == 10 and rows['parent']['process_count'] == 1
    assert rows['nested']['memory_bytes'] == 50 and rows['nested']['process_count'] == 2
    assert rows['parent']['cpu_percent'] == 100 and rows['nested']['cpu_percent'] == 200
    assert result['summary']['native']['memory_bytes'] == 60
    assert result['status'] == 'passed'


def test_tray_cpu_first_unknown_then_interval_without_sleep(monkeypatch):
    process = Process(101)
    fake_processes(monkeypatch, [process])
    clock = [0.0]
    monkeypatch.setattr(usage.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(usage.time, 'sleep', lambda _: pytest.fail('tray must not sleep for CPU'))
    sampler = usage.Sampler()
    first = sampler.sample(inventory(native()), wait=False)
    assert first['items'][0]['cpu_percent'] is None
    assert first['items'][0]['memory_bytes'] == 100
    clock[0], process.cpu = 5.0, 3.5
    second = sampler.sample(inventory(native()), wait=False)
    assert second['items'][0]['cpu_percent'] == 50
    assert second['status'] == 'passed'


def test_unknown_identity_preserves_other_metrics(monkeypatch):
    process = Process(101)
    fake_processes(monkeypatch, [process])
    def verify(identity):
        if identity['pid'] == 102: raise psutil.AccessDenied(102)
        return process
    monkeypatch.setattr(usage, 'verified_process', verify)
    result = usage.Sampler().sample(inventory(native(), native('denied', 102)))
    rows = {i['name']: i for i in result['items']}
    assert rows['denied']['memory_bytes'] is None
    assert rows['api']['memory_bytes'] == 100
    assert result['status'] == 'unverified'
    assert result['summary']['native']['unknown_count'] == 1


def test_pid_reuse_and_child_sampling_failure(monkeypatch):
    process, child = Process(101), Process(102, parent=101)
    process.descendants = [child]
    fake_processes(monkeypatch, [process, child])
    child.create_time = lambda: 2.0
    frame = usage.native_frame([native()])
    assert frame['members']['api'] == {(101, 1.0), (102, 2.0)}
    child.is_running = lambda: False
    result = usage.Sampler().sample(inventory(native()))
    assert result['items'][0]['memory_bytes'] is None
    assert result['status'] == 'unverified'


def test_shared_instance_aliases_global_read_only_and_private_values(ws, tmp_path, monkeypatch):
    record = register(ws)
    init_workspace(tmp_path / 'second')
    other = Workspace(tmp_path / 'second')
    other.cfg['services'] = {'alias': ws.cfg['services']['api']}
    Registry().reference(other, 'alias', key(ws, 'api'))
    from echo_kit.services import Services
    monkeypatch.setattr(Services, 'status', lambda *a, **k: pytest.fail('no service status/health'))
    before = {p: p.read_bytes() for p in data_root().rglob('*') if p.is_file()}
    found = usage.discover()
    assert len(found['items']) == 1
    assert {r['name'] for r in found['items'][0]['references']} == {'api', 'alias'}
    result = usage.Sampler().sample(found)
    text = json.dumps(result)
    assert 'command_digest' not in text and 'signature' not in text and 'identity' not in text
    assert all(p.read_bytes() == content for p, content in before.items())
    assert result['items'][0]['instance_id'] == record['instance_id']


def test_scope_unknown_names_and_legacy_no_migration(ws, monkeypatch):
    save(ws.state / 'services-local.json', {'api': {'identity': fingerprint(os.getpid())}})
    save(ws.state / 'browser.json', {'id': 'b' * 64, 'docker_host': 'unix:///var/run/docker.sock'})
    before = (ws.state / 'services-local.json').read_bytes()
    with pytest.raises(KitError, match='Unknown service'):
        usage.discover(ws, ['unknown'])
    monkeypatch.setattr(usage, 'docker_sample', lambda _: pytest.fail('selected service must not query browser'))
    result = usage.status(ws, ['api'])
    assert len(result['items']) == 1 and result['items'][0]['legacy']
    assert (ws.state / 'services-local.json').read_bytes() == before
    assert not list((data_root() / 'services').glob('*.json'))


def test_occupancy_without_run_id_and_pending_cleanup(ws):
    register(ws)
    journal = Journal()
    oid = journal.acquire(ws, 'browser-observer', {'instance:' + key(ws, 'api'): 'shared'})
    assert journal.show(oid)['run_id'] is None
    found = usage.discover(ws, ['api'])
    assert found['items'][0]['occupants'][0]['id'] == oid
    assert found['items'][0]['occupants'][0]['state'] == 'running'
    journal.update(oid, requires_resolution=True)
    journal.release(oid)
    found = usage.discover(ws, ['api'])
    assert found['items'][0]['occupants'][0]['state'] == 'needs_cleanup'


def test_index_concurrency_environments_and_untrusted_paths(ws, tmp_path):
    other = copy.copy(ws)
    other.environment = 'development'
    with ThreadPoolExecutor(2) as pool:
        list(pool.map(workspace_index.register, [ws, other]))
    hints, problems = workspace_index.known()
    assert {h['environment'] for h in hints} == {'local', 'development'} and not problems
    value = workspace_index.entry(ws)
    value['services_file'] = str(tmp_path / 'outside-secret.json')
    assert workspace_index.validate(value)['services_file'] == str(ws.state / 'services-local.json')


def browser_item(cid='a' * 64, owner='owner'):
    item = usage.base('browser', cid, 'browser')
    item.update(instance_id=cid, _owner=owner, _docker_host='local-test')
    return item


def docker_fake(monkeypatch, items, *, wrong=False, fail=False):
    calls = []
    monkeypatch.setattr(usage.docker_host, 'is_local', lambda host: host == 'local-test')
    def command(args, host, deadline):
        calls.append(args)
        if args[0] == 'inspect':
            return json.dumps([{'Id': i['instance_id'], 'Config': {'Labels': {'echo-kit.workspace': 'wrong' if wrong else 'owner'}},
                                'State': {'Status': 'running'}} for i in items])
        if fail: raise subprocess_timeout()
        return '\n'.join(json.dumps({'ID': i['instance_id'], 'MemUsage': '2.5MiB / 8GiB', 'CPUPerc': '125.0%', 'PIDs': '12'}) for i in items)
    monkeypatch.setattr(usage, '_docker', command)
    return calls


def subprocess_timeout():
    import subprocess
    return subprocess.TimeoutExpired('docker', 3)


def test_docker_batches_full_ids_and_distinct_metrics(monkeypatch):
    items = [browser_item(), browser_item('b' * 64)]
    calls = docker_fake(monkeypatch, items)
    result = usage.Sampler().sample(inventory(*items))
    assert len(calls) == 2 and all(i['instance_id'] in calls[1] for i in items)
    assert result['summary']['native']['memory_bytes'] == 0
    assert result['summary']['containers']['memory_bytes'] == 5 * 1024**2
    assert all(i['container_pids'] == 12 and i['process_count'] is None for i in result['items'])
    assert result['status'] == 'passed'


@pytest.mark.parametrize('wrong,fail', [(True, False), (False, True)])
def test_docker_ownership_and_failure_keep_native(ws, monkeypatch, wrong, fail):
    items = [browser_item()]
    calls = docker_fake(monkeypatch, items, wrong=wrong, fail=fail)
    register(ws)
    found = usage.discover(ws, ['api'])
    found['items'] += items
    result = usage.Sampler().sample(found)
    assert result['summary']['native']['memory_bytes'] > 0
    assert result['items'][-1]['memory_bytes'] is None and result['status'] == 'unverified'
    assert len(calls) == (1 if wrong else 2)


def test_pagination_corruption_and_summary(ws):
    register(ws)
    save(data_root() / 'services' / ('f' * 64 + '.json'), {'broken': 'secret-not-for-output'})
    result = usage.status(limit=1)
    assert result['status'] == 'unverified' and result['total'] == 2
    assert result['summary']['native']['unknown_count'] == 1
    assert result['summary']['native']['memory_bytes'] > 0
    assert 'secret-not-for-output' not in json.dumps(result)
    assert 'unreadable_service_registration' in json.dumps(result)


def test_background_nonoverlap_menu_read_and_stale(monkeypatch):
    entered, finish, published = threading.Event(), threading.Event(), threading.Event()
    background = usage.BackgroundUsage()
    monkeypatch.setattr(usage, 'discover', lambda **_: inventory())
    def sample(inv, wait):
        entered.set()
        assert finish.wait(3)
        return {'sampled_at': 0, 'status': 'passed', 'summary': {
            'native': {'memory_bytes': 1, 'count': 1, 'measured_count': 1, 'unknown_count': 0},
            'containers': {'memory_bytes': 0, 'count': 0, 'measured_count': 0, 'unknown_count': 0}}}
    monkeypatch.setattr(background.sampler, 'sample', sample)
    background.start()
    try:
        assert background.request([], []) and entered.wait(2)
        assert not background.request([], [])
        assert background.snapshot() is None
        finish.set()
        # Join is synchronized with a second explicit completion hook, not a timing assertion.
        background.close()
        background.thread.join(3)
        assert not background.thread.is_alive()
        assert '旧数据' in usage.summary_text(background.snapshot(), now=16)[0]
    finally:
        finish.set()
        background.close()


@pytest.mark.parametrize('args', [['--workspace', '.', '--json', 'resources', 'status', '--all'],
                                  ['--environment', 'local', '--json', 'resources', 'status', '--all'],
                                  ['--json', 'resources', 'status', 'api', '--all']])
def test_cli_all_rejects_scope(args, capsys):
    assert cli.main(args) == 2
    assert 'cannot be combined' in json.loads(capsys.readouterr().out)['error']


def test_cli_all_no_workspace_single_json_and_table(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, 'Workspace', lambda *a: pytest.fail('--all must not load configuration'))
    assert cli.main(['--json', 'resources', 'status', '--all']) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['total'] == 0 and result['tool_version'] == '0.15.0'
    assert cli.main(['resources', 'status', '--all']) == 0
    assert 'RSS' in capsys.readouterr().out


def test_real_process_tree_and_pid_identity(ws, tmp_path):
    from echo_kit.processes import spawn, stop
    import subprocess
    code = ("import subprocess,sys,os; "
            "p=subprocess.Popen([sys.executable,'-c','import time; x=bytearray(2097152); print(123,flush=True); time.sleep(60)'], "
            "stdout=subprocess.PIPE,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)); "
            "p.stdout.readline(); "
            "print(p.pid,flush=True); sys.stdin.buffer.read()")
    process = spawn([sys.executable, '-u', '-c', code], tmp_path, os.environ.copy(), subprocess.PIPE, subprocess.PIPE)
    identity = fingerprint(process.pid)
    try:
        assert int(process.stdout.readline()) > 1
        expected_count = 1 + len(psutil.Process(process.pid).children(recursive=True))
        register(ws, pid=process.pid)
        result = usage.status(ws, ['api'])
        assert result['status'] == 'passed'
        assert result['items'][0]['process_count'] == expected_count and expected_count >= 2
        assert result['items'][0]['memory_bytes'] > 0
        file = Registry().file(key(ws, 'api'))
        record = read(file)
        record['identity']['created'] -= 1
        save(file, record)
        invalid = usage.status(ws, ['api'])
        assert invalid['status'] == 'unverified' and invalid['items'][0]['memory_bytes'] is None
        assert process.poll() is None  # A read-only identity failure never kills it.
    finally:
        stop(identity)
        process.wait(timeout=5)


def test_large_history_only_describes_unfinished_once(ws, monkeypatch):
    journal = Journal()
    oid = journal.acquire(ws, 'observe', {'instance:' + key(ws, 'api'): 'shared'})
    template = read(journal.file(oid))
    template.update(state='finished', held={}, finished=template['started'])
    for index in range(7200):
        value = {**template, 'id': f'finished-{index}'}
        journal.file(value['id']).write_text(json.dumps(value), encoding='utf-8')
    register(ws)
    described = []
    original = Journal._describe
    def describe(self, record):
        described.append(record['id'])
        return original(self, record)
    monkeypatch.setattr(Journal, '_describe', describe)
    result = usage.status(ws, ['api'])
    assert described == [oid]
    assert result['items'][0]['occupants'][0]['id'] == oid


def test_resource_topics_and_export_from_package(tmp_path):
    from echo_kit import documentation
    for name in ('echo-init', 'echo-lab', 'echo-workbench'):
        for topic in ('services', 'browser'):
            result = documentation.guide(name, topic=topic)
            assert 'Resource usage' in result['protocol']['content']
            assert 'resources status' in result['protocol']['content']
    destination = tmp_path / 'export'
    documentation.export(destination)
    assert 'resources status' in (destination / 'references/protocol.md').read_text(encoding='utf-8')


def test_dependency_occupancy_not_attributed_to_unrelated_parent(ws):
    register(ws, 'api')
    register(ws, 'worker')
    file = Registry().file(key(ws, 'worker'))
    record = read(file)
    record['resource_keys'].append('instance:' + key(ws, 'api'))
    save(file, record)
    oid = Journal().acquire(ws, 'use-api', {'instance:' + key(ws, 'api'): 'shared'})
    found = usage.discover(ws)
    rows = {i['name']: i for i in found['items']}
    assert rows['api']['occupants'][0]['id'] == oid
    assert rows['worker']['occupants'] == []


def test_bad_occupancy_keeps_memory_and_reports_unknown(ws):
    register(ws)
    (Journal().root / 'broken.json').write_text('{broken-secret', encoding='utf-8')
    result = usage.status(ws, ['api'])
    assert result['status'] == 'unverified'
    assert result['items'][0]['memory_bytes'] > 0
    assert result['items'][0]['occupancy_verified'] is False
    assert '占用状态未知' in usage.render(result)
    assert 'broken-secret' not in json.dumps(result)


def test_external_instances_and_nonlocal_engine_are_not_sampled(ws, monkeypatch):
    ws.cfg['services']['api']['mode'] = 'external'
    ws.cfg['browser'] = {'endpoint': 'ws://external-example:1234'}
    monkeypatch.setattr(usage, '_docker', lambda *a: pytest.fail('external instance was queried'))
    result = usage.status(ws)
    assert result['status'] == 'passed'
    assert [i['state'] for i in result['items']].count('external') == 2
    item = browser_item()
    item['_docker_host'] = 'tcp://remote-example:2375'
    actual = usage.Sampler().sample(inventory(item))
    assert actual['status'] == 'unverified' and actual['items'][0]['memory_bytes'] is None


def test_all_docker_groups_share_one_deadline(monkeypatch):
    items = [browser_item(), browser_item('b' * 64)]
    items[0]['_docker_host'], items[1]['_docker_host'] = 'local-one', 'local-two'
    clock, calls = [0.0], []
    monkeypatch.setattr(usage.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(usage.docker_host, 'is_local', lambda host: host.startswith('local-'))
    monkeypatch.setattr(usage.docker_host, 'command', lambda args, host: (list(args), {}))
    def run(args, **options):
        calls.append((args, options['timeout']))
        clock[0] = 3.1
        return SimpleNamespace(returncode=0, stdout=json.dumps([{'Id': items[0]['instance_id'],
            'Config': {'Labels': {'echo-kit.workspace': 'owner'}}, 'State': {'Status': 'running'}}]))
    monkeypatch.setattr(usage.subprocess, 'run', run)
    result = usage.docker_sample(items)
    assert len(calls) == 1 and calls[0][1] == 3
    assert all(result[i['id']]['diagnostics'] for i in items)


def test_container_without_labels_is_unknown_not_a_traceback(monkeypatch):
    item = browser_item()
    monkeypatch.setattr(usage.docker_host, 'is_local', lambda _: True)
    def inspect_only(args, *unused):
        assert args[0] == 'inspect'
        return json.dumps([{'Id': item['instance_id'], 'Config': {'Labels': None}, 'State': {'Status': 'running'}}])
    monkeypatch.setattr(usage, '_docker', inspect_only)
    result = usage.Sampler().sample(inventory(item))
    assert result['status'] == 'unverified' and result['items'][0]['memory_bytes'] is None


def test_background_initial_failure_is_unknown_not_waiting(monkeypatch):
    background, entered = usage.BackgroundUsage(), threading.Event()
    def fail(**options):
        entered.set()
        raise ValueError('private-failure-detail')
    monkeypatch.setattr(usage, 'discover', fail)
    background.start()
    try:
        background.request([], [])
        assert entered.wait(2)
    finally:
        background.close()
        background.thread.join(3)
    result = background.snapshot()
    assert result['status'] == 'unverified'
    assert '未知' in '\n'.join(usage.summary_text(result))
    assert 'private-failure-detail' not in json.dumps(result)


def test_transient_windows_metadata_swap_is_bounded(tmp_path, monkeypatch):
    from echo_kit import core
    file = tmp_path / '登记.json'
    save(file, {'generation': 'before'})
    replace, attempts, waits = core.os.replace, [], []
    def transient(source, target):
        attempts.append(True)
        if len(attempts) < 3:
            error = PermissionError('temporary reader')
            error.winerror = 32
            raise error
        return replace(source, target)
    monkeypatch.setattr(core.os, 'replace', transient)
    monkeypatch.setattr(core.time, 'sleep', waits.append)
    save(file, {'generation': 'after'})
    assert len(attempts) == 3 and waits == [.01, .01]
    assert read(file) == {'generation': 'after'}
    def permanent(*args):
        error = PermissionError('permanent denial')
        error.winerror = 5
        raise error
    monkeypatch.setattr(core.os, 'replace', permanent)
    waits.clear()
    with pytest.raises(PermissionError):
        save(file, {'generation': 'never-committed'})
    assert len(waits) == 5 and read(file) == {'generation': 'after'}
