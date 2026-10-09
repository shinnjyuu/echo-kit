import json
import os
import time
import sys
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from echo_kit import tray
from echo_kit.core import Workspace, init_workspace, save, KitError
from echo_kit.processes import fingerprint


@pytest.fixture
def ws(tmp_path, monkeypatch):
    monkeypatch.setattr(tray, 'root', lambda: tmp_path / 'tray-data')
    workspace = tmp_path / 'project'
    init_workspace(workspace)
    ws = Workspace(workspace)
    ws.cfg['services'] = {'api': {}, 'remote': {'mode': 'external'}}
    return ws


def browser(ws, **extra):
    from echo_kit.browser import RUNTIME_VERSION
    save(ws.state / 'browser.json', {'id': 'container', 'runtime_version': RUNTIME_VERSION,
         'headless': False, 'viewer_url': 'http://127.0.0.1:19324/vnc.html', **extra})


def containers(ws, **extra):
    return {'container': {'ID': 'container', 'Labels': 'echo-kit.workspace=' + ws.identity, 'State': 'running', **extra}}


def test_snapshot_is_read_only_and_batches_docker(ws, monkeypatch):
    from echo_kit.services import Services
    monkeypatch.setattr(Services, 'status', lambda self: pytest.fail('business health check'))
    identity = fingerprint(os.getpid())
    save(ws.state / 'services-local.json', {n: {'identity': identity} for n in ('api', 'remote')})
    browser(ws)
    tray.register(ws)
    ws.environment = 'other'
    tray.register(ws)
    calls = []
    monkeypatch.setattr(tray, 'container_states', lambda: calls.append(True) or containers(ws))
    rows = tray.snapshot()
    assert len(rows) == 2 and calls == [True]
    assert {r['environment'] for r in rows} == {'local', 'other'}
    assert rows[0]['services'] == [{'name': 'api', 'state': 'running'}]
    assert rows[0]['browser']['viewer_url']


@pytest.mark.parametrize('mode', ['wrong_owner', 'missing', 'timeout', 'headless', 'legacy', 'external'])
def test_container_boundaries(ws, monkeypatch, mode):
    browser(ws, **({'headless': True} if mode == 'headless' else {'runtime_version': -1} if mode == 'legacy' else {}))
    if mode == 'external':
        ws.cfg['browser'] = {'endpoint': 'ws://external:3000'}
    tray.register(ws)
    def inspect():
        if mode == 'external':
            pytest.fail('external queried Docker')
        if mode == 'timeout':
            raise tray.subprocess.TimeoutExpired('docker', 3)
        return {} if mode == 'missing' else containers(ws, **({'Labels': 'echo-kit.workspace=other'} if mode == 'wrong_owner' else {}))
    monkeypatch.setattr(tray, 'container_states', inspect)
    result = tray.snapshot()[0]['browser']
    if mode == 'external':
        assert result is None
    else:
        assert not result['viewer_url']
        if mode == 'timeout':
            assert result['state'] == 'unknown'
            assert tray.has_resources(tray.snapshot(), include_unknown=True)


def test_pid_reuse_not_treated_as_owned():
    identity = fingerprint(os.getpid())
    identity['created'] -= 100
    assert tray.process_state(identity) == 'stopped'


def test_idle_grace_and_unknown():
    timer = tray.IdleTimer()
    assert not timer.expired([], 10)
    assert not timer.expired([], 39)
    assert timer.expired([], 40)
    assert not timer.expired([{'services': [{'state': 'unknown'}], 'browser': None}], 50)
    assert not timer.expired([], 60)
    assert not timer.expired([{'services': [{'state': 'running'}], 'browser': None}], 70)
    assert not timer.expired([], 100)


@pytest.mark.parametrize('url', ['file:///secret', 'http://example.invalid:80', 'http://localhost:bad', 'http://user@localhost:80'])
def test_reject_unsafe_viewer(url):
    assert not tray.safe_viewer(url)


def test_open_checks_current_owner_again(ws, monkeypatch):
    browser(ws)
    key = tray.register(ws)
    monkeypatch.setattr(tray, 'container_states', lambda: containers(ws))
    opened = []
    monkeypatch.setattr(tray.webbrowser, 'open', lambda url, **kw: opened.append(url) or True)
    tray.open_viewer(key)
    assert len(opened) == 1
    monkeypatch.setattr(tray, 'container_states', lambda: containers(ws, Labels=''))
    with pytest.raises(KitError):
        tray.open_viewer(key)
    assert len(opened) == 1


def test_start_single_instance_and_manual_stop_epoch(ws, monkeypatch):
    monkeypatch.setattr(tray.sys, 'platform', 'win32')
    monkeypatch.setattr(tray, 'enabled', lambda: True)
    monkeypatch.setattr(tray, 'snapshot', lambda: [{'services': [{'state': 'running'}], 'browser': None}])
    current, calls = [], []
    monkeypatch.setattr(tray, 'instance', lambda: current[0] if current else None)
    def launch(token):
        calls.append(token)
        current.append({'token': token, 'version': 'test', 'protocol': tray.PROTOCOL})
        return SimpleNamespace(poll=lambda: None)
    monkeypatch.setattr(tray, 'launch', launch)
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _: tray.start(ws), range(6)))
    assert len(calls) == 1 and all(r['tray'] == 'running' for r in results)
    earlier = time.time()
    tray.stop()
    current.clear()
    assert tray.start(ws, earlier)['tray'] == 'dismissed'
    assert tray.start(ws)['tray'] == 'running'
    assert len(calls) == 2


