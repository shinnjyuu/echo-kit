import io
import json
import queue
import subprocess
import sys
import threading
import time
from argparse import Namespace
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from echo_kit import __version__, browser, cli, emergency, launches, observation, observer_cli
from echo_kit.core import KitError, Workspace, init_workspace, read, save
from echo_kit.operations import Journal


def setup_worker(tmp_path, monkeypatch, body=''):
    init_workspace(tmp_path)
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    ws = Workspace(tmp_path)
    ws.startup_progress = False
    ws.cfg['browser'] = {'backend': 'playwright', 'headless': True}
    state = {'id': 'a' * 64, 'endpoint': 'ws://127.0.0.1:19323/', 'viewer_url': None}
    save(ws.state / 'browser.json', state)
    monkeypatch.setattr(browser, 'manage', lambda *_: state)
    adapter = tmp_path / 'worker.py'
    adapter.write_text('''import json, sys, time
from pathlib import Path
from echo_kit import __version__
settings = json.loads(sys.stdin.readline())
print(json.dumps({'protocol':1,'event':'ready','version':__version__,'operation_id':settings['operation_id']}), flush=True)
''' + body + '''
for line in sys.stdin:
    request = json.loads(line)
    if request['op'] == 'close':
        print(json.dumps({'protocol':1,'status':'closed','id':request['id']}), flush=True)
        break
    print(json.dumps({'protocol':1,'status':'error','id':request['id'],'images':[]}), flush=True)
''', encoding='utf-8')
    args = Namespace(url='http://fixture.invalid', service=[], auth=None, adapter=str(adapter), timeout_ms=20)
    return ws, args


def test_default_request_preserves_state_and_explicit_operations_validate():
    assert observation.validate({'id': '1', 'op': 'observe'}) == 'observe'
    for request in ({'id': '2', 'op': 'navigate', 'url': 'file:///tmp/test'},
                    {'id': '2', 'op': 'observe', 'viewports': [{'width': True, 'height': 500}]},
                    {'id': '2', 'op': 'observe', 'capture': 'element'}, {'op': 'close'}):
        with pytest.raises(KitError):
            observation.validate(request)
    assert observation.public_url('https://example.com/page?token=secret#fragment') == 'https://example.com/page?<redacted>'


def test_secret_metadata_is_bounded_and_redacted():
    assert observation.clean({'info': 'token-private-value'}, ['private-value']) == {'info': 'token-[redacted]'}
    with pytest.raises(KitError):
        observation.clean('x' * 33000, [])


def test_supervisor_closes_on_eof_and_keeps_single_line_protocol(tmp_path, monkeypatch, capsys):
    ws, args = setup_worker(tmp_path, monkeypatch)
    monkeypatch.setattr(sys, 'stdin', io.StringIO('{"id":"1","op":"observe"}\n{"id":"1","op":"observe"}\n{"id":NaN,"op":"observe"}\n'))
    observer_cli.session(ws, args)
    messages = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert messages[0]['event'] == 'ready'
    assert messages[1]['id'] == '1'
    assert messages[2]['error']['code'] == 'invalid_request'
    assert messages[3]['id'] is None and messages[3]['images'] == []
    assert messages[-1]['status'] == 'closed'
    operation, = Journal().list(unfinished=True) or [None]
    assert operation is None
    receipt, = ws.state.glob('observations/*/latest.json')
    assert read(receipt)['status'] == 'closed'


def test_mismatched_worker_response_preserves_uncertain_operation(tmp_path, monkeypatch, capsys):
    body = '''request = json.loads(sys.stdin.readline())
print(json.dumps({'protocol':1,'status':'ok','id':'old','images':[]}), flush=True)
time.sleep(60)
'''
    ws, args = setup_worker(tmp_path, monkeypatch, body)
    monkeypatch.setattr(sys, 'stdin', io.StringIO('{"id":"new","op":"observe"}\n'))
    with pytest.raises(KitError, match='id mismatch'):
        observer_cli.session(ws, args)
    assert Journal().list(unfinished=True)[0]['requires_resolution']
    assert len(capsys.readouterr().out.splitlines()) == 1


