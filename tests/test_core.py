import json
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from echo_kit.core import Workspace, KitError, init_workspace, save
from echo_kit.runner import run, verdict
from echo_kit.services import Services
from echo_kit.processes import fingerprint, matches
from echo_kit.runs import report
from echo_kit.cli import main


@pytest.fixture
def ws(tmp_path):
    init_workspace(tmp_path)
    return Workspace(tmp_path)


def script(ws, body, **extra):
    path = ws.root / ('script' + str(len(list(ws.root.glob('script*')))) + '.py')
    path.write_text(body, encoding='utf-8')
    return {'command': [sys.executable, str(path)], **extra}


def result_script(ws, value, **extra):
    return script(ws, 'import os,json\nfrom pathlib import Path\nPath(os.environ["ECHO_RESULT"]).write_text(' + repr(json.dumps(value)) + ')', **extra)


def test_init_does_not_overwrite(ws):
    original = (ws.root / 'echo-kit.toml').read_bytes()
    with pytest.raises(KitError):
        init_workspace(ws.root)
    assert (ws.root / 'echo-kit.toml').read_bytes() == original
    assert '.echo-kit/' in (ws.root / '.gitignore').read_text()


def test_multi_project_local_override_and_environment(ws, tmp_path):
    other = tmp_path / 'elsewhere'
    other.mkdir()
    (ws.state).mkdir(exist_ok=True)
    (ws.state / 'local.toml').write_text('[projects.main]\npath = ' + json.dumps(str(other)) + '\n[environments.qa]\nname="remote"')
    updated = Workspace(ws.root, 'qa')
    assert updated.project('main') == other
    assert updated.cfg['name'] == 'remote'
    with pytest.raises(KitError):
        Workspace(ws.root, 'unknown')


def test_command_array_and_missing_environment(ws):
    with pytest.raises(KitError):
        ws.command({'command': 'echo hello'})
    with pytest.raises(KitError):
        ws.command({'command': ['${ECHO_MISSING_10301}']})


def test_required_evidence_cannot_pass():
    value = {'status': 'passed', 'checks': []}
    assert verdict(value, ['download']) == 'unverified'
    assert verdict({'status': 'passed', 'checks': [{'name': 'x', 'status': 'failed'}]}, []) == 'failed'
    assert verdict({'status': 'passed', 'checks': [{'name': 'x', 'status': 'not_applicable'}]}, ['x']) == 'unverified'


def test_lab_repeat_compare_and_report(ws):
    case = result_script(ws, {'status': 'passed', 'checks': [{'name': 'x', 'status': 'passed'}], 'metrics': {'number': 7}}, required_checks=['x'])
    ws.cfg['cases'] = {'demo': case}
    result = run(ws, 'demo', repeat=2)
    assert len(result) == 2 and result[0]['id'] != result[1]['id']
    assert all(r['status'] == 'passed' for r in result)
    assert all((ws.runs / r['id'] / 'report.html').exists() for r in result)
    assert not list(ws.runs.glob('*/active.json'))


def test_missing_result_is_not_business_success(ws):
    ws.cfg['cases'] = {'demo': script(ws, 'print("done")')}
    assert run(ws, 'demo')[0]['status'] == 'unverified'


def test_timeout_local_process_and_cleanup_failure(ws):
    case = script(ws, 'import time\ntime.sleep(20)', timeout=.2)
    ws.cfg['cases'] = {'slow': case}
    r = run(ws, 'slow')[0]
    assert r['status'] == 'failed'
    assert not (ws.runs / r['id'] / 'active.json').exists()
    case['creates_tasks'] = True
    r = run(ws, 'slow')[0]
    assert r['status'] == 'unverified'
    assert (ws.runs / r['id'] / 'active.json').exists()
    with pytest.raises(KitError):
        run(ws, 'slow')


def test_cleanup_terminal_required(ws):
    cleanup = result_script(ws, {'status': 'passed', 'terminal_confirmed': True})
    ws.cfg['cases'] = {'slow': script(ws, 'import time;time.sleep(20)', timeout=.2, creates_tasks=True, cleanup=cleanup)}
    result = run(ws, 'slow')[0]
    assert result['cleanup'] == 'confirmed'
    assert not list(ws.runs.glob('*/active.json'))


def test_invalid_artifact_rejected(ws):
    ws.cfg['cases'] = {'bad': result_script(ws, {'status': 'passed', 'artifacts': ['../../outside.txt']})}
    result = run(ws, 'bad')[0]
    assert result['status'] == 'blocked'


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def test_service_reuse_external_and_pid_mismatch(ws):
    port = free_port()
    spec = {'command': [sys.executable, '-m', 'http.server', str(port), '--bind', '127.0.0.1'], 'port': port,
            'timeout': 8, 'ready': {'url': f'http://127.0.0.1:{port}/'}}
    ws.cfg['services'] = {'web': spec}
    service = Services(ws)
    try:
        first = service.up(['web'])['web']
        second = service.up(['web'])['web']
        assert first['instance']['identity'] == second['instance']['identity']
        wrong = {**first['instance']['identity'], 'created': 0}
        assert not matches(wrong)
        with ws.lock():
            assert service.status()['web']['ready']
        from echo_kit.core import read
        saved = read(service.file)
        service.file.unlink()
        with pytest.raises(KitError, match='Port occupied'):
            service.up(['web'])
        spec['mode'] = 'external'
        assert service.up(['web'])['web']['ready']
        service.down(['web'])
        assert matches(first['instance']['identity'])
        spec['mode'] = 'managed'
        save(service.file, saved)
    finally:
        service.down(['web'])


def test_lock_concurrency(ws):
    from filelock import Timeout
    with ws.lock():
        with pytest.raises(Timeout):
            with ws.lock():
                pass


def test_dependency_cycle(ws):
    ws.cfg['services'] = {'a': {'depends': ['b']}, 'b': {'depends': ['a']}}
    with pytest.raises(KitError, match='cycle'):
        Services(ws).up(['a'])


def test_html_escapes_adapter_content(ws):
    ws.cfg['cases'] = {'x': result_script(ws, {'status': 'failed', 'checks': [{'name': '<script>x</script>', 'status': 'failed'}]})}
    value = run(ws, 'x')[0]
    text = (ws.runs / value['id'] / 'report.html').read_text()
    assert '<script>' not in text and '&lt;script&gt;' in text


def test_skill_export_and_cli_init(tmp_path, capsys):
    dest = tmp_path / 'skills'
    assert main(['--json', 'skills', 'export', str(dest)]) == 0
    assert len(list(dest.glob('*/SKILL.md'))) == 3
    assert main(['skills', 'export', str(dest)]) == 2
