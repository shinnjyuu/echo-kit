import json
import os
import subprocess
import sys

import pytest
from filelock import FileLock, Timeout

from echo_kit import __version__, tasks, updates
from echo_kit.cli import main
from echo_kit.core import KitError, Workspace, init_workspace, read, save
from echo_kit.documentation import tool_info
from echo_kit.operations import Journal, canonical_path, operation


@pytest.fixture
def ws(tmp_path):
    init_workspace(tmp_path)
    workspace = Workspace(tmp_path)
    tasks.setup(workspace)
    return workspace


@pytest.fixture
def fake_probe(monkeypatch):
    monkeypatch.setattr(updates, 'probe', lambda version, *a, **kw: {**tool_info(), 'version': version,
                                                                  'documentation_version': version})


def available(monkeypatch, version='0.2.1'):
    monkeypatch.setattr(updates, 'check', lambda current=None, policy='manual', **kw: {
        'status': 'passed', 'current_version': current, 'latest_version': version,
        'update_available': True, 'candidate_version': version if policy != 'manual' else None,
        'checked_at': 10, 'source': 'network', 'stale': False})


def test_task_boundary_defers_upgrade_and_records_version(ws, fake_probe, monkeypatch):
    first = tasks.start(ws, 'first')
    available(monkeypatch)
    second = tasks.start(ws, 'second')
    assert second['version'] == first['version'] == __version__
    assert second['adoption']['state'] == 'deferred'
    assert tasks.project_version(ws)['version'] == __version__
    tasks.finish(ws, first['id'])
    tasks.finish(ws, second['id'])
    third = tasks.start(ws, 'third')
    assert third['version'] == '0.2.1'
    assert third['adoption']['state'] == 'upgraded'
    assert tasks.project_version(ws)['previous_version'] == __version__
    assert tasks.load_task(ws, first['id'])['version'] == __version__


def test_failed_candidate_preserves_project_version(ws, fake_probe, monkeypatch):
    available(monkeypatch)
    before = (ws.root / tasks.LOCKFILE).read_bytes()
    def probe(version, *args, **kwargs):
        if version == '0.2.1':
            raise KitError('candidate failed validation')
        return tool_info()
    monkeypatch.setattr(updates, 'probe', probe)
    result = tasks.start(ws)
    assert result['version'] == __version__ and result['adoption']['state'] == 'failed'
    assert (ws.root / tasks.LOCKFILE).read_bytes() == before
    assert not any(r['held'] for r in Journal().list())


def test_manual_policy_and_stale_check_do_not_auto_upgrade(ws, fake_probe, monkeypatch):
    tasks.setup(ws, 'manual')
    available(monkeypatch)
    first = tasks.start(ws)
    assert first['version'] == __version__
    tasks.finish(ws, first['id'])
    tasks.setup(ws, 'patch')
    monkeypatch.setattr(updates, 'check', lambda *a, **kw: {'candidate_version': '0.2.1', 'stale': True})
    assert tasks.start(ws)['version'] == __version__


def test_upgrade_cannot_overlap_unmanaged_operations(ws, fake_probe, monkeypatch):
    available(monkeypatch)
    with operation(ws, 'lab.run', {}):
        with pytest.raises(KitError, match='pending operations'):
            tasks.apply(ws, '0.2.1')
    assert tasks.project_version(ws)['version'] == __version__
    journal = Journal()
    oid = journal.acquire(ws, 'updates.apply', {'toolchain:' + canonical_path(ws.root): 'exclusive'})
    try:
        with pytest.raises(KitError, match='occupied'):
            with operation(ws, 'services.up', {}):
                pytest.fail('operation ran during update')
    finally:
        journal.release(oid)


