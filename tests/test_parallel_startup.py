import sys
import threading
from collections import Counter
from contextvars import ContextVar

import pytest

from echo_kit.core import Workspace, init_workspace, KitError
from echo_kit.services import Services
from echo_kit.operations import Journal


@pytest.fixture
def manager(tmp_path, monkeypatch):
    monkeypatch.setenv('ECHO_KIT_DATA_HOME', str(tmp_path / 'journal'))
    init_workspace(tmp_path)
    ws = Workspace(tmp_path)
    ws.cfg['services'] = {n: {'command': [sys.executable, '-V'], 'ready': {'port': 19000 + i},
                                  'resource_id': n} for i, n in enumerate('abcd')}
    return Services(ws)


def test_parallel_limit_context_and_complete_mapping(manager, monkeypatch):
    barrier = threading.Barrier(2)
    lock = threading.Lock()
    active = peak = 0
    calls = Counter()
    context = ContextVar('test_context', default=None)
    context.set('inherited')
    def start(name, cancel, timings, build_lock):
        nonlocal active, peak
        assert context.get() == 'inherited'
        with lock:
            calls[name] += 1
            active += 1
            peak = max(peak, active)
        barrier.wait(timeout=5)
        with lock:
            active -= 1
        return 'ready'
    monkeypatch.setattr(manager, '_up', start)
    result = manager.up(list('abcd'), jobs=2)
    assert peak == 2 and calls == Counter('abcd')
    assert all(r['startup']['result'] == 'ready' for r in result.values())
    op, = Journal().list()
    assert not op['held']
    assert len([e for e in op['events'] if e['action'].startswith('startup:')]) == 4


def test_dependencies_deduplicated_and_serial(manager, monkeypatch):
    manager.ws.cfg['services']['c']['depends'] = ['a', 'b']
    manager.ws.cfg['services']['d']['depends'] = ['a', 'c']
    order = []
    def start(name, *args):
        assert all(d in order for d in manager.ws.cfg['services'][name].get('depends', []))
        order.append(name)
        return 'ready'
    monkeypatch.setattr(manager, '_up', start)
    manager.up(['d', 'c', 'a'], jobs=1)
    assert order == list('abcd')


def test_failure_drains_and_does_not_schedule_more(manager, monkeypatch):
    barrier = threading.Barrier(2)
    calls = []
    def start(name, *args):
        calls.append(name)
        barrier.wait(timeout=5)
        if name == 'a':
            raise KitError('broken build')
        return 'ready'
    monkeypatch.setattr(manager, '_up', start)
    manager.ws.cfg['services']['c']['depends'] = ['a']
    manager.ws.cfg['services']['d']['depends'] = ['a']
    with pytest.raises(KitError, match='broken build') as caught:
        manager.up(list('abcd'), jobs=2)
    result = caught.value.details['services']
    assert set(calls) == {'a', 'b'}
    assert result['b']['startup']['result'] == 'ready'
    assert result['c']['startup']['result'] == 'not_started'
    assert caught.value.details['status'] == 'failed'
    assert not Journal().list()[0]['held']


@pytest.mark.parametrize('jobs', [0, -1, True, '4'])
def test_invalid_jobs_before_launch(manager, jobs):
    with pytest.raises(KitError, match='positive integer'):
        manager.up(['a'], jobs=jobs)
    assert Journal().list() == []


def test_invalid_later_service_prevents_all_launches(manager, monkeypatch):
    manager.ws.cfg['services']['b']['command'] = []
    monkeypatch.setattr(manager, '_up', lambda *a: pytest.fail('must validate first'))
    with pytest.raises(KitError, match='command'):
        manager.up(['a', 'b'])


def test_build_lock_ends_before_readiness(manager, monkeypatch):
    from echo_kit.startup import Startup
    barrier = threading.Barrier(2)
    second_attempt = threading.Event()
    calls = []
    class ObservedLock:
        def __init__(self):
            self.lock = threading.Lock()
            self.counter = threading.Lock()
            self.attempts = 0
        def __enter__(self):
            with self.counter:
                self.attempts += 1
                if self.attempts == 2:
                    second_attempt.set()
            self.lock.acquire()
        def __exit__(self, *args):
            self.lock.release()
    def launch(name, *args):
        calls.append(name)
        if len(calls) == 1:
            assert second_attempt.wait(5)
            # Second worker reached the lock but cannot enter launch yet.
            assert len(calls) == 1
    monkeypatch.setattr(manager, '_launch', launch)
    monkeypatch.setattr(manager, '_wait_ready', lambda *a: barrier.wait(timeout=5))
    startup = Startup(manager, ['a', 'b'], 2)
    startup.locks = {key: ObservedLock() for key in startup.locks}
    startup.run()
    assert set(calls) == {'a', 'b'}


def test_different_projects_launch_concurrently(manager, monkeypatch, tmp_path):
    barrier = threading.Barrier(2)
    (tmp_path / 'other').mkdir()
    manager.ws.cfg['projects']['other'] = {'path': 'other'}
    manager.ws.cfg['services']['b']['project'] = 'other'
    monkeypatch.setattr(manager, '_launch', lambda *a: barrier.wait(timeout=5))
    monkeypatch.setattr(manager, '_wait_ready', lambda *a: None)
    manager.up(['a', 'b'], jobs=2)


