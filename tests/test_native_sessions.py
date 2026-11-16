"""Owned-session contracts without starting Chrome or touching business services."""
import json
import os
import secrets
import subprocess
import threading
import uuid
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from multiprocessing.connection import Client, Listener, AuthenticationError
from pathlib import Path
from types import SimpleNamespace

import pytest

from echo_kit import browser, native_sessions as sessions
from echo_kit.core import KitError, Workspace, init_workspace, read, save
from echo_kit.processes import fingerprint


@pytest.fixture
def ws(tmp_path):
    init_workspace(tmp_path)
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    return Workspace(tmp_path)


def record(ws, sid=None, **changes):
    value = {'backend': 'agent-browser', 'session_id': sid or sessions.session_key(ws),
             'instance_id': uuid.uuid4().hex, 'workspace': str(ws.root),
             'environment': ws.environment, 'actor': ws.actor,
             'identity': fingerprint(os.getpid()), 'children': [], 'status': 'ready'}
    value.update(changes)
    save(ws.state / 'native-browsers' / (value['session_id'] + '.json'), value)
    return value


def test_session_identity_separates_workspace_environment_and_actor(ws, tmp_path):
    base = sessions.session_key(ws)
    ws.actor = 'agent-a'
    actor_a = sessions.session_key(ws)
    ws.actor = 'agent-b'
    actor_b = sessions.session_key(ws)
    ws.environment = 'staging'
    staging = sessions.session_key(ws)
    other = tmp_path / 'other'
    init_workspace(other)
    assert len({base, actor_a, actor_b, staging, sessions.session_key(Workspace(other))}) == 5


@pytest.mark.parametrize('field,value', [('workspace', 'different-workspace'),
                                        ('environment', 'other-environment'), ('actor', 'other-agent')])
@pytest.mark.parametrize('operation', ['request', 'up', 'status', 'down'])
def test_explicit_session_cannot_cross_its_recorded_scope(monkeypatch, ws, field, value, operation):
    live = record(ws, **{field: value})
    monkeypatch.setattr(sessions, 'private_directory', lambda path: path.mkdir(parents=True, exist_ok=True))
    monkeypatch.setattr(sessions, 'Client', lambda *a, **kw: pytest.fail('Mismatched session must not connect'))
    with pytest.raises(KitError, match='different workspace, environment or actor'):
        if operation == 'request':
            sessions.request(ws, live['session_id'], {'op': 'exec', 'command': ['snapshot']})
        else:
            sessions.manage(ws, operation, sid=live['session_id'])


def test_start_reuses_owned_live_session_without_creating_a_process(monkeypatch, ws):
    live = record(ws)
    monkeypatch.setattr(sessions, 'private_directory', lambda path: path.mkdir(parents=True, exist_ok=True))
    monkeypatch.setattr(sessions.subprocess, 'Popen', lambda *a, **kw: pytest.fail('Healthy owned session must be reused'))
    assert sessions.start(ws) == live
    with pytest.raises(KitError, match='already running'):
        sessions.start(ws, url='http://localhost:5173/changed')


def test_start_does_not_replace_unconfirmed_existing_processes(monkeypatch, ws):
    live = record(ws, status='starting')
    monkeypatch.setattr(sessions, 'private_directory', lambda path: path.mkdir(parents=True, exist_ok=True))
    monkeypatch.setattr(sessions.subprocess, 'Popen', lambda *a, **kw: pytest.fail('Unconfirmed processes cannot be replaced'))
    assert sessions.inspect_record(live)['state'] == 'unknown'
    with pytest.raises(KitError, match='unconfirmed processes'):
        sessions.start(ws)


def test_stopped_session_request_does_not_recreate_it(monkeypatch, ws):
    live = record(ws)
    monkeypatch.setattr(sessions, 'matches', lambda identity: False)
    monkeypatch.setattr(sessions, 'Client', lambda *a, **kw: pytest.fail('Stopped session cannot execute'))
    monkeypatch.setattr(sessions, 'start', lambda *a, **kw: pytest.fail('No implicit recreation'))
    with pytest.raises(KitError, match='stopped or unverified'):
        sessions.request(ws, live['session_id'], {'op': 'exec', 'command': ['click', '#submit']})


