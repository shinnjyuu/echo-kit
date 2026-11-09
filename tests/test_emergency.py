import json
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import psutil
import pytest
from filelock import FileLock

from echo_kit import browser, cli, emergency, launches, runner, tray, updates
from echo_kit.core import Workspace, init_workspace, read, save
from echo_kit.operations import Journal, data_root
from echo_kit.processes import fingerprint, identity_exited
from echo_kit.service_registry import Registry, key
from echo_kit.services import Services


def workspace(path):
    init_workspace(path)
    return Workspace(path)


@pytest.fixture
def children():
    processes = []

    def start(code='import time; time.sleep(60)', *args):
        process = subprocess.Popen([sys.executable, '-c', code, *map(str, args)],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        processes.append(process)
        return process

    yield start
    for process in processes:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=10)


def until(predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(.025)
    pytest.fail('Timed out waiting for the controlled process')


def shared_service(ws):
    ws.cfg['services'] = {'api': {'resource_id': 'fixture-api', 'command': [sys.executable, '-V'],
                                  'ready': {'port': 43760}}}


def test_emergency_cli_is_local_requires_explicit_all(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, 'Workspace', lambda *a: pytest.fail('loaded workspace'))
    monkeypatch.setattr(updates, 'startup', lambda *a: pytest.fail('routed update'))
    for args in ([], ['--a']):
        with pytest.raises(SystemExit) as error:
            cli.main(['emergency', 'stop', *args])
        assert error.value.code == 2
    assert launches.gate() == 'initial'
    capsys.readouterr()
    assert cli.main(['--json', 'emergency', 'stop', '--all']) == 0
    assert json.loads(capsys.readouterr().out)['counts'] == {'stopped': 0, 'already_exited': 0, 'unconfirmed': 0}
    assert launches.gate() != 'initial'


def test_forwarded_old_launch_is_cancelled_before_loading_workspace(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    old_generation = launches.gate()
    assert emergency.stop_all()['status'] == 'passed'
    monkeypatch.setenv(updates.FORWARDED, 'forwarded')
    monkeypatch.setenv(launches.FORWARDED_GENERATION, old_generation)
    monkeypatch.setattr(cli, 'Workspace', lambda *a: pytest.fail('loaded workspace'))
    monkeypatch.setattr(updates, 'startup', lambda *a: pytest.fail('routed a cancelled command'))

    assert cli.main(['--json', 'services', 'up']) == 130
    assert json.loads(capsys.readouterr().out)['reason'] == 'emergency_stop'
    assert launches.FORWARDED_GENERATION not in os.environ


def test_shared_tree_stops_even_with_busy_locks_and_reused_pid_is_spared(tmp_path, children, monkeypatch):
    first, second = workspace(tmp_path / 'first'), workspace(tmp_path / 'second')
    for ws in (first, second):
        shared_service(ws)
    child_file = tmp_path / 'descendant'
    parent = children('import subprocess, sys, time; from pathlib import Path; '
                      'p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"]); '
                      'Path(sys.argv[1]).write_text(str(p.pid)); time.sleep(60)', child_file)
    descendant = until(lambda: fingerprint(int(child_file.read_text())) if child_file.is_file() else None)
    unrelated = children()
    registry, journal = Registry(), Journal()
    original = registry.create(first, 'api', {'identity': fingerprint(parent.pid)})
    registry.reference(second, 'api', original['registry_id'])
    save(registry.file('1' * 64), {**original, 'registry_id': '1' * 64,
                                  'identity': {**fingerprint(unrelated.pid), 'created': 0}})
    (registry.root / 'broken.json').write_text('{', encoding='utf-8')
    oid = journal.acquire(second, 'fixture', {'service:fixture-api': 'exclusive'})
    monkeypatch.setattr(Services, 'down', lambda *a: pytest.fail('ordinary stop'))
    try:
        with registry.lock(), journal.lock():
            result = emergency.stop_all()
        assert result['status'] == 'unverified'
        assert result['counts'] == {'stopped': 1, 'already_exited': 0, 'unconfirmed': 2}
        assert [item['id'] for item in result['pending_operations']] == [oid]
        assert parent.wait(timeout=5) != 0
        assert identity_exited(descendant)
        assert unrelated.poll() is None
    finally:
        if not identity_exited(descendant):
            process = psutil.Process(descendant['pid'])
            if process.create_time() == descendant['created']:
                process.kill()


def test_permission_failure_isolated_from_legacy_service_and_abandoned_task(tmp_path, children, monkeypatch):
    ws = workspace(tmp_path)
    task, service, denied = (children() for _ in range(3))
    journal = Journal()
    oid = journal.acquire(ws, 'fixture', {'service:fixture': 'exclusive'})
    journal.update(oid, identity={**fingerprint(denied.pid), 'created': 0}, children=[fingerprint(task.pid)])
    # Discover the registration even when no journal or tray entry mentions its environment.
    legacy = ws.state / 'services-legacy-test.json'
    save(legacy, {'api': {'identity': fingerprint(service.pid)}})
    oid_denied = journal.acquire(ws, 'fixture', {'service:denied': 'exclusive'})
    journal.update(oid_denied, children=[fingerprint(denied.pid)])
    real_kill = emergency.kill_tree

    def kill(identity, *args):
        if identity['pid'] == denied.pid:
            raise psutil.AccessDenied(denied.pid)
        return real_kill(identity, *args)

    monkeypatch.setattr(emergency, 'kill_tree', kill)
    result = emergency.stop_all()
    assert result['counts']['stopped'] == 2
    assert result['counts']['unconfirmed'] == 1
    assert any(row.get('reason') == 'permission_denied' for row in result['targets'])
    assert read(legacy) == {}
    assert service.poll() is not None and task.poll() is not None and denied.poll() is None
    assert {item['id'] for item in result['pending_operations']} == {oid_denied}
    assert journal.show(oid)['result'] == 'emergency_stopped'


def test_local_abandoned_reservation_released_after_stop(tmp_path, children):
    ws = workspace(tmp_path)
    process = children()
    journal = Journal()
    oid = journal.acquire(ws, 'services.up', {'service:fixture': 'exclusive'})
    journal.update(oid, identity={**fingerprint(process.pid), 'created': 0}, children=[fingerprint(process.pid)])
    assert emergency.stop_all()['status'] == 'passed'
    assert journal.show(oid)['result'] == 'emergency_stopped'
    assert journal.acquire(ws, 'services.up', {'service:fixture': 'exclusive'})


def test_queued_start_cancelled_before_lock_releases_then_new_command_runs(tmp_path, monkeypatch):
    ws = workspace(tmp_path)
    shared_service(ws)
    queued = threading.Event()
    real_lock = launches.launch_lock

    def wait(lock):
        if lock.lock_file.endswith('.startup.lock'):
            queued.set()
        return real_lock(lock)

    monkeypatch.setattr(launches, 'launch_lock', wait)
    starts = []
    monkeypatch.setattr(Services, '_up', lambda self, name, *args: starts.append(name) or 'ready')
    lock_path = Registry().root / (key(ws, 'api') + '.startup.lock')
    with ThreadPoolExecutor(max_workers=1) as pool, FileLock(str(lock_path)):
        old = pool.submit(Services(ws).up, ['api'])
        assert queued.wait(5)
        assert emergency.stop_all()['status'] == 'passed'
        with pytest.raises(launches.EmergencyInterrupted):
            old.result(timeout=5)
        assert not starts
    assert Services(ws).up(['api'])['api']['startup']['result'] == 'ready'
    assert starts == ['api']


def test_late_native_spawn_is_stopped_even_after_emergency_returns(tmp_path, children, monkeypatch):
    ws = workspace(tmp_path)
    created, return_child = threading.Event(), threading.Event()
    spawned = []
    monkeypatch.setattr(emergency, 'VERIFY_SECONDS', .15)

    def start():
        spawned.append(children())
        created.set()
        assert return_child.wait(8)
        return spawned[0]

    def launch():
        with launches.process(ws, 'service', start, journal=False):
            pytest.fail('cancelled service was handed to the registry')

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(launch)
        try:
            assert created.wait(5)
            result = emergency.stop_all()
            assert result['status'] == 'unverified'
            assert any(row.get('reason') == 'launch_in_flight' for row in result['targets'])
        finally:
            return_child.set()
        with pytest.raises(launches.EmergencyInterrupted):
            future.result(timeout=5)
    assert spawned[0].wait(timeout=5) != 0


def test_registered_temporary_child_is_killed_before_journal_handoff(tmp_path, children, monkeypatch):
    from echo_kit import operations
    ws = workspace(tmp_path)
    registered, finish = threading.Event(), threading.Event()
    spawned = []

    def track(identity):
        registered.set()
        assert finish.wait(8)

    def launch():
        def start():
            child = children()
            spawned.append(child)
            return child
        with launches.process(ws, 'task', start):
            pytest.fail('cancelled task resumed')

    monkeypatch.setattr(operations, 'track_child', track)
    with ThreadPoolExecutor(max_workers=1) as pool:
        old = pool.submit(launch)
        try:
            assert registered.wait(5)
            result = emergency.stop_all()
            assert result['counts']['stopped'] == 1
            assert spawned[0].poll() is not None
        finally:
            finish.set()
        with pytest.raises(launches.EmergencyInterrupted):
            old.result(timeout=5)


def local_host():
    return 'npipe:////./pipe/echo-kit-test' if sys.platform == 'win32' else 'unix:///tmp/echo-kit-test.sock'


def test_browser_state_and_docker_timeout_do_not_delay_native_signals(tmp_path, children, monkeypatch):
    ws = workspace(tmp_path)
    shared_service(ws)
    child = children()
    Registry().create(ws, 'api', {'identity': fingerprint(child.pid)})
    save(ws.state / 'browser.json', {'id': 'a' * 64, 'docker_host': local_host()})

    def states(host, deadline):
        until(lambda: child.poll() is not None)
        raise subprocess.TimeoutExpired('docker', 2)

    monkeypatch.setattr(emergency, 'container_states', states)
    result = emergency.stop_all()
    assert result['counts'] == {'stopped': 1, 'already_exited': 0, 'unconfirmed': 1}
    assert any(row.get('reason') == 'docker_state_unavailable' for row in result['targets'])


def test_late_docker_start_checks_generation_and_terminates_owned_id(tmp_path, monkeypatch):
    ws = workspace(tmp_path)
    entered, reply = threading.Event(), threading.Event()
    cid = 'b' * 64
    target = {'id': cid, 'docker_host': local_host()}
    save(ws.state / 'browser.json', target)
    state = {cid: ('created', ws.identity)}
    kills = []
    monkeypatch.setattr(emergency, 'VERIFY_SECONDS', .25)
    monkeypatch.setattr(emergency, 'container_states', lambda *a: dict(state))

    def start(*args):
        entered.set()
        assert reply.wait(8)
        state[cid] = ('running', ws.identity)

    def call(host, deadline, *args):
        assert args == ('kill', '--signal=KILL', cid)
        kills.append(cid)
        state[cid] = ('exited', ws.identity)

    monkeypatch.setattr(emergency, 'docker_call', call)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(browser.start_container, ws, target, start)
        try:
            assert entered.wait(5)
            assert any(row.get('reason') == 'launch_in_flight' for row in emergency.stop_all()['targets'])
        finally:
            reply.set()
        with pytest.raises(launches.EmergencyInterrupted):
            future.result(timeout=5)
    assert kills == [cid] and state[cid][0] == 'exited'


def test_container_scope_rejects_remote_engine_wrong_label_and_incomplete_id(monkeypatch):
    cid, wrong = 'a' * 64, 'b' * 64
    sweep = emergency.Sweep()
    sweep.browser({'id': cid, 'docker_host': local_host()}, 'owner', None)
    sweep.browser({'id': wrong, 'docker_host': local_host()}, 'owner', None)
    sweep.browser({'id': 'c' * 64, 'docker_host': 'ssh://remote'}, 'owner', None)
    states = {cid: ('restarting', 'owner'), wrong: ('paused', 'other')}
    monkeypatch.setattr(emergency, 'container_states', lambda *a: dict(states))

    def call(host, deadline, *args):
        assert host == local_host() and args == ('kill', '--signal=KILL', cid)
        states[cid] = ('exited', 'owner')

    monkeypatch.setattr(emergency, 'docker_call', call)
    emergency.stop_containers(sweep.browsers.values(), time.monotonic() + 2)
    assert [row['status'] for row in sweep.targets] == ['stopped', 'unconfirmed', 'unconfirmed']
    with pytest.raises(emergency.KitError, match='full browser'):
        sweep.browser({'id': 'abc'}, 'owner', None)


def test_in_progress_rejects_launch_and_crashed_controller_unlocks():
    launches.directory().mkdir()
    with launches.stop_lock():
        launches.set_gate('changed', True)
        assert emergency.stop_all()['reason'] == 'emergency_in_progress'
        with pytest.raises(launches.EmergencyBusy), launches.scope():
            pytest.fail('admitted during emergency')
    with launches.scope():
        assert launches.admitted_generation() == 'changed'


def test_cancels_lab_rounds_and_cleanup_leaves_business_marker(tmp_path, monkeypatch):
    ws = workspace(tmp_path)
    ws.cfg['cases'] = {'sample': {'command': [sys.executable, '-V'], 'creates_tasks': True,
                                 'cleanup': {'command': [sys.executable, '-V']}}}
    called = []

    def execute(ws, spec, path, request, *args):
        called.append(path.name)
        assert path.name == 'adapter'
        save(Path(request['active_file']), {'run_id': request['run_id'], 'tasks': ['remote-fixture']})
        assert emergency.stop_all()['status'] == 'passed'
        return {'status': 'passed'}

    monkeypatch.setattr(runner, 'execute', execute)
    monkeypatch.setattr(runner, 'report', lambda *a: pytest.fail('emergency report'))
    with pytest.raises(launches.EmergencyInterrupted):
        runner.run(ws, 'sample', repeat=3)
    assert called == ['adapter']
    run, = ws.runs.iterdir()
    assert read(run / 'run.json')['status'] == 'interrupted'
    assert read(run / 'active.json')['tasks'] == ['remote-fixture']
    assert Journal().list(unfinished=True)[0]['held']


def test_tray_detects_only_a_running_local_task(tmp_path, children, monkeypatch):
    ws = workspace(tmp_path)
    monkeypatch.setattr(tray, 'root', lambda: data_root() / 'tray' / 'session-test')
    process = children()
    journal = Journal()
    oid = journal.acquire(ws, 'lab.run', {})
    journal.update(oid, children=[fingerprint(process.pid)])
    rows = tray.snapshot()
    assert tray.has_resources(rows) and rows[0]['kind'] == 'tasks'
    assert emergency.stop_all()['counts']['stopped'] == 1
    assert not tray.has_resources(tray.snapshot())
