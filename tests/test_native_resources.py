"""Native browser discovery, resource accounting, tray ownership and emergency boundaries."""
import os
import subprocess
import sys

import pytest

from echo_kit import emergency, resource_usage as usage, tray, workspace_index
from echo_kit.core import KitError, Workspace, init_workspace, save
from echo_kit.operations import Journal, data_root, native_browser_resource
from echo_kit.processes import fingerprint
from test_resource_usage import Process, fake_processes, inventory, native


@pytest.fixture
def ws(tmp_path, tmp_path_factory, isolated_operation_journal):
    # Hard assertion before creating any target: even emergency tests can only
    # discover the fresh user-data directory supplied by tests/conftest.py.
    assert data_root().is_relative_to(tmp_path_factory.getbasetemp().resolve())
    assert os.environ['ECHO_KIT_TRAY'] == '0'
    init_workspace(tmp_path / '原生 浏览器')
    value = Workspace(tmp_path / '原生 浏览器')
    value.cfg['services'] = {}
    return value


def registered(ws, sid='session-one', identity=None, children=(), **fields):
    record = {'backend': 'agent-browser', 'session_id': sid, 'instance_id': 'instance-' + sid,
              'workspace': str(ws.root), 'environment': ws.environment, 'actor': 'controlled-agent',
              'identity': identity or fingerprint(os.getpid()), 'children': list(children), 'status': 'ready',
              'headless': False, 'operation_id': None, 'output_dir': str(ws.state / 'observations' / sid),
              'cdp_endpoint': 'ws://127.0.0.1:41000/devtools/browser/controlled', 'trace_format': 'chrome-trace-events', **fields}
    save(ws.state / 'native-browsers' / (sid + '.json'), record)
    workspace_index.register(ws)
    return record


def test_index_does_not_trust_native_path_or_expose_private_fields(ws, tmp_path):
    record = registered(ws, token='private-token', ipc_address='private-pipe')
    hint = workspace_index.entry(ws)
    hint['native_browsers_dir'] = str(tmp_path / 'other')
    found, problems = workspace_index.native_records(hint)
    assert not problems and [r['session_id'] for r in found] == [record['session_id']]
    assert 'token' not in found[0] and 'ipc_address' not in found[0]
    bad = ws.state / 'native-browsers' / 'bad.json'
    bad.write_text('{broken', encoding='utf-8')
    found, problems = workspace_index.native_records(hint)
    assert len(found) == 1 and problems[0]['reason'] == 'invalid_native_browser_registration'


def test_native_and_old_container_are_both_discovered_without_docker_query(ws, monkeypatch):
    record = registered(ws)
    save(ws.state / 'browser.json', {'id': 'a' * 64, 'endpoint': 'ws://127.0.0.1:19323/', 'docker_host': ''})
    monkeypatch.setattr(workspace_index, 'native_state', lambda _: 'running')
    monkeypatch.setattr(usage, '_docker', lambda *a: pytest.fail('discovery must not query Docker'))
    key = native_browser_resource(ws, record['session_id'])
    assert '19323' not in key
    oid = Journal().acquire(ws, 'browser.native.session', {key: 'shared'})
    current = usage.discover(ws)
    assert {item['kind'] for item in current['items']} == {'browser', 'native_browser'}
    item = next(i for i in current['items'] if i['kind'] == 'native_browser')
    assert item['occupants'][0]['id'] == oid
    assert item['_keys'] == [key]
    all_known = usage.discover()
    assert len([i for i in all_known['items'] if i['kind'] == 'native_browser']) == 1
    assert 'private-token' not in str(all_known)
    # A changed external configuration must not hide an already-owned old container.
    ws.cfg['browser'] = {'backend': 'playwright', 'endpoint': 'ws://explicit-external:3000'}
    workspace_index.register(ws)
    assert {i['kind'] for i in usage.discover()['items']} == {'browser', 'native_browser'}