def test_control_identity_change_is_rejected_before_connection(monkeypatch, ws):
    live = record(ws)
    save(sessions.control(ws, live['session_id']) / 'connection.json', {'instance_id': 'replaced'})
    monkeypatch.setattr(sessions, 'Client', lambda *a, **kw: pytest.fail('Replaced instance cannot execute'))
    with pytest.raises(KitError, match='identity changed'):
        sessions.request(ws, live['session_id'], {'op': 'exec', 'command': ['snapshot']})


@pytest.mark.parametrize('sid', ['../other', 'x' * 32, '0' * 33, '', '/absolute'])
def test_session_control_path_is_not_arbitrary(ws, sid):
    with pytest.raises(KitError, match='session id'):
        sessions.control(ws, sid)


def test_status_does_not_expose_private_connection_token(ws):
    live = record(ws)
    save(sessions.control(ws, live['session_id']) / 'connection.json',
         {'token': 'secret-connection-token', 'instance_id': live['instance_id'], 'address': 'private-pipe'})
    result = json.dumps(sessions.manage(ws, 'status'))
    assert 'secret-connection-token' not in result
    assert 'private-pipe' not in result


@pytest.mark.parametrize('body', ['invalid-json', '[]', 'wrong-id', 'wrong-status', 'eof'])
def test_sent_ipc_request_bad_reply_is_unknown_and_never_repeated(ws, body):
    live = record(ws)
    directory = sessions.control(ws, live['session_id'])
    directory.mkdir(parents=True)
    token = secrets.token_bytes(32)
    # The real authenticated transport is exercised; only the worker response is controlled.
    family = 'AF_PIPE' if os.name == 'nt' else 'AF_UNIX'
    address = r'\\.\pipe\echo-test-' + uuid.uuid4().hex if os.name == 'nt' else '/tmp/echo-test-' + uuid.uuid4().hex + '.sock'
    listener = Listener(address, family=family, authkey=token)
    save(directory / 'connection.json', {'address': address, 'family': family, 'token': token.hex(),
                                         'instance_id': live['instance_id']})
    requests, errors = [], []

    def respond():
        try:
            with listener.accept() as connection:
                message = json.loads(connection.recv_bytes())
                requests.append(message)
                response = (b'{' if body == 'invalid-json' else b'[]' if body == '[]' else
                            json.dumps({'id': 'wrong' if body == 'wrong-id' else message['id'],
                                        'status': 'impossible' if body == 'wrong-status' else 'ok'}).encode())
                if body != 'eof':
                    connection.send_bytes(response)
        except BaseException as error:
            errors.append(error)
        finally:
            listener.close()

    thread = threading.Thread(target=respond, daemon=True)
    thread.start()
    with pytest.raises(KitError, match='outcome_unknown'):
        sessions.request(ws, live['session_id'], {'op': 'exec', 'command': ['click', '#submit']}, timeout=2)
    thread.join(3)
    assert not errors
    assert len(requests) == 1


def target(ws, tmp_path):
    output = tmp_path / 'output'
    output.mkdir()
    return sessions.NativeTarget(ws, output, tmp_path / 'control', {}, timeout_ms=100)


def test_target_detects_stopped_process_before_sending_business_action(monkeypatch, ws, tmp_path):
    engine = target(ws, tmp_path)
    engine.processes = [SimpleNamespace(poll=lambda: 1)]
    engine.connection = SimpleNamespace(request=lambda *a, **kw: pytest.fail('Must not send to a stopped browser'))
    with pytest.raises(KitError, match='automatic recreation is disabled'):
        engine.command(['click', '#submit'])


