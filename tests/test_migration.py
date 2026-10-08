import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from echo_kit import __version__, migration
from echo_kit.core import KitError, Workspace, init_workspace, save
from echo_kit.documentation import PACKAGE, digest
from echo_kit.operations import Journal, operation
from echo_kit.runs import report


@pytest.fixture
def ws(tmp_path):
    subprocess.run(['git', 'init', str(tmp_path)], check=True, capture_output=True)
    init_workspace(tmp_path)
    return Workspace(tmp_path)


def legacy(ws):
    content = (Path(__file__).parent / 'fixtures/legacy_launcher.txt').read_bytes()
    assert digest(content) == migration.LEGACY_DIGEST
    (ws.root / 'echo-kit.py').write_bytes(content)
    save(ws.root / 'echo-kit.lock.json', {'schema_version': 1, 'package': PACKAGE, 'version': '0.3.0',
                                       'policy': 'patch', 'launcher_digest': digest(content)})
    save(ws.state / 'tasks/old.json', {'state': 'open', 'historical': 'preserve exactly'})
    return content


def invoke(ws, *args):
    env = dict(os.environ)
    env['PATH'] = str(Path(sys.executable).parent) + os.pathsep + env.get('PATH', '')
    p = subprocess.run([sys.executable, str(ws.root / 'echo-kit.py'), '--offline', '--json', *args],
                       cwd=ws.root.parent, env=env, capture_output=True, text=True, encoding='utf-8', timeout=30)
    assert p.returncode == 0, (p.stdout, p.stderr)
    return json.loads(p.stdout)


def test_standard_entry_migration_backup_and_no_task_requirement(ws):
    original = legacy(ws)
    old_task = (ws.state / 'tasks/old.json').read_bytes()
    old_lock = (ws.root / 'echo-kit.lock.json').read_bytes()
    migrated = migration.setup(ws)
    backup = Path(migrated['backup'])
    assert (backup / 'echo-kit.py').read_bytes() == original
    assert (backup / 'echo-kit.lock.json').read_bytes() == old_lock
    assert not (ws.root / 'echo-kit.lock.json').exists()
    assert (ws.state / 'tasks/old.json').read_bytes() == old_task
    assert subprocess.run(['git', '-C', str(ws.root), 'check-ignore', '-q', str(backup / 'echo-kit.py')]).returncode == 0
    assert migration.setup(ws)['changed'] is False
    assert invoke(ws, 'skills', 'list')['version'] == __version__


def test_new_workspaces_do_not_get_version_artifacts(ws):
    assert migration.setup(ws)['changed'] is False
    assert not (ws.root / 'echo-kit.py').exists()
    assert not (ws.root / 'echo-kit.lock.json').exists()
    assert not (ws.state / 'tasks').exists()


def test_customized_entry_is_preserved_even_if_digest_changed(ws):
    legacy(ws)
    custom = b'# project modifications\n'
    (ws.root / 'echo-kit.py').write_bytes(custom)
    save(ws.root / 'echo-kit.lock.json', {'schema_version': 1, 'package': PACKAGE, 'launcher_digest': digest(custom)})
    with pytest.raises(KitError, match='Project-owned'):
        migration.setup(ws)
    assert (ws.root / 'echo-kit.py').read_bytes() == custom
    assert (ws.root / 'echo-kit.lock.json').exists()


def test_actual_operations_and_unfinished_runs_block_migration(ws):
    legacy(ws)
    with operation(ws, 'verify.run', {'service:test': 'exclusive'}):
        with pytest.raises(KitError, match='actual operations'):
            migration.setup(ws)
    save(ws.runs / 'old/active.json', {'run_id': 'old'})
    with pytest.raises(KitError, match='unfinished runs'):
        migration.setup(ws)
    assert (ws.root / 'echo-kit.lock.json').exists()


def test_run_records_actual_version_without_task_or_toolchain(ws):
    legacy(ws)
    migration.setup(ws)
    with (ws.root / 'echo-kit.toml').open('a', encoding='utf-8') as f:
        f.write('\n[cases.smoke]\nprotocol="command"\ncommand=' + json.dumps([sys.executable, '-c', 'print("ok")']) + '\n')
    result = invoke(ws, 'lab', 'run', 'smoke')[0]
    assert result['status'] == 'passed' and result['tool']['version'] == __version__
    assert 'task_id' not in result
    record = Journal().show(result['operation_id'])
    assert 'task_id' not in record and record['tool_version'] == __version__
    assert not any(key.startswith('toolchain:') for key in record['resources'])
    path = ws.runs / result['id']
    assert 'task_id' not in Path(report(path)).read_text(encoding='utf-8')
    result['task_id'] = 'historical-task'
    save(path / 'run.json', result)
    assert 'historical-task' in Path(report(path)).read_text(encoding='utf-8')


def test_thin_entry_reports_missing_install(ws):
    legacy(ws)
    migration.setup(ws)
    env = dict(os.environ, PATH='')
    p = subprocess.run([sys.executable, str(ws.root / 'echo-kit.py'), '--json', 'runtime', 'info'],
                       env=env, capture_output=True, text=True, encoding='utf-8')
    assert p.returncode == 2 and 'uv tool install' in p.stderr