@pytest.mark.parametrize('pending_business', [False, True])
def test_idle_worker_is_terminated_by_emergency_and_new_session_can_start(tmp_path, monkeypatch, capsys, pending_business):
    body = "from echo_kit.operations import Journal\nJournal().update(settings['operation_id'],requires_resolution=True)\n" if pending_business else ''
    ws, args = setup_worker(tmp_path, monkeypatch, body)
    waiting = threading.Event()
    release_input = threading.Event()
    class WaitingInput:
        def readline(self, *_):
            waiting.set()
            release_input.wait(30)
            return ''
    monkeypatch.setattr(sys, 'stdin', WaitingInput())
    def stopping():
        assert waiting.wait(20)
        if pending_business:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and not Journal().list(unfinished=True)[0].get('requires_resolution'):
                time.sleep(.025)
            assert Journal().list(unfinished=True)[0].get('requires_resolution')
        return emergency.stop_all()
    with ThreadPoolExecutor(max_workers=1) as pool:
        stopped = pool.submit(stopping)
        try:
            with pytest.raises(launches.EmergencyInterrupted):
                observer_cli.session(ws, args)
            result = stopped.result(timeout=30)
        finally:
            release_input.set()
    assert any(row['kind'] == 'task' and row['status'] in ('stopped', 'already_exited') for row in result['targets'])
    outstanding = Journal().list(unfinished=True)
    if pending_business:
        assert len(outstanding) == 1 and outstanding[0]['requires_resolution']
        assert not outstanding[0]['children_alive']
        return
    assert not outstanding
    monkeypatch.setattr(sys, 'stdin', io.StringIO(''))
    observer_cli.session(ws, args)
    assert json.loads(capsys.readouterr().out.splitlines()[-1])['status'] == 'closed'


def test_receipt_does_not_return_an_old_image(tmp_path):
    receipt = tmp_path / 'latest.json'
    save(receipt, {'id': 'old', 'images': ['old.png']})
    with pytest.raises(KitError):
        observation.read_reply(receipt, 'new', timeout_ms=5)
    save(receipt, {'id': 'new', 'images': []})
    assert observation.read_reply(receipt, 'new')['images'] == []


def test_failed_observe_start_uses_json_lines(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert cli.main(['browser', 'observe', '--url', 'http://example.com']) == 2
    assert json.loads(capsys.readouterr().out)['status'] == 'blocked'


def test_emergency_rejection_before_full_parse_is_a_json_line(capsys):
    launches.directory().mkdir(parents=True, exist_ok=True)
    with launches.stop_lock():
        launches.set_gate('stopping', True)
        assert cli.main(['browser', 'observe', '--url', 'http://fixture']) == 2
    output = capsys.readouterr().out
    assert len(output.splitlines()) == 1
    assert json.loads(output)['reason'] == 'emergency_in_progress'


def test_project_exception_does_not_echo_private_auth(tmp_path, monkeypatch, capsys):
    init_workspace(tmp_path)
    ws = Workspace(tmp_path)
    oid = Journal().acquire(ws, 'browser.observe', {})
    private_value = 'fixture-private-credential'
    settings = {'generation': launches.gate(), 'operation_id': oid, 'endpoint': 'ws://fixture',
                'url': 'http://fixture', 'output': str(tmp_path), 'timeout_ms': 20,
                'auth': {'token': private_value}}
    page = SimpleNamespace(set_viewport_size=lambda *_: None, goto=lambda *_args, **_kwargs: None, on=lambda *_: None)
    @contextmanager
    def context(*_):
        yield SimpleNamespace(set_default_timeout=lambda *_: None,
                              set_default_navigation_timeout=lambda *_: None, new_page=lambda: page)
    monkeypatch.setattr(browser, 'session', context)
    monkeypatch.setattr(sys, 'stdin', io.StringIO(json.dumps(settings) + '\n'))
    def prepare(*_):
        raise RuntimeError(private_value)
    with pytest.raises(KitError, match='worker failed') as error:
        observation.serve_observer(prepare=prepare)
    assert private_value not in str(error.value)
    assert private_value not in capsys.readouterr().out


def test_interrupt_requests_worker_cleanup_before_releasing(tmp_path, monkeypatch):
    ws, args = setup_worker(tmp_path, monkeypatch)
    def interrupt(*_):
        raise KeyboardInterrupt()
    monkeypatch.setattr(observer_cli, 'relay', interrupt)
    with pytest.raises(KeyboardInterrupt):
        observer_cli.session(ws, args)
    assert not Journal().list(unfinished=True)
    session, = ws.state.glob('observations/*/session.json')
    assert read(session)['status'] == 'closed'


def test_unicode_worker_reply_uses_utf8(tmp_path, monkeypatch, capsys):
    body = '''request = json.loads(sys.stdin.readline())
print(json.dumps({'protocol':1,'status':'ok','id':request['id'],'title':'中文与日本語','images':[]},ensure_ascii=False),flush=True)
'''
    ws, args = setup_worker(tmp_path, monkeypatch, body)
    monkeypatch.setenv('PYTHONIOENCODING', 'ascii')
    monkeypatch.setattr(sys, 'stdin', io.StringIO('{"op":"observe","id":"中文"}\n'))
    observer_cli.session(ws, args)
    messages = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert messages[1]['title'] == '中文与日本語'