def test_target_unknown_action_does_not_retry_or_expose_browser_error(monkeypatch, ws, tmp_path):
    engine = target(ws, tmp_path)
    calls = []

    def unknown(action, **params):
        calls.append((action, params))
        return {'success': False, 'code': 'outcome_unknown', 'error': 'secret-form-value'}

    engine.connection = SimpleNamespace(request=unknown)
    with pytest.raises(KitError) as caught:
        engine.command(['click', '#submit'])
    assert caught.value.details['outcome_unknown'] is True
    assert 'secret-form-value' not in str(caught.value)
    assert calls == [('click', {'selector': '#submit'})]


def test_target_rejects_foreign_baseline_before_browser_read(ws, tmp_path):
    engine = target(ws, tmp_path)
    foreign = tmp_path / 'private.png'
    foreign.write_bytes(b'private evidence')
    engine.connection = SimpleNamespace(request=lambda *a, **kw: pytest.fail('Foreign baseline must not be read'))
    with pytest.raises(KitError, match='baseline.*observation session'):
        engine.command(['diff', 'screenshot', '--baseline', str(foreign), '--output', str(engine.output / 'diff.png')])


@pytest.mark.parametrize('command', [['screenshot', '{outside}'], ['trace', 'stop', '{outside}']])
def test_target_cannot_write_artifacts_outside_its_unit(ws, tmp_path, command):
    engine = target(ws, tmp_path)
    engine.connection = SimpleNamespace(request=lambda *a, **kw: pytest.fail('Foreign output must not be written'))
    with pytest.raises(KitError, match='owned observation directory'):
        engine.command([value.format(outside=tmp_path / 'foreign.png') for value in command])


def test_explicit_page_read_is_not_persisted_in_receipt():
    response = {'id': 'read-1', 'status': 'ok', 'images': [], 'data': {'value': 'private-input'},
                'session_id': 'session', 'operation_id': 'operation'}
    saved = sessions.receipt(response, 'exec')
    assert 'private-input' not in json.dumps(saved)
    assert saved['operation_id'] == 'operation'
    assert response['data']['value'] == 'private-input'


def test_legacy_configuration_requires_explicit_start_mode(monkeypatch, ws):
    ws.cfg['browser'] = {'endpoint': 'ws://localhost:3109/'}
    monkeypatch.setattr(sessions, 'start', lambda *a, **kw: pytest.fail('Migration must be resolved before startup'))
    monkeypatch.setattr(browser, '_legacy_manage', lambda *a, **kw: pytest.fail('Do not silently use compatibility'))
    with pytest.raises(KitError, match='explicit backend'):
        browser.manage(ws, 'up')


def test_explicit_native_mode_is_forwarded_without_mutating_shared_config(monkeypatch, ws):
    original = {'endpoint': 'ws://localhost:3109/'}
    ws.cfg['browser'] = original
    seen = []

    def native(workspace, action, **options):
        seen.append((workspace.cfg['browser']['backend'], action, options))
        return {'backend': 'agent-browser'}

    monkeypatch.setattr(sessions, 'manage', native)
    assert browser.manage(ws, 'up', backend='agent-browser', url='http://localhost:5173')['backend'] == 'agent-browser'
    assert seen == [('agent-browser', 'up', {'url': 'http://localhost:5173'})]
    assert ws.cfg['browser'] is original