def test_interrupt_drains_workers(manager, monkeypatch):
    import echo_kit.startup as module
    actual_wait = module.wait
    entered = threading.Event()
    def start(name, cancel, *args):
        entered.set()
        assert cancel.wait(5)
        raise KitError('cancelled')
    def interrupt_once(*args, **kwargs):
        assert entered.wait(5)
        monkeypatch.setattr(module, 'wait', actual_wait)
        raise KeyboardInterrupt
    monkeypatch.setattr(manager, '_up', start)
    monkeypatch.setattr(module, 'wait', interrupt_once)
    with pytest.raises(KitError) as caught:
        manager.up(list('abcd'), jobs=1)
    assert caught.value.details['status'] == 'interrupted'
    assert caught.value.details['services']['b']['startup']['result'] == 'not_started'
    assert not Journal().list()[0]['held']

def test_real_prepare_timeout_tracked_and_stopped(manager, monkeypatch):
    import subprocess
    from echo_kit.processes import matches
    subprocess.run(['git', 'init', str(manager.ws.root)], check=True, capture_output=True)
    (manager.ws.root / '.gitignore').write_text('.echo-kit/\njournal/\n')
    manager.ws.cfg['services']['a']['prepare'] = {'command': [sys.executable, '-c', 'import time; time.sleep(30)'], 'timeout': .5}
    with pytest.raises(KitError, match='timed out') as caught:
        manager.up(['a'])
    op, = Journal().list()
    assert op['children'] and not any(matches(c) for c in op['children'])
    assert not op['held']
    assert caught.value.details['services']['a']['startup']['timings']['build'] > 0


def test_nested_operation_context_and_real_services(manager):
    from echo_kit.operations import operation, service_resources
    from test_core import free_port
    for name in 'ab':
        port = free_port()
        manager.ws.cfg['services'][name] = {'command': [sys.executable, '-m', 'http.server', str(port), '--bind', '127.0.0.1'],
                                           'port': port, 'ready': {'port': port}, 'timeout': 10}
    try:
        with operation(manager.ws, 'verify.run', service_resources(manager.ws, ['a', 'b'])) as oid:
            result = manager.up(['a', 'b'])
            assert all(result[n]['owned'] and result[n]['ready'] for n in 'ab')
            assert len(Journal().list()) == 1
            events = Journal().show(oid)['events']
            assert {'service:a', 'service:b'} <= {e['action'] for e in events}
            again = manager.up(['a', 'b'])
            assert all(again[n]['startup']['result'] == 'reused' for n in 'ab')
            assert all(again[n]['instance'] == result[n]['instance'] for n in 'ab')
    finally:
        manager.down(['a', 'b'])


def test_cli_json_failure_summary(manager, monkeypatch, capsys):
    from echo_kit.cli import main
    import echo_kit.cli as cli
    monkeypatch.setattr(cli, 'Workspace', lambda *args: manager.ws)
    monkeypatch.setattr(Services, '_up', lambda *args: (_ for _ in ()).throw(KitError('failure')))
    assert main(['--offline', '--json', 'services', 'up', 'a', '--jobs', '1']) == 1
    import json
    result = json.loads(capsys.readouterr().out)
    assert result['status'] == 'failed'
    assert result['services']['a']['startup']['error'] == 'failure'

def test_real_build_interruption_terminates_child(manager, monkeypatch):
    import subprocess
    import echo_kit.startup as scheduler
    import echo_kit.operations as operations
    from echo_kit.processes import matches
    subprocess.run(['git', 'init', str(manager.ws.root)], check=True, capture_output=True)
    (manager.ws.root / '.gitignore').write_text('.echo-kit/\njournal/\n')
    manager.ws.cfg['services']['a']['prepare'] = {'command': [sys.executable, '-c', 'import time; time.sleep(30)']}
    tracked = threading.Event()
    original_track = operations.track_child
    original_wait = scheduler.wait
    def track(identity):
        original_track(identity)
        tracked.set()
    def interrupt(*args, **kwargs):
        assert tracked.wait(10)
        monkeypatch.setattr(scheduler, 'wait', original_wait)
        raise KeyboardInterrupt
    monkeypatch.setattr(operations, 'track_child', track)
    monkeypatch.setattr(scheduler, 'wait', interrupt)
    with pytest.raises(KitError) as caught:
        manager.up(['a'])
    assert caught.value.details['status'] == 'interrupted'
    op, = Journal().list()
    assert not op['held'] and op['children']
    assert not any(matches(c) for c in op['children'])


def test_readiness_timeout_keeps_owned_process(manager):
    from echo_kit.processes import matches
    manager.ws.cfg['services']['a']['command'] = [sys.executable, '-c', 'import time; time.sleep(30)']
    manager.ws.cfg['services']['a']['timeout'] = .25
    try:
        with pytest.raises(KitError, match='readiness timed out') as caught:
            manager.up(['a'])
        entry = caught.value.details['services']['a']
        assert entry['owned'] and not entry['ready']
        assert matches(entry['instance']['identity'])
        assert entry['startup']['timings']['ready'] > 0
    finally:
        manager.down(['a'])


def test_restart_validation_precedes_stop(manager, monkeypatch):
    monkeypatch.setattr(manager, '_down', lambda *a: pytest.fail('must not stop'))
    with pytest.raises(KitError):
        manager.restart(['missing'])


def test_config_default_and_cli_override(manager):
    manager.ws.cfg['service_startup'] = {'concurrency': 2}
    assert manager._plan(['a'], None)[1] == 2
    assert manager._plan(['a'], 1)[1] == 1
