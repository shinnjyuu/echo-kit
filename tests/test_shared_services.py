import copy
import json
import sys
import subprocess
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from echo_kit.core import Workspace, KitError, init_workspace, read, save
from echo_kit.operations import Journal, ResourceBusy, service_resources
from echo_kit.service_registry import Registry, key, process_state
from echo_kit.services import Services
from test_core import free_port
from test_operations import cli, wait_for


def pair(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    port = free_port()
    result = []
    for folder, alias in [('a', 'api'), ('b', 'backend')]:
        root = tmp_path / folder
        init_workspace(root)
        ws = Workspace(root)
        ws.cfg['projects']['main']['path'] = str(source)
        ws.cfg['services'] = {alias: {'resource_id': 'shared-api', 'project': 'main',
            'port': port, 'ready': {'port': port},
            'command': [sys.executable, '-m', 'http.server', str(port), '--bind', '127.0.0.1']}}
        result.append((ws, alias))
    return result


def test_concurrent_aliases_single_instance_and_default_stop_scope(tmp_path):
    (a, x), (b, y) = pair(tmp_path)
    barrier = Barrier(2)
    def start(ws, name):
        barrier.wait(timeout=5)
        return Services(ws).up([name])[name]['instance']
    try:
        with ThreadPoolExecutor(2) as pool:
            first = pool.submit(start, a, x)
            second = pool.submit(start, b, y)
            one, two = first.result(), second.result()
        assert one['instance_id'] == two['instance_id']
        assert len(Registry().all()) == 1
        assert len(Registry().get(key(a, x))['references']) == 2
        for ws, name in [(a, x), (b, y)]:
            assert read(Services(ws).file)[name] == {'registry_id': key(ws, name)}
        borrower = b if one['origin']['workspace'] == str(a.root) else a
        Services(borrower).down([])
        assert process_state(one['identity']) == 'running'
        Services(b).down([y])
        assert not Services(a).status()[x]['owned']
    finally:
        Services(a).down([x])


def test_shared_leases_and_atomic_prepare_transition(tmp_path):
    (a, x), (b, y) = pair(tmp_path)
    manager = Services(a)
    try:
        with manager.reservation([x], 'verify.run') as (oid, prepared):
            manager._up_all(*manager._plan([x], None))
            with pytest.raises(ResourceBusy):
                Journal().acquire(b, 'reader', service_resources(b, [y], builds=False, access='shared'))
            prepared()
            other = Journal().acquire(b, 'reader', service_resources(b, [y], builds=False, access='shared'))
            try:
                assert all(v == 'shared' for v in Journal().show(oid)['held'].values())
                with pytest.raises(ResourceBusy):
                    Services(b).down([y])
                with pytest.raises(ResourceBusy):
                    Journal().acquire(b, 'exclusive case', service_resources(b, [y], builds=False))
            finally:
                Journal().release(other)
    finally:
        from echo_kit.processes import stop
        if manager.instances().get(x, {}).get('legacy'):
            stop(manager.instances()[x]['identity'])
        else:
            manager.down([x])


def test_incompatible_and_secret_environment(tmp_path):
    (a, x), (b, y) = pair(tmp_path)
    secret = 'unique-password-not-in-registry'
    a.cfg['services'][x]['env'] = {'PASSWORD': secret}
    b.cfg['services'][y]['env'] = {'PASSWORD': secret}
    try:
        first = Services(a).up([x])[x]['instance']
        Services(b).up([y])
        assert secret not in Registry().file(key(a, x)).read_text()
        b.cfg['services'][y]['env']['PASSWORD'] = 'changed'
        with pytest.raises(KitError, match='configuration differs'):
            Services(b).up([y])
        assert process_state(first['identity']) == 'running'
        b.cfg['services'][y]['resource_id'] = 'different'
        with pytest.raises(KitError, match='occupied|Unowned'):
            Services(b).up([y])
    finally:
        Services(a).down([x])


def test_local_identity_is_workspace_scoped(tmp_path):
    (a, x), (b, y) = pair(tmp_path)
    del a.cfg['services'][x]['resource_id']
    del b.cfg['services'][y]['resource_id']
    assert key(a, x) != key(b, y)


def test_legacy_read_only_then_adopt_with_backup(tmp_path):
    (a, x), _ = pair(tmp_path)
    manager = Services(a)
    try:
        registered = manager.up([x])[x]['instance']
        legacy = {k: v for k, v in registered.items() if k not in
                  ('registry_id', 'instance_id', 'signature', 'origin', 'references', 'dependencies', 'resource_keys', 'resource_id')}
        from echo_kit.processes import fingerprint
        legacy['identity'] = fingerprint(registered['identity']['pid'])
        Registry().file(key(a, x)).unlink()
        save(manager.file, {x: legacy})
        before = manager.file.read_bytes()
        assert manager.status()[x]['migration_required']
        assert manager.file.read_bytes() == before and Registry().all() == []
        adopted = manager.up([x])[x]['instance']
        assert adopted['identity']['pid'] == legacy['identity']['pid']
        assert list((a.state / 'migrations').glob('*.json'))
    finally:
        manager.down([x])


def test_two_verify_processes_share_and_keep_service_protected(tmp_path):
    (a, x), (b, y) = pair(tmp_path)
    for ws, name in [(a, x), (b, y)]:
        spec = ws.cfg['services'][name]
        with (ws.root / 'echo-kit.toml').open('a') as file:
            file.write('\n[projects.source]\npath=' + json.dumps(str(a.project('main')))
                       + '\n[services.' + name + ']\nproject="source"\nresource_id="shared-api"\nport='
                       + str(spec['port']) + '\ncommand=' + json.dumps(spec['command'])
                       + '\n[services.' + name + '.ready]\nport=' + str(spec['port'])
                       + '\n[cases.demo]\nservices=[' + json.dumps(name)
                       + ']\ncommand=["{python}","adapter.py"]\ntimeout=30\n')
        (ws.root / 'adapter.py').write_text('''import os,json,time
from pathlib import Path
Path('entered').touch()
while not Path('finish').exists(): time.sleep(.02)
Path(os.environ['ECHO_RESULT']).write_text(json.dumps({'status':'passed','checks':[]}))
''')
    a, b = Workspace(a.root), Workspace(b.root)
    children = []
    try:
        for ws in (a, b):
            children.append(subprocess.Popen(cli(ws, 'verify', 'run', 'demo'), stdout=subprocess.PIPE, stderr=subprocess.PIPE))
        wait_for(lambda: all((ws.root / 'entered').exists() for ws in (a, b)), timeout=20)
        assert len(Registry().all()) == 1
        with pytest.raises(ResourceBusy):
            Services(a).restart([x])
        assert len(Services(a).status()[x]['users']) == 2
    finally:
        for ws in (a, b):
            (ws.root / 'finish').touch()
        for child in children:
            out, err = child.communicate(timeout=15)
            assert child.returncode == 0, (out, err)
        Services(a).down([x])


def test_dependencies_across_workspaces_prevent_stop(tmp_path):
    (a, x), (b, y) = pair(tmp_path)
    port = free_port()
    b.cfg['services']['worker'] = {**copy.deepcopy(b.cfg['services'][y]),
        'resource_id': 'worker', 'depends': [y], 'port': port, 'ready': {'port': port},
        'command': [sys.executable, '-m', 'http.server', str(port), '--bind', '127.0.0.1']}
    try:
        Services(a).up([x])
        Services(b).up(['worker'])
        with pytest.raises(KitError, match='dependent'):
            Services(a).down([x])
        assert Services(a).status()[x]['owned']
    finally:
        Services(b).down(['worker', y])


def test_cleanup_refuses_new_generation(tmp_path):
    from echo_kit.service_registry import validate_run_instances
    (a, x), _ = pair(tmp_path)
    try:
        first = Services(a).up([x])
        Services(a).restart([x])
        with pytest.raises(KitError, match='Original service instance changed'):
            validate_run_instances({'services': first})
    finally:
        Services(a).down([x])


def test_tray_deduplicates_service_and_preserves_workspace_viewers(tmp_path, monkeypatch):
    from echo_kit import tray, browser
    (a, x), (b, y) = pair(tmp_path)
    try:
        Services(a).up([x])
        Services(b).up([y])
        containers, links, keys = {}, [], []
        for index, ws in enumerate((a, b)):
            cid = 'test-container-' + str(index)
            link = 'http://127.0.0.1:' + str(19000 + index) + '/vnc.html'
            links.append(link)
            save(ws.state / 'browser.json', {'id': cid, 'runtime_version': browser.RUNTIME_VERSION,
                 'headless': False, 'viewer_url': link})
            containers[cid] = {'ID': cid, 'Labels': 'echo-kit.workspace=' + ws.identity, 'State': 'running'}
            keys.append(tray.register(ws))
        monkeypatch.setattr(tray, 'container_states', lambda: containers)
        monkeypatch.setattr(Services, 'status', lambda *a, **kw: pytest.fail('tray ran health checks'))
        rows = tray.snapshot()
        shared = next(row for row in rows if row.get('kind') == 'shared_services')
        assert len(shared['services']) == 1 and len(shared['services'][0]['references']) == 2
        opened = []
        monkeypatch.setattr(tray.webbrowser, 'open', lambda url, **kw: opened.append(url) or True)
        for key in keys:
            tray.open_viewer(key)
        assert opened == links
        monkeypatch.undo()
    finally:
        Services(a).down([x])


def test_nested_start_does_not_downgrade_exclusive_caller(tmp_path):
    from echo_kit.operations import operation
    (a, x), _ = pair(tmp_path)
    try:
        with operation(a, 'exclusive parent', service_resources(a, [x])) as oid:
            Services(a).up([x])
            assert set(Journal().show(oid)['held'].values()) == {'exclusive'}
    finally:
        Services(a).down([x])


def test_legacy_pending_operation_and_duplicate_refuse_adoption(tmp_path):
    from echo_kit.processes import fingerprint
    (a, x), (b, y) = pair(tmp_path)
    one = Services(a).up([x])[x]['instance']
    legacy = {**one, 'identity': fingerprint(one['identity']['pid'])}
    legacy.pop('registry_id')
    Registry().file(key(a, x)).unlink()
    save(Services(a).file, {x: legacy})
    pending = Journal().acquire(a, 'unfinished unrelated', {'test:pending': 'exclusive'})
    try:
        with pytest.raises(KitError, match='existing operations'):
            Services(a).up([x])
        assert not list((a.state / 'migrations').glob('*.json'))
    finally:
        Journal().release(pending)
    Services(a).up([x])
    # A different live process in a legacy registration must not be hidden by
    # the shared registry's healthy instance.
    import os
    other = {**legacy, 'identity': fingerprint(os.getpid())}
    save(Services(b).file, {y: other})
    try:
        assert Services(b).status()[y]['migration_required']
        with pytest.raises(KitError, match='Multiple instances'):
            Services(b).up([y])
    finally:
        Services(a).down([x])


def test_unhealthy_registered_instance_not_restarted(tmp_path, monkeypatch):
    from echo_kit import services
    (a, x), _ = pair(tmp_path)
    try:
        original = Services(a).up([x])[x]['instance']
        monkeypatch.setattr(services, 'healthy', lambda *args: False)
        with pytest.raises(KitError, match='unhealthy'):
            Services(a).up([x])
        assert Registry().get(key(a, x))['instance_id'] == original['instance_id']
    finally:
        Services(a).down([x])


def test_failed_cleanup_retains_original_service_and_rejects_retargeting(tmp_path, monkeypatch):
    from echo_kit import runner
    (a, x), (b, y) = pair(tmp_path)
    a.cfg['cases'] = {'pending': {'services': [x], 'command': [sys.executable, '-c', 'pass'],
        'cleanup': {'command': [sys.executable, '-c', 'pass'], 'env': {'TARGET': 'original'}}}}
    monkeypatch.setattr(runner, 'execute', lambda *args, **kwargs: {'status': 'passed', 'terminal_confirmed': False})
    record = None
    try:
        record = runner.run(a, 'pending', 'verify')[0]
        assert record['cleanup'] == 'unconfirmed'
        held = Journal().show(record['operation_id'])['held']
        assert held and set(held.values()) == {'shared'}
        with pytest.raises(ResourceBusy):
            Services(b).down([y])
        a.cfg['cases']['pending']['cleanup']['env']['TARGET'] = 'different'
        with pytest.raises(KitError, match='Cleanup configuration changed'):
            runner.cleanup_run(a, a.runs / record['id'])
        assert Journal().show(record['operation_id'])['held'] == held
        assert (a.runs / record['id'] / 'active.json').exists()
    finally:
        if record:
            # Test adapters performed no business work; release test-only leases.
            Journal().release(record['operation_id'])
        Services(a).down([x])


def test_startup_lock_timeout_marks_run_blocked(tmp_path, monkeypatch):
    import filelock
    from echo_kit import runner
    (a, x), _ = pair(tmp_path)
    a.cfg['cases'] = {'waiting': {'services': [x], 'command': [sys.executable, '-c', 'pass']}}
    original = filelock.FileLock
    def lock(path, **kwargs):
        if str(path).endswith('.startup.lock'):
            raise filelock.Timeout(path)
        return original(path, **kwargs)
    monkeypatch.setattr(filelock, 'FileLock', lock)
    with pytest.raises(KitError, match='startup wait timed out'):
        runner.run(a, 'waiting', 'verify')
    record = read(next(a.runs.glob('*/run.json')))
    assert record['status'] == 'blocked' and record['finished']
    assert Registry().all() == []