@pytest.fixture
def worker_harness(monkeypatch, ws, request):
    """Run the real authenticated coordinator loop, with an inert browser facade."""
    from echo_kit import auth, native_runtime, services, storage_metadata, tray, workspace_index
    options = getattr(request, 'param', {})
    ready = threading.Event()
    stop = threading.Event()
    calls, updates, failures = [], [], []
    sid = sessions.session_key(ws)
    directory = sessions.control(ws, sid)
    directory.mkdir(parents=True)
    settings = {'workspace': str(ws.root), 'environment': ws.environment, 'actor': ws.actor,
                'session_id': sid, 'instance_id': uuid.uuid4().hex, 'url': 'http://localhost:5173/',
                'services': [], 'auth_name': 'test' if 'auth' in options else None, 'offline': True, 'visual': None,
                'timeout_ms': 100, 'generation': sessions.launches.gate()}
    save(directory / 'settings.json', settings)

    class Journal:
        def protect_evidence(self, *args, **kwargs):
            pass

        def update(self, oid, **values):
            updates.append((oid, values))

    class Services:
        def __init__(self, workspace):
            pass

        def _plan(self, names, jobs):
            return [], 0

        @contextmanager
        def reservation(self, *args, **kwargs):
            yield 'controlled-operation', lambda: None

    class Engine:
        def __init__(self, *args, **kwargs):
            self.identities = [fingerprint(os.getpid())]
            self.endpoint = 'ws://127.0.0.1:1/owned'
            self.viewport_size = {'width': 1440, 'height': 900}

        @contextmanager
        def running(self, url):
            yield self

        def _alive(self):
            if stop.is_set():
                raise KitError('Controlled test teardown')

        def command(self, command):
            calls.append(command)
            if command == ['click', '#unknown']:
                error = KitError('Native action failed: outcome_unknown')
                error.details = {'outcome_unknown': True}
                raise error
            return {'success': True, 'data': {'text': 'controlled-private-input'}}

        def call(self, action, **values):
            calls.append((action, values))
            if action == 'state_load':
                return {'success': True, 'data': options.get('loaded', {'loaded': True, 'skippedOrigins': []})}
            return {'success': True, 'data': {}}

    original_save = sessions.save

    def notifying_save(path, value):
        original_save(path, value)
        if Path(path).name == 'ready.json':
            ready.set()

    monkeypatch.setattr(sessions, 'save', notifying_save)
    monkeypatch.setattr(sessions, 'Journal', Journal)
    monkeypatch.setattr(sessions, 'NativeTarget', Engine)
    monkeypatch.setattr(services, 'Services', Services)
    monkeypatch.setattr(native_runtime, 'prepare', lambda **kw: {'agent_browser_version': '0.43.0', 'chrome_version': '157.0.8103.78'})
    monkeypatch.setattr(storage_metadata, 'own', lambda *a, **kw: None)
    monkeypatch.setattr(workspace_index, 'remember', lambda *a, **kw: None)
    monkeypatch.setattr(tray, 'notify', lambda *a, **kw: None)
    monkeypatch.setattr(auth, 'authenticate', lambda *a, **kw: ({}, options.get('auth')))

    def run():
        try:
            sessions.worker(directory / 'settings.json')
        except BaseException as error:
            if not stop.is_set():
                failures.append(error)

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    assert ready.wait(3), 'Coordinator did not become ready'
    prepared = read(directory / 'ready.json')
    assert prepared['status'] == ('failed' if options.get('expect_failure') else 'ready')
    harness = SimpleNamespace(ws=ws, sid=sid, directory=directory, worker=worker,
                              calls=calls, updates=updates, failures=failures, prepared=prepared)
    try:
        yield harness
    finally:
        if worker.is_alive():
            try:
                sessions.request(ws, sid, {'op': 'close'}, timeout=1)
            except (OSError, KitError):
                stop.set()
        worker.join(3)
        assert not worker.is_alive(), 'Coordinator did not close'


@pytest.mark.parametrize('invalid', [None, b'{not-json', b'[]'])
def test_coordinator_survives_disconnected_or_malformed_client(worker_harness, invalid):
    h = worker_harness
    metadata = read(h.directory / 'connection.json')
    with Client(metadata['address'], family=metadata['family'], authkey=bytes.fromhex(metadata['token'])) as connection:
        if invalid is not None:
            connection.send_bytes(invalid)
    result = sessions.request(h.ws, h.sid, {'op': 'exec', 'command': ['snapshot']}, timeout=2)
    assert result['status'] == 'ok'
    assert h.calls == [['snapshot']]
    assert not h.failures


def test_coordinator_preserves_unknown_outcome_and_does_not_repeat(worker_harness):
    h = worker_harness
    response = sessions.request(h.ws, h.sid, {'op': 'exec', 'command': ['click', '#unknown']}, timeout=2)
    assert response['status'] == 'blocked'
    assert h.calls == [['click', '#unknown']]
    assert h.updates == [('controlled-operation', {'requires_resolution': True})]
    assert h.worker.is_alive()


