"""Query work is bounded by selected services and unfinished process identities."""
import json
import os

import pytest

from echo_kit import operations, services
from echo_kit.cli import main
from echo_kit.core import KitError, Workspace, init_workspace, read, save
from echo_kit.operations import Journal, ResourceBusy, endpoint
from echo_kit.processes import fingerprint


def workspace(tmp_path):
    init_workspace(tmp_path)
    ws = Workspace(tmp_path)
    ws.cfg['services'] = {f's{i}': {'mode': 'external', 'port': 23000 + i,
                                  'ready': {'port': 23000 + i}} for i in range(18)}
    return ws


def test_large_history_only_inspects_unfinished_processes(tmp_path, monkeypatch):
    ws = workspace(tmp_path)
    journal = Journal()
    first = journal.acquire(ws, 'history', {})
    journal.release(first)
    template = read(journal.file(first))
    for i in range(7199):
        record = {**template, 'id': f'history-{i:04}'}
        journal.file(record['id']).write_text(json.dumps(record), encoding='utf-8')
    active = journal.acquire(ws, 'active', {endpoint(port=23000): 'shared'})
    stale = journal.acquire(ws, 'stale', {endpoint(port=23001): 'shared'})
    journal.update(stale, identity={**fingerprint(os.getpid()), 'created': 0},
                   children=[fingerprint(os.getpid())])
    empty = journal.acquire(ws, 'unfinished-without-resources', {})

    process_calls = []
    original_matches = operations.matches
    def matched(identity):
        process_calls.append(identity)
        return original_matches(identity)
    monkeypatch.setattr(operations, 'matches', matched)
    resource_calls = []
    original_resources = operations.service_resources
    def resources(ws, names, **kwargs):
        resource_calls.append(tuple(names))
        return original_resources(ws, names, **kwargs)
    monkeypatch.setattr(operations, 'service_resources', resources)
    result = services.Services(ws).status(probe=False)
    assert len(result) == 18 and len(resource_calls) == 18
    assert len(process_calls) == 4  # three owners plus the live child; no historical PIDs
    assert result['s0']['occupants'][0]['id'] == active
    assert result['s1']['occupants'][0]['id'] == stale
    assert result['s1']['occupants'][0]['state'] == 'needs_cleanup'
    assert result['s1']['occupants'][0]['children_alive']
    assert empty in {r['id'] for r in journal.list(unfinished=True)}
    assert len(list(journal.root.glob('*.json'))) == 7203  # history remains intact


def test_unfinished_cli_preserves_history_and_empty_operations(tmp_path, monkeypatch, capsys):
    ws = workspace(tmp_path)
    journal = Journal()
    finished = journal.acquire(ws, 'finished', {})
    journal.release(finished)
    empty = journal.acquire(ws, 'empty', {})
    journal.update(empty, identity={**fingerprint(os.getpid()), 'created': 0})
    held = journal.acquire(ws, 'stale', {'service:test': 'exclusive'})
    journal.update(held, identity={**fingerprint(os.getpid()), 'created': 0})
    # Even inconsistent finished + nonempty held records must not disappear.
    journal.update(held, state='finished')
    monkeypatch.chdir(tmp_path / 'echo')
    for flags, expected in (([], {finished, empty, held}), (['--unfinished'], {empty, held})):
        assert main(['--offline', '--json', 'operations', 'list', *flags]) == 0
        data = json.loads(capsys.readouterr().out)
        assert set(data) == {'operations'}
        assert {r['id'] for r in data['operations']} == expected
        assert all('owner_alive' in r and 'query' in r for r in data['operations'])
    assert main(['--offline', '--json', 'operations', 'show', finished]) == 0
    assert json.loads(capsys.readouterr().out)['state'] == 'finished'


