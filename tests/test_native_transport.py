import json
import os
import socket
import threading

import pytest

from echo_kit import native_transport as transport
from echo_kit.core import KitError


def daemon_connection(monkeypatch, tmp_path, responder):
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(('127.0.0.1', 0))
    listener.listen(4)
    listener.settimeout(3)
    address = listener.getsockname()
    requests, failures = [], []

    def worker():
        try:
            with listener.accept()[0] as connection:
                reader = connection.makefile('rb')
                request = json.loads(reader.readline())
                requests.append(request)
                responder(connection, request)
        except BaseException as error:
            failures.append(error)
        finally:
            listener.close()

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()

    def connect(self):
        return socket.create_connection(address, timeout=self.timeout)

    monkeypatch.setattr(transport.AgentConnection, '_socket', connect)
    return transport.AgentConnection(tmp_path, 'owned', timeout=1), thread, requests, failures


def test_request_uses_matching_id_and_exact_fields(monkeypatch, tmp_path):
    def reply(connection, request):
        connection.sendall(json.dumps({'id': request['id'], 'success': True,
                                       'data': {'path': '/local/shot.png'}}).encode() + b'\n')

    client, worker, requests, failures = daemon_connection(monkeypatch, tmp_path, reply)
    result = client.request('screenshot', path='/local/shot.png', fullPage=False)
    worker.join(3)
    assert not failures
    assert len(requests) == 1
    assert requests[0] == {'id': result['id'], 'action': 'screenshot', 'path': '/local/shot.png', 'fullPage': False}
    assert result['data']['path'] == '/local/shot.png'


@pytest.mark.parametrize('response', ['eof', 'wrong_id', 'invalid_json', 'invalid_success'])
def test_sent_action_is_not_retried_when_reply_is_unusable(monkeypatch, tmp_path, response):
    def reply(connection, request):
        if response == 'wrong_id':
            connection.sendall(b'{"id":"old","success":true}\n')
        elif response == 'invalid_json':
            connection.sendall(b'not JSON\n')
        elif response == 'invalid_success':
            connection.sendall(json.dumps({'id': request['id'], 'success': 'true'}).encode() + b'\n')

    client, worker, requests, failures = daemon_connection(monkeypatch, tmp_path, reply)
    result = client.request('click', selector='#submit')
    worker.join(3)
    assert result['code'] == 'outcome_unknown'
    assert result['success'] is False
    assert len(requests) == 1
    assert not failures


def test_connection_failure_does_not_launch_daemon(monkeypatch, tmp_path):
    def unavailable(self):
        raise ConnectionRefusedError()

    monkeypatch.setattr(transport.AgentConnection, '_socket', unavailable)
    assert transport.AgentConnection(tmp_path, 'owned').request('snapshot')['code'] == 'stopped'


def test_response_timeout_does_not_repeat_action(monkeypatch, tmp_path):
    release = threading.Event()

    def busy(connection, request):
        release.wait(3)

    client, worker, requests, failures = daemon_connection(monkeypatch, tmp_path, busy)
    client.timeout = .05
    try:
        assert client.request('click', selector='#submit')['code'] == 'outcome_unknown'
    finally:
        release.set()
        worker.join(3)
    assert len(requests) == 1
    assert not failures


@pytest.mark.skipif(os.name == 'nt', reason='Unix domain sockets are validated on Linux')
def test_unix_reads_owned_socket_path(tmp_path):
    path = tmp_path / 'owned.sock'
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(path))
    listener.listen()
    connection = transport.AgentConnection(tmp_path, 'owned')._socket()
    accepted, _ = listener.accept()
    connection.close()
    accepted.close()
    listener.close()


def test_response_limit_is_not_a_success(monkeypatch, tmp_path):
    monkeypatch.setattr(transport, 'MAX_MESSAGE', 512)

    def reply(connection, request):
        connection.sendall(json.dumps({'id': request['id'], 'success': True,
                                       'data': 'x' * 1024}).encode() + b'\n')

    client, worker, requests, _ = daemon_connection(monkeypatch, tmp_path, reply)
    assert client.request('snapshot')['code'] == 'outcome_unknown'
    worker.join(3)
    assert len(requests) == 1