def test_coordinator_rejects_reused_ids_without_replaying_action(worker_harness):
    h = worker_harness
    message = {'id': 'same-action', 'op': 'exec', 'command': ['click', '#submit']}
    assert sessions.request(h.ws, h.sid, message, timeout=2)['status'] == 'ok'
    assert sessions.request(h.ws, h.sid, message, timeout=2)['status'] == 'blocked'
    assert h.calls == [['click', '#submit']]


def test_invalid_close_does_not_close_existing_context(worker_harness):
    h = worker_harness
    assert sessions.request(h.ws, h.sid, {'id': '', 'op': 'close'}, timeout=2)['status'] == 'blocked'
    assert sessions.request(h.ws, h.sid, {'op': 'exec', 'command': ['snapshot']}, timeout=2)['status'] == 'ok'
    assert h.worker.is_alive()


def test_coordinator_delivers_explicit_read_without_persisting_body(worker_harness):
    h = worker_harness
    response = sessions.request(h.ws, h.sid, {'op': 'exec', 'command': ['get', 'text', '#panel']}, timeout=2)
    assert response['data'] == {'text': 'controlled-private-input'}
    latest = h.ws.state / 'observations/controlled-operation/latest.json'
    assert 'controlled-private-input' not in latest.read_text()
    assert read(latest)['status'] == 'ok'


def test_unresponsive_unauthenticated_client_does_not_block_other_clients(worker_harness):
    h = worker_harness
    metadata = read(h.directory / 'connection.json')
    stalled = Client(metadata['address'], family=metadata['family'], authkey=None)
    try:
        response = sessions.request(h.ws, h.sid, {'op': 'exec', 'command': ['snapshot']}, timeout=2)
        assert response['status'] == 'ok'
        assert h.calls == [['snapshot']]
    finally:
        stalled.close()


def test_wrong_authentication_does_not_stop_accepting_clients(worker_harness):
    h = worker_harness
    metadata = read(h.directory / 'connection.json')
    with pytest.raises(AuthenticationError):
        Client(metadata['address'], family=metadata['family'], authkey=b'incorrect-authentication-key')
    response = sessions.request(h.ws, h.sid, {'op': 'exec', 'command': ['snapshot']}, timeout=2)
    assert response['status'] == 'ok'
    assert h.calls == [['snapshot']]


def test_concurrent_clients_receive_only_their_own_replies(worker_harness):
    h = worker_harness
    ready = threading.Barrier(6)

    def execute(index):
        ready.wait(3)
        return sessions.request(h.ws, h.sid, {'id': f'client-{index}', 'op': 'exec',
                                              'command': ['get', 'text', f'#item-{index}']}, timeout=3)

    with ThreadPoolExecutor(max_workers=6) as executor:
        results = list(executor.map(execute, range(6)))
    assert [value['id'] for value in results] == [f'client-{index}' for index in range(6)]
    assert all(value['status'] == 'ok' for value in results)
    assert len(h.calls) == 6


def test_client_authentication_timeout_is_before_business_execution(tmp_path):
    family = 'AF_PIPE' if os.name == 'nt' else 'AF_UNIX'
    address = r'\\.\pipe\echo-kit-stall-' + uuid.uuid4().hex if os.name == 'nt' else '/tmp/echo-kit-stall-' + uuid.uuid4().hex + '.sock'
    listener = Listener(address, family=family, authkey=None)
    release = threading.Event()
    received, errors = [], []

    def accept_without_challenge():
        try:
            with listener.accept() as connection:
                release.wait(2)
                if connection.poll(.1):
                    try:
                        received.append(connection.recv_bytes())
                    except EOFError:
                        pass
        except (OSError, EOFError) as error:
            errors.append(error)
        finally:
            listener.close()

    server = threading.Thread(target=accept_without_challenge, daemon=True)
    server.start()
    try:
        with pytest.raises(KitError, match='before sending.*no action was executed'):
            sessions.connect_client(address, family=family, authkey=b'never-sent', timeout=.05)
    finally:
        release.set()
        server.join(3)
    assert not received


