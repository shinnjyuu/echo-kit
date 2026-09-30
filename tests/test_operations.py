import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from echo_kit.core import Workspace, KitError, init_workspace, read, save
from echo_kit.operations import Journal, ResourceBusy, operation, service_resources, endpoint
from echo_kit.processes import fingerprint
from echo_kit.runner import run


def workspace(path):
    init_workspace(path)
    return Workspace(path)


def cli(ws, *args):
    return [sys.executable, '-m', 'echo_kit', '--workspace', str(ws.root), '--json', *args]


def wait_for(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(.05)
    raise AssertionError('Timed out waiting for subprocess evidence')


def configure(ws, service_id='api'):
    script = ws.root / 'adapter.py'
    script.write_text('''import os,json,time
from pathlib import Path
root=Path.cwd()
(root/'entered').touch()
while not (root/'finish').exists(): time.sleep(.02)
Path(os.environ['ECHO_RESULT']).write_text(json.dumps({'status':'passed','checks':[]}))
''')
    with (ws.root / 'echo-kit.toml').open('a') as file:
        file.write('\n[services.api]\nmode="external"\nresource_id=' + json.dumps(service_id)
                   + '\n[services.api.ready]\ncommand=["{python}","-c","pass"]'
                   + '\n[cases.demo]\nservices=["api"]\ncommand=["{python}","adapter.py"]\ntimeout=20\n')
    return Workspace(ws.root)


def test_two_cli_processes_and_different_workspaces(tmp_path):
    a = configure(workspace(tmp_path / 'a'))
    b = configure(workspace(tmp_path / 'b'))
    child = subprocess.Popen(cli(a, '--actor', 'A acceptance', 'verify', 'run', 'demo'), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        wait_for(lambda: (a.root / 'entered').exists())
        conflict = subprocess.run(cli(b, 'verify', 'run', 'demo'), capture_output=True, text=True, timeout=5)
        value = json.loads(conflict.stdout)
        assert conflict.returncode == 2 and value['reason'] == 'resource_busy'
        assert value['occupants'][0]['actor'] == 'A acceptance'
        assert not (b.root / 'entered').exists()
        blocked = list(b.runs.glob('*/run.json'))
        assert read(blocked[0])['status'] == 'blocked'
        restart = subprocess.run(cli(b, 'services', 'restart', 'api'), capture_output=True, text=True, timeout=5)
        assert json.loads(restart.stdout)['reason'] == 'resource_busy'
        status = subprocess.run(cli(a, 'services', 'status'), capture_output=True, text=True, timeout=5)
        assert status.returncode == 0 and json.loads(status.stdout)['api']['occupants']
        # Different resources are allowed while A still holds its reservation.
        c = configure(workspace(tmp_path / 'c'), 'other')
        (c.root / 'finish').touch()
        other = subprocess.run(cli(c, 'verify', 'run', 'demo'), capture_output=True, text=True, timeout=5)
        assert other.returncode == 0
        (a.root / 'finish').touch()
        out, err = child.communicate(timeout=10)
        assert child.returncode == 0, err
        assert json.loads(out)[0]['status'] == 'passed'
        (b.root / 'finish').touch()
        reused = subprocess.run(cli(b, 'verify', 'run', 'demo'), capture_output=True, timeout=5)
        assert reused.returncode == 0
    finally:
        (a.root / 'finish').touch()
        child.communicate(timeout=10)


def test_atomic_claim_and_aliases(tmp_path):
    ws = workspace(tmp_path)
    ws.cfg['services'] = {'a': {'ready': {'url': 'http://localhost:8123/health'}, 'depends': ['b']},
                          'b': {'port': 8124}}
    assert endpoint(url='ws://127.0.0.1:8123/') in service_resources(ws, ['a'])
    journal = Journal()
    oid = journal.acquire(ws, 'busy', {endpoint(port=8124): 'exclusive'})
    try:
        with pytest.raises(ResourceBusy):
            journal.acquire(ws, 'all', service_resources(ws, ['a']))
        assert len(journal.list()) == 1
        free = journal.acquire(ws, 'free', {endpoint(port=8123): 'exclusive'})
        journal.release(free)
    finally:
        journal.release(oid)


def test_actual_simultaneous_claims(tmp_path):
    ws = workspace(tmp_path)
    code = '''import sys,time
from pathlib import Path
from echo_kit.core import Workspace
from echo_kit.operations import Journal,ResourceBusy
ws=Workspace(sys.argv[1]); gate=Path(sys.argv[2])
while not gate.exists(): time.sleep(.01)
try:
 oid=Journal().acquire(ws,'race',{'service:race':'exclusive'}); print(oid,flush=True)
except ResourceBusy: sys.exit(2)
'''
    gate = tmp_path / 'gate'
    children = [subprocess.Popen([sys.executable, '-c', code, str(ws.root), str(gate)], stdout=subprocess.PIPE) for _ in range(2)]
    gate.touch()
    for child in children:
        child.communicate(timeout=10)
    assert sorted(c.returncode for c in children) == [0, 2]
    entries = Journal().list()
    assert len(entries) == 1 and entries[0]['state'] == 'needs_cleanup'
    Journal().resolve(entries[0]['id'], 'Fixture did not create business work')


def test_crash_pid_reuse_and_manual_resolution(tmp_path):
    ws = workspace(tmp_path)
    journal = Journal()
    oid = journal.acquire(ws, 'run', {'service:x': 'exclusive'})
    with pytest.raises(KitError, match='alive'):
        journal.resolve(oid, 'not allowed')
    wrong = {**fingerprint(os.getpid()), 'created': 0}
    journal.update(oid, identity=wrong)
    assert journal.show(oid)['state'] == 'needs_cleanup'
    with pytest.raises(ResourceBusy):
        journal.acquire(ws, 'other', {'service:x': 'exclusive'})
    journal.resolve(oid, 'User checked remote task is finished')
    assert journal.show(oid)['resolution_note'] and not journal.show(oid)['held']


def test_browser_shared_use_blocks_down_only(tmp_path):
    ws = workspace(tmp_path)
    journal = Journal()
    a = journal.acquire(ws, 'verify', {'endpoint:loopback:19323': 'shared'})
    b = journal.acquire(ws, 'verify', {'endpoint:loopback:19323': 'shared'})
    with pytest.raises(ResourceBusy):
        journal.acquire(ws, 'down', {'endpoint:loopback:19323': 'exclusive'})
    journal.release(a)
    journal.release(b)


def test_auth_independent_from_service_and_legacy_block(tmp_path, monkeypatch):
    from echo_kit import auth
    ws = workspace(tmp_path)
    ws.cfg['auth'] = {'user': {'account': 'alice', 'target': 'https://example.invalid'}}
    monkeypatch.setattr(auth, 'secure_backend', lambda: False)
    monkeypatch.setattr(auth, 'invoke', lambda *a: {'valid': True, 'state': {'token': 'TEST_SECRET'}})
    oid = Journal().acquire(ws, 'verify', {'service:api': 'exclusive'})
    try:
        assert auth.authenticate(ws, 'user')[0]['valid']
    finally:
        Journal().release(oid)
    assert 'TEST_SECRET' not in json.dumps(Journal().list())
    save(ws.runs / 'old/active.json', {'run_id': 'old'})
    with pytest.raises(KitError, match='legacy'):
        with operation(ws, 'run', {}):
            pass


def test_unfinished_only_blocks_related_run(tmp_path):
    ws = workspace(tmp_path)
    ws.cfg['cases'] = {'pending': {'command': [sys.executable, '-c', 'pass'], 'creates_tasks': True},
                       'independent': {'command': [sys.executable, '-c', 'pass'], 'protocol': 'command'}}
    r = run(ws, 'pending')[0]
    assert r['status'] == 'unverified'
    assert run(ws, 'independent')[0]['status'] == 'passed'
    with pytest.raises(ResourceBusy):
        run(ws, 'pending')


def test_build_directory_release_and_missing_identifier(tmp_path):
    ws = workspace(tmp_path)
    ws.cfg['services'] = {'a': {'port': 8100, 'prepare': {}}, 'b': {'port': 8101, 'prepare': {}}}
    # A real configured prepare command acquires the common build directory.
    for spec in ws.cfg['services'].values():
        spec['prepare'] = {'command': [sys.executable, '-c', 'pass']}
    with operation(ws, 'verify', service_resources(ws, ['a'])) as oid:
        with pytest.raises(ResourceBusy):
            Journal().acquire(ws, 'b', service_resources(ws, ['b']))
        Journal().release_builds(oid)
        other = Journal().acquire(ws, 'b', service_resources(ws, ['b']))
        Journal().release(other)
    ws.cfg['services']['missing'] = {'ready': {'command': ['true']}}
    with pytest.raises(KitError, match='resource_id'):
        service_resources(ws, ['missing'])


def test_cleanup_cli_preserves_original_verdict(tmp_path):
    ws = workspace(tmp_path)
    (ws.root / 'pending.py').write_text("import os;from pathlib import Path;Path(os.environ['ECHO_RESULT']).write_text('{\"status\":\"passed\"}')")
    (ws.root / 'cleanup.py').write_text("import os,json;from pathlib import Path;Path(os.environ['ECHO_RESULT']).write_text(json.dumps({'status':'passed','terminal_confirmed':Path('allow-cleanup').exists()}))")
    with (ws.root / 'echo-kit.toml').open('a') as file:
        file.write('\n[cases.pending]\ncreates_tasks=true\ncommand=["{python}","pending.py"]\n[cases.pending.cleanup]\ncommand=["{python}","cleanup.py"]\n')
    first = subprocess.run(cli(ws, 'verify', 'run', 'pending'), capture_output=True, text=True, timeout=10)
    record = json.loads(first.stdout)[0]
    assert record['status'] == 'unverified'
    oid = record['operation_id']
    assert Journal().show(oid)['state'] == 'needs_cleanup'
    (ws.root / 'allow-cleanup').touch()
    result = subprocess.run(cli(ws, 'runs', 'cleanup', record['id']), capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout
    assert json.loads(result.stdout)['original_status'] == 'unverified'
    assert read(ws.runs / record['id'] / 'run.json')['status'] == 'unverified'
    assert not Journal().show(oid)['held']


def test_live_child_prevents_manual_release(tmp_path):
    ws = workspace(tmp_path)
    journal = Journal()
    oid = journal.acquire(ws, 'crashed-parent', {'service:x': 'exclusive'})
    child = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(20)'])
    try:
        journal.update(oid, identity={**fingerprint(os.getpid()), 'created': 0}, children=[fingerprint(child.pid)])
        with pytest.raises(KitError, match='alive'):
            journal.resolve(oid, 'Cannot release an active child')
    finally:
        child.terminate()
        child.wait(timeout=5)
    journal.resolve(oid, 'Fixture child stopped; no business tasks')


def test_two_service_starts_do_not_lose_state(tmp_path):
    from test_core import free_port
    ws = workspace(tmp_path)
    ports = [free_port(), free_port()]
    assert ports[0] != ports[1]
    with (ws.root / 'echo-kit.toml').open('a') as file:
        for name, port in zip(('a', 'b'), ports):
            file.write(f'\n[services.{name}]\nport={port}\ncommand=["{{python}}","-m","http.server","{port}","--bind","127.0.0.1"]\n[services.{name}.ready]\nport={port}\n')
    children = []
    try:
        children = [subprocess.Popen(cli(ws, 'services', 'up', name), stdout=subprocess.PIPE, stderr=subprocess.PIPE) for name in ('a', 'b')]
        for child in children:
            out, err = child.communicate(timeout=15)
            assert child.returncode == 0, (out, err)
        assert set(read(ws.state / 'services-local.json')) == {'a', 'b'}
    finally:
        for child in children:
            child.communicate(timeout=15)
        subprocess.run(cli(ws, 'services', 'down'), capture_output=True, timeout=15, check=True)