@pytest.mark.parametrize('problem', ['json', 'held', 'state', 'finished', 'read'])
def test_invalid_records_never_mean_unoccupied(tmp_path, monkeypatch, problem):
    ws = workspace(tmp_path)
    journal = Journal()
    oid = journal.acquire(ws, 'fixture', {})
    record = read(journal.file(oid))
    if problem == 'read':
        original_read = operations.read
        def denied(path, *args):
            if path == journal.file(oid):
                raise PermissionError('private content must not leak')
            return original_read(path, *args)
        monkeypatch.setattr(operations, 'read', denied)
    elif problem == 'json':
        journal.file(oid).write_text('{private content must not leak', encoding='utf-8')
    else:
        record.update(state='finished', held={})
        record[problem] = [] if problem == 'held' else 'unknown'
        save(journal.file(oid), record)
    with pytest.raises(KitError) as error:
        journal.acquire(ws, 'must-not-start', {'service:unrelated': 'exclusive'})
    assert 'private content' not in str(error.value)
    assert len(list(journal.root.glob('*.json'))) == 1


def test_status_selection_and_per_query_source_cache(tmp_path, monkeypatch):
    ws = workspace(tmp_path)
    for spec in ws.cfg['services'].values():
        spec['mode'] = 'managed'
    identity = fingerprint(os.getpid())
    instances = {name: {'identity': identity, 'source': {'commit': 'before'},
                         'origin': {}, 'references': []} for name in ws.cfg['services']}
    names_read = []
    def selected(self, names=None):
        names_read.append(names)
        return {name: instances[name] for name in names}
    monkeypatch.setattr(services.Services, 'instances', selected)
    snapshot_calls = []
    def snapshot(path):
        snapshot_calls.append(path)
        return {'commit': 'before'} if len(snapshot_calls) == 1 else {'commit': 'after'}
    monkeypatch.setattr(services, 'source_snapshot', snapshot)
    probes = []
    monkeypatch.setattr(services, 'healthy', lambda ws, spec: probes.append(spec['port']) or True)
    manager = services.Services(ws)
    first = manager.status(names=['s1', 's0', 's1'])
    assert list(first) == ['s1', 's0'] and probes == [23001, 23000]
    assert len(snapshot_calls) == 1
    assert not any(v['source_changed_since_start'] for v in first.values())
    second = manager.status(probe=False, names=['s0', 's1'])
    assert len(snapshot_calls) == 2  # no cache survives into another query
    assert all(v['source_changed_since_start'] for v in second.values())
    assert all(v['ready'] is None for v in second.values())
    assert probes == [23001, 23000]
    with pytest.raises(KitError, match='Unknown service'):
        manager.status(names=['s0', 'missing'])
    assert len(names_read) == 2 and len(snapshot_calls) == 2


def test_occupancy_is_fresh_after_release_and_downgrade(tmp_path):
    ws = workspace(tmp_path)
    journal = Journal()
    resources = {endpoint(port=23000): 'exclusive'}
    first = journal.acquire(ws, 'first', resources)
    assert services.Services(ws).status(probe=False, names=['s0'])['s0']['occupants']
    with pytest.raises(ResourceBusy):
        journal.acquire(ws, 'blocked', resources)
    journal.downgrade_services(first, {endpoint(port=23000): 'shared'})
    second = journal.acquire(ws, 'reader', {endpoint(port=23000): 'shared'})
    journal.release(first)
    assert [r['id'] for r in journal.list(unfinished=True)] == [second]
    with pytest.raises(ResourceBusy):
        journal.acquire(ws, 'writer', resources)
    journal.release(second)
    assert services.Services(ws).status(probe=False, names=['s0'])['s0']['occupants'] == []
    third = journal.acquire(ws, 'writer', resources)
    journal.release(third)


def test_status_cli_queries_only_named_services(tmp_path, monkeypatch, capsys):
    workspace(tmp_path)
    (tmp_path / 'echo-kit.toml').write_text('''schema_version=1
[services.one]
mode="external"
port=23000
[services.one.ready]
port=23000
[services.two]
mode="external"
port=23001
[services.two.ready]
port=23001
''', encoding='utf-8')
    calls = []
    monkeypatch.setattr(services, 'healthy', lambda ws, spec: calls.append(spec['port']) or True)
    assert main(['--offline', '--json', '--workspace', str(tmp_path), 'services', 'status', 'one']) == 0
    assert list(json.loads(capsys.readouterr().out)) == ['one']
    assert calls == [23000]
    assert main(['--offline', '--json', '--workspace', str(tmp_path), 'services', 'status', 'two', 'missing']) == 2
    capsys.readouterr()
    assert calls == [23000]