def test_tcp_reads_actual_port_file(monkeypatch, tmp_path):
    # Replace only the module's platform view; never change pathlib/os globally.
    from types import SimpleNamespace
    monkeypatch.setattr(transport, 'os', SimpleNamespace(name='nt'))
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    listener.listen()
    (tmp_path / 'owned.port').write_text(str(listener.getsockname()[1]))
    connection = transport.AgentConnection(tmp_path, 'owned')._socket()
    accepted, _ = listener.accept()
    connection.close()
    accepted.close()
    listener.close()


@pytest.mark.parametrize('value', ['0', '65536', 'host:1234', '-1', '12345678901234567'])
def test_bad_port_metadata_does_not_connect(monkeypatch, tmp_path, value):
    from types import SimpleNamespace
    monkeypatch.setattr(transport, 'os', SimpleNamespace(name='nt'))
    (tmp_path / 'owned.port').write_text(value)
    assert transport.AgentConnection(tmp_path, 'owned').request('snapshot')['code'] == 'stopped'


@pytest.mark.parametrize(('command', 'expected'), [
    (['open', 'http://localhost:5173/path'], {'action': 'navigate', 'url': 'http://localhost:5173/path'}),
    (['snapshot', '-i', '--compact', '--depth', '2', '--selector', '#app'],
     {'action': 'snapshot', 'interactive': True, 'compact': True, 'maxDepth': 2, 'selector': '#app'}),
    (['click', '@e2'], {'action': 'click', 'selector': '@e2'}),
    (['fill', '#input', '--config'], {'action': 'fill', 'selector': '#input', 'value': '--config'}),
    (['get', 'text', '#title'], {'action': 'gettext', 'selector': '#title'}),
    (['get', 'attr', '#link', 'href'], {'action': 'getattribute', 'selector': '#link', 'attribute': 'href'}),
    (['get', 'url'], {'action': 'url'}),
    (['wait', '#ready', '--timeout', '3000'], {'action': 'wait', 'selector': '#ready', 'timeout': 3000}),
    (['wait', '--load', 'domcontentloaded'], {'action': 'waitforloadstate', 'state': 'domcontentloaded'}),
    (['wait', '--fn', 'window.ready'], {'action': 'waitforfunction', 'expression': 'window.ready'}),
    (['screenshot', 'view.png', '--selector', '#panel'], {'action': 'screenshot', 'format': 'png', 'path': 'view.png', 'selector': '#panel'}),
    (['diff', 'screenshot', '--baseline', 'base.png', '--output', 'diff.png', '--threshold', '0.2'],
     {'action': 'diff_screenshot', 'baseline': 'base.png', 'output': 'diff.png', 'threshold': .2}),
    (['trace', 'start'], {'action': 'trace_start'}),
    (['trace', 'stop', 'trace.json'], {'action': 'trace_stop', 'path': 'trace.json'}),
    (['tab', 'list'], {'action': 'tab_list'}),
    (['tab', 'A1'], {'action': 'tab_switch', 'tabId': 'A1'}),
])
def test_translate_pinned_command_contract(command, expected):
    assert transport.translate(command) == expected


@pytest.mark.parametrize('command', [[], ['open'], ['open', 'file:///private/passwords'],
    ['open', 'https://name:secret@example.com'], ['connect', '9222'], ['close'], ['launch'],
    ['--cdp', '9222', 'snapshot'], ['snapshot', '--config', 'user.json'], ['install'], ['session'],
    ['auth', 'save'], ['tab', 'new'], ['tab', 'close'], ['window', 'new'],
    ['trace', 'start', 'extra'], ['screenshot', 'one.png', 'two.png'],
    ['diff', 'screenshot', '--baseline', 'base.png'],
    ['diff', 'screenshot', '--baseline', 'a', '--output', 'b', '--threshold', 'NaN'],
    ['wait', '--url', '/foo', '#target'], ['snapshot', '--depth', '-1'],
])
def test_translate_rejects_takeover_lifecycle_and_unknown_options(command):
    with pytest.raises(KitError):
        transport.translate(command)