def test_task_finish_requires_cleanup_and_keeps_evidence(ws, fake_probe):
    task = tasks.start(ws)
    run_dir = ws.runs / 'pending'
    save(run_dir / 'run.json', {'task_id': task['id']})
    save(run_dir / 'active.json', {'run_id': 'pending'})
    with pytest.raises(KitError, match='pending work'):
        tasks.finish(ws, task['id'])
    assert (run_dir / 'active.json').exists()
    assert tasks.load_task(ws, task['id'])['state'] == 'open'
    with FileLock(str(tasks.task_path(ws, task['id'])) + '.lock'):
        with pytest.raises(Timeout):
            tasks.finish(ws, task['id'])


def test_setup_preserves_modified_launcher(ws):
    launcher = ws.root / tasks.LAUNCHER
    launcher.write_text('# project-owned edit\n', encoding='utf-8')
    before = (ws.root / tasks.LOCKFILE).read_bytes()
    with pytest.raises(KitError, match='Project-owned'):
        tasks.setup(ws, 'latest')
    assert launcher.read_text() == '# project-owned edit\n'
    assert (ws.root / tasks.LOCKFILE).read_bytes() == before


def test_task_cli_routes_new_commands_before_old_parser(ws, fake_probe, monkeypatch, capfd):
    task = tasks.start(ws)
    record = tasks.load_task(ws, task['id'])
    record['version'] = '0.2.1'
    record['tool']['version'] = '0.2.1'
    save(tasks.task_path(ws, task['id']), record)
    helper = ws.root / 'future.py'
    helper.write_text('import json,sys;print(json.dumps({"args":sys.argv[1:]}))', encoding='utf-8')
    monkeypatch.setattr(updates, 'command_prefix', lambda *a: [sys.executable, str(helper)])
    args = ['--workspace', str(ws.root), '--task', task['id'], '--json', 'future-command']
    assert main(args) == 0
    assert json.loads(capfd.readouterr().out)['args'] == args


def test_generated_entry_runs_full_task_and_records_provenance(ws):
    config = ws.root / 'echo-kit.toml'
    with config.open('a', encoding='utf-8') as stream:
        stream.write('\n[cases.smoke]\nprotocol="command"\ncommand=' + json.dumps([sys.executable, '-c', 'print("ok")']) + '\n')
    entry = [sys.executable, str(ws.root / tasks.LAUNCHER)]
    def invoke(*args, code=0):
        result = subprocess.run(entry + list(args), cwd=ws.root.parent, env=os.environ.copy(), capture_output=True,
                                text=True, encoding='utf-8', timeout=30)
        assert result.returncode == code, (result.stdout, result.stderr)
        return json.loads(result.stdout)
    task = invoke('--offline', '--json', 'task', 'start', '--label', 'local smoke')
    assert task['version'] == __version__
    docs = invoke('--task', task['id'], '--json', 'skills', 'show', 'echo-workbench')
    assert docs['documentation_digest'] == task['tool']['documentation_digest']
    result = invoke('--task', task['id'], '--offline', '--json', 'lab', 'run', 'smoke')[0]
    assert result['status'] == 'passed' and result['task_id'] == task['id']
    assert result['tool']['version'] == __version__ and result['update']['source'] == 'offline'
    journal = Journal().show(result['operation_id'])
    assert journal['task_id'] == task['id'] and journal['tool_version'] == __version__
    assert invoke('--json', 'task', 'finish', task['id'])['state'] == 'finished'
    blocked = subprocess.run(entry + ['--task', task['id'], '--json', 'lab', 'run', 'smoke'],
                             capture_output=True, text=True, timeout=10)
    assert blocked.returncode == 2


def test_task_environment_and_documentation_are_pinned(ws, fake_probe, monkeypatch):
    task = tasks.start(ws)
    wrong = Workspace(ws.root)
    wrong.environment = 'other'
    with pytest.raises(KitError, match='environment differs'):
        with tasks.bind(wrong, task['id']):
            pass
    monkeypatch.setattr(tasks, 'tool_info', lambda: {**tool_info(), 'documentation_digest': '0' * 64})
    with pytest.raises(KitError, match='documentation changed'):
        with tasks.bind(ws, task['id']):
            pass