def test_native_environment_scope_and_selected_service_isolation(ws, monkeypatch):
    registered(ws, environment='other')
    monkeypatch.setattr(workspace_index, 'native_state', lambda _: 'running')
    assert not usage.discover(ws)['items']
    assert len(usage.discover()['items']) == 1
    ws.cfg['services'] = {'api': {'mode': 'external'}}
    monkeypatch.setattr(workspace_index, 'native_records', lambda *_: pytest.fail('specific service query reads browser'))
    selected = usage.discover(ws, ['api'])
    assert len(selected['items']) == 1 and selected['items'][0]['kind'] == 'service'


def test_native_process_tree_and_nested_service_are_counted_once(monkeypatch):
    worker, daemon, chrome, renderer = Process(101, rss=10), Process(102, parent=101, rss=20), Process(103, parent=102, rss=30), Process(104, parent=103, rss=40)
    fake_processes(monkeypatch, [worker, daemon, chrome, renderer])
    native_browser = usage.base('native_browser', 'native-browser:controlled', 'Controlled browser')
    native_browser.update(state='running', _identity={'pid': 101, 'created': 1.0},
                          _identities=[{'pid': p.pid, 'created': 1.0} for p in (worker, daemon, chrome)])
    waits = []
    monkeypatch.setattr(usage.time, 'sleep', lambda seconds: waits.append(seconds))
    monkeypatch.setattr(usage, 'docker_sample', lambda *_: pytest.fail('native browser queried Docker'))
    result = usage.Sampler().sample(inventory(native_browser, native('nested-service', 104)))
    rows = {r['kind']: r for r in result['items']}
    assert rows['native_browser']['memory_bytes'] == 60 and rows['native_browser']['process_count'] == 3
    assert rows['service']['memory_bytes'] == 40
    assert result['summary']['native_browsers']['memory_bytes'] == 60
    assert result['summary']['native']['memory_bytes'] == 40
    assert result['summary']['containers']['memory_bytes'] == 0 and waits == [.25]


def test_tray_native_window_entry_is_independent_of_legacy_and_rechecks_owner(ws, tmp_path, monkeypatch):
    record = registered(ws)
    monkeypatch.setattr(tray, 'root', lambda: tmp_path / 'tray')
    tray.register(ws)
    monkeypatch.setattr(workspace_index, 'native_state', lambda _: 'running')
    monkeypatch.setattr(tray, 'container_states', lambda: pytest.fail('native tray queried Docker'))
    rows = tray.snapshot()
    row = next(r for r in rows if r['browser'] and r['browser'].get('backend') == 'agent-browser')
    assert row['browser']['session_id'] == record['session_id']
    assert tray.has_resources(rows)
    assert 'identity' not in str(row)
    focused = []
    monkeypatch.setattr(tray, 'focus_native_window', lambda found: focused.append(found['instance_id']))
    tray.open_viewer(row['key'])
    assert focused == [record['instance_id']]
    monkeypatch.setattr(workspace_index, 'native_state', lambda _: 'unknown')
    with pytest.raises(KitError, match='verified owned'):
        tray.open_viewer(row['key'])
    assert focused == [record['instance_id']]


def test_emergency_discovers_native_registrations_and_spares_reused_pid(ws):
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    unrelated = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        registered(ws, identity=fingerprint(child.pid), children=[{**fingerprint(unrelated.pid), 'created': 0}])
        result = emergency.stop_all()
        child.wait(timeout=5)
        assert unrelated.poll() is None
        assert any(row['kind'] == 'native_browser' and row['status'] == 'stopped' for row in result['targets'])
        assert any(row['status'] == 'unconfirmed' for row in result['targets'])
        assert result['status'] == 'unverified'
    finally:
        for process in (child, unrelated):
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)


def test_emergency_pending_native_owner_is_visible_without_final_registration(ws):
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        sweep = emergency.Sweep()
        record = registered(ws, identity=fingerprint(child.pid))
        # Use the same entry point as a browser launch cancelled after creation.
        assert emergency.stop_registered_browser(record, ws.identity)
        child.wait(timeout=5)
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=5)
