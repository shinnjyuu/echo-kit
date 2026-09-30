import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from echo_kit.core import Workspace, KitError, init_workspace, read, save
from echo_kit.runner import run
from echo_kit.services import Services
from echo_kit.processes import fingerprint, matches
from echo_kit import auth


@pytest.fixture
def ws(tmp_path):
    init_workspace(tmp_path)
    return Workspace(tmp_path)


def test_partial_startup_and_timeout_preserve_state(ws):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    ws.cfg['services'] = {
        'good': {'command': [sys.executable, '-m', 'http.server', str(port)], 'port': port, 'ready': {'port': port}},
        'bad': {'command': [sys.executable, '-c', 'import time;time.sleep(10)'], 'depends': ['good'], 'timeout': .2,
                'ready': {'command': [sys.executable, '-c', 'raise SystemExit(1)']}}}
    manager = Services(ws)
    try:
        with pytest.raises(KitError, match='timed out'):
            manager.up(['bad'])
        state = read(manager.file)
        assert set(state) == {'good', 'bad'}
        assert matches(state['good']['identity'])
        assert Services(ws).status()['good']['ready']
        before = state['good']['identity']
        manager.down(['bad', 'good'])
        manager.up(['good'])
        assert read(manager.file)['good']['identity'] != before
    finally:
        manager.down([])


@pytest.mark.skipif(os.name == 'nt', reason='SIGINT transport is platform-specific; runner interruption is separately tested')
def test_actual_cli_sigint_stops_child(ws):
    import signal
    import time
    text = (ws.root / 'echo-kit.toml').read_text()
    text += '\n[cases.slow]\ncommand = ' + json.dumps([sys.executable, '-c', 'import time;time.sleep(30)']) + '\n'
    (ws.root / 'echo-kit.toml').write_text(text)
    p = subprocess.Popen([sys.executable, '-m', 'echo_kit', '--workspace', str(ws.root), '--json', 'lab', 'run', 'slow'], stdout=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 10
        while not list(ws.runs.glob('*/adapter/request.json')) and time.monotonic() < deadline:
            time.sleep(.05)
        time.sleep(.2)
        p.send_signal(signal.SIGINT)
        output = p.communicate(timeout=10)[0]
        assert p.returncode == 130
        assert json.loads(output)[0]['status'] == 'interrupted'
        assert not list(ws.runs.glob('*/active.json'))
    finally:
        if p.poll() is None:
            p.kill()


def test_runner_interruption_cleanup(ws, monkeypatch):
    from echo_kit import runner
    ws.cfg['cases'] = {'x': {'command': [sys.executable]}}
    monkeypatch.setattr(runner, 'execute', lambda *a, **k: {'status': 'interrupted', 'checks': []})
    r = run(ws, 'x')[0]
    assert r['status'] == 'interrupted'
    assert not list(ws.runs.glob('*/active.json'))


def test_refresh_and_secret_transport(ws, monkeypatch):
    adapter = ws.root / 'auth.py'
    adapter.write_text('import sys,json\nq=json.load(sys.stdin)\nprint(json.dumps({"valid":q["action"]!="check", "account":q["account"],"target":q["target"],"state":{"token":"TEST_PRIVATE_TOKEN"}}))')
    ws.cfg['auth'] = {'demo': {'command': [sys.executable, str(adapter)], 'account': 'a', 'target': 'http://local'}}
    monkeypatch.setattr(auth, 'secure_backend', lambda: True)
    monkeypatch.setattr(auth.keyring, 'get_password', lambda *a: '{"token":"old"}')
    written = []
    monkeypatch.setattr(auth.keyring, 'set_password', lambda *a: written.append(a[-1]))
    summary, state = auth.authenticate(ws, 'demo')
    assert state['token'] == 'TEST_PRIVATE_TOKEN' and written
    assert 'TEST_PRIVATE_TOKEN' not in json.dumps(summary)
    assert not ws.state.exists()


def test_required_scope_does_not_pass_empty_command(ws):
    ws.cfg['cases'] = {'x': {'command': [sys.executable, '-c', 'pass'], 'protocol': 'command', 'required_checks': ['business']}}
    assert run(ws, 'x')[0]['status'] == 'unverified'


def test_run_traversal_rejected(ws):
    from echo_kit.runs import locate
    with pytest.raises(KitError):
        locate(ws, '../outside')