VALID_STATE = {'cookies': [{'name': 'session', 'value': 'private-cookie', 'domain': 'localhost', 'path': '/',
                            'expires': -1, 'httpOnly': True, 'secure': False, 'sameSite': 'Lax'}],
               'origins': [{'origin': 'http://localhost:5173',
                            'localStorage': [{'name': 'identity', 'value': 'private-local-storage'}]}]}


def test_supported_browser_authentication_state_contract():
    assert sessions.storage_state(VALID_STATE) == VALID_STATE
    assert sessions.storage_state({'storage_state': VALID_STATE}) == VALID_STATE


@pytest.mark.parametrize('field,value', [('httpOnly', 'false'), ('secure', 1), ('expires', float('nan')),
                                        ('expires', float('inf')), ('sameSite', 'unexpected')])
def test_cookie_attribute_types_are_validated_before_native_import(field, value):
    state = {'cookies': [{**VALID_STATE['cookies'][0], field: value}], 'origins': []}
    with pytest.raises(KitError, match='cookie attributes'):
        sessions.storage_state(state)


@pytest.mark.parametrize('origin', [None, 42, [], 'http://localhost:invalid'])
def test_invalid_origin_type_or_port_is_a_clean_authentication_error(origin):
    with pytest.raises(KitError, match='authentication origin'):
        sessions.storage_state({'cookies': [], 'origins': [{'origin': origin, 'localStorage': []}]})


@pytest.mark.parametrize('value', [None, 'opaque-token', {'token': 'private-token'}, {'cookies': {}},
    {'cookies': [], 'origins': {}}, {'cookies': [None], 'origins': []},
    {'cookies': [{'name': 'session', 'value': 'secret', 'domain': 'localhost', 'path': 'relative'}], 'origins': []},
    {'cookies': [], 'origins': [{'origin': 'https://user:password@example.com', 'localStorage': []}]},
    {'cookies': [], 'origins': [{'origin': 'file:///private', 'localStorage': []}]},
    {'cookies': [], 'origins': [{'origin': 'https://example.com/path', 'localStorage': []}]},
    {'cookies': [], 'origins': [{'origin': 'https://example.com', 'localStorage': [{'name': 'a', 'value': 3}]}]},
    {'cookies': [], 'origins': [{'origin': 'https://example.com', 'localStorage': [], 'sessionStorage': []}]},
])
def test_unsupported_authentication_is_rejected_without_echoing_secrets(value):
    with pytest.raises(KitError) as caught:
        sessions.storage_state(value)
    assert 'private-token' not in str(caught.value)
    assert 'user:password' not in str(caught.value)


@pytest.mark.parametrize('worker_harness', [{'auth': VALID_STATE}], indirect=True)
def test_authentication_is_loaded_privately_then_target_url_is_restored(worker_harness):
    h = worker_harness
    assert h.calls[0][0] == 'state_load'
    assert h.calls[1] == ('navigate', {'url': 'http://localhost:5173/'})
    assert not (h.directory / 'authentication.json').exists()
    public = h.ws.state / 'native-browsers' / (h.sid + '.json')
    assert 'private-cookie' not in public.read_text()
    assert 'private-local-storage' not in public.read_text()


@pytest.mark.parametrize('worker_harness', [
    {'auth': VALID_STATE, 'loaded': {'loaded': True, 'skippedOrigins': ['http://localhost:5173']}, 'expect_failure': True},
    {'auth': VALID_STATE, 'loaded': {'loaded': False, 'skippedOrigins': []}, 'expect_failure': True},
], indirect=True)
def test_incomplete_authentication_does_not_mark_session_ready(worker_harness):
    h = worker_harness
    assert h.prepared['status'] == 'failed'
    assert 'not completely restored' in h.prepared['error']
    assert not (h.directory / 'authentication.json').exists()
    assert len(h.calls) == 1