def test_scope_failure_and_nested_scope_shared(ws, monkeypatch):
    seen = []
    monkeypatch.setattr(tray, 'notify', lambda ws: seen.append(tray._scope.get()['started']))
    @tray.startup_scope
    def child(ws):
        raise KitError('partial failure')
    @tray.startup_scope
    def parent(ws):
        child(ws)
    with pytest.raises(KitError):
        parent(ws)
    assert len(seen) == 2 and seen[0] == seen[1]
    assert tray._scope.get() is None


def test_optional_failure_keeps_business_verdict(ws, monkeypatch, capsys):
    monkeypatch.setattr(tray, 'enabled', lambda: True)
    def fail(*args):
        raise OSError('Job denied')
    monkeypatch.setattr(tray, 'start', fail)
    @tray.startup_scope
    def action(ws):
        return {'status': 'passed'}
    assert action(ws)['status'] == 'passed'
    output = capsys.readouterr()
    assert not output.out and 'tray unavailable' in output.err
    assert 'Job denied' in (tray.root() / 'tray.log').read_text()


def test_disabled_and_no_resources_do_not_launch(ws, monkeypatch):
    monkeypatch.setattr(tray.sys, 'platform', 'win32')
    monkeypatch.setattr(tray, 'enabled', lambda: False)
    monkeypatch.setattr(tray, 'launch', lambda *a: pytest.fail('launched'))
    assert tray.start(ws)['tray'] == 'disabled'
    monkeypatch.setattr(tray, 'enabled', lambda: True)
    assert tray.start(ws)['tray'] == 'idle'


def test_platform_status_without_workspace(monkeypatch, capsys, tmp_path):
    from echo_kit.cli import main
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(tray.sys, 'platform', 'linux')
    assert main(['--offline', '--json', 'tray', 'status']) == 2
    assert json.loads(capsys.readouterr().out)['reason'] == 'unsupported_platform'


def test_lab_does_not_notify_but_verify_has_one_scope(ws, monkeypatch):
    scopes = []
    monkeypatch.setattr(tray, 'notify', lambda ws: scopes.append(tray._scope.get()))
    @tray.startup_scope
    def prepare(ws):
        return None
    @tray.startup_scope(verify_only=True)
    def run(ws, name, kind='lab'):
        if kind == 'verify':
            prepare(ws)
            prepare(ws)
    run(ws, 'case')
    assert not scopes
    run(ws, 'case', 'verify')
    assert len(scopes) == 3 and all(scope is scopes[0] for scope in scopes)


def test_core_import_does_not_load_gui():
    import subprocess
    import sys
    result = subprocess.run([sys.executable, '-I', '-c',
        'import sys; import echo_kit.cli, echo_kit.tray, echo_kit.services; '
        'assert "pystray" not in sys.modules and "PIL" not in sys.modules'], capture_output=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows launcher regression')
def test_windowless_venv_launcher_keeps_environment_without_console(ws, monkeypatch, tmp_path):
    """Use the actual venv redirector and OS flags, not a mocked process."""
    import subprocess
    from echo_kit import __version__
    tray.root().mkdir()
    output = tmp_path / 'console.json'
    popen = subprocess.Popen
    observed = []
    def probe(command, **kwargs):
        observed.append((command, kwargs['creationflags']))
        script = ('import ctypes,json,sys; from pathlib import Path; from echo_kit import __version__; '
                  f'Path({str(output)!r}).write_text(json.dumps(dict(console=ctypes.windll.kernel32.GetConsoleWindow(), '
                  'prefix=sys.prefix,version=__version__)))')
        return popen(command[:2] + ['-c', script], **kwargs)
    monkeypatch.setattr(tray.subprocess, 'Popen', probe)
    process = tray.launch('test-token')
    assert process.wait(timeout=10) == 0
    value = json.loads(output.read_text())
    assert value == {'console': 0, 'prefix': sys.prefix, 'version': __version__}
    assert observed[0][0][0].endswith('pythonw.exe')
    assert observed[0][1] & subprocess.CREATE_BREAKAWAY_FROM_JOB
    assert not observed[0][1] & subprocess.DETACHED_PROCESS


def test_missing_gui_launcher_does_not_fall_back_to_console(ws, monkeypatch, tmp_path):
    monkeypatch.setattr(tray.sys, 'executable', str(tmp_path / 'missing/python.exe'))
    monkeypatch.setattr(tray.subprocess, 'Popen', lambda *a, **kw: pytest.fail('console fallback'))
    with pytest.raises(KitError, match='pythonw'):
        tray.launch('test-token')
