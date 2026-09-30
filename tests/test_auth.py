import json
import sys

import pytest

from echo_kit.core import Workspace, init_workspace, KitError
from echo_kit import auth


@pytest.fixture
def setup(tmp_path, monkeypatch):
    init_workspace(tmp_path)
    ws = Workspace(tmp_path)
    ws.cfg['auth'] = {'user': {'account': 'alice', 'target': 'https://example.invalid', 'command': [sys.executable]}}
    vault = {}
    monkeypatch.setattr(auth, 'secure_backend', lambda: True)
    monkeypatch.setattr(auth.keyring, 'get_password', lambda service, key: vault.get(key))
    monkeypatch.setattr(auth.keyring, 'set_password', lambda service, key, value: vault.update({key: value}))
    monkeypatch.setattr(auth.keyring, 'delete_password', lambda service, key: vault.pop(key, None))
    return ws, vault


def test_reuse_refresh_and_logout(setup, monkeypatch):
    ws, vault = setup
    calls = []
    def invoke(ws, cfg, action, state=None):
        calls.append(action)
        return {'valid': True, 'state': {'token': 'SECRET'}}
    monkeypatch.setattr(auth, 'invoke', invoke)
    summary, _ = auth.authenticate(ws, 'user')
    assert not summary['reused'] and summary['persistent']
    assert 'SECRET' not in json.dumps(summary)
    summary, _ = auth.authenticate(ws, 'user')
    assert summary['reused'] and calls == ['login', 'check']
    auth.authenticate(ws, 'user', 'logout')
    assert not vault
    assert not list(ws.state.rglob('*SECRET*'))


def test_vault_unavailable_is_ephemeral(setup, monkeypatch):
    ws, vault = setup
    monkeypatch.setattr(auth, 'secure_backend', lambda: False)
    monkeypatch.setattr(auth, 'invoke', lambda *args: {'valid': True, 'state': {'token': 'secret'}})
    summary, state = auth.authenticate(ws, 'user')
    assert not summary['persistent'] and state and not vault


def test_environment_binding_changes(setup):
    ws, _ = setup
    _, first = auth.binding(ws, 'user')
    ws.environment = 'another'
    assert auth.binding(ws, 'user')[1] != first


def test_wrong_password_and_identity(setup, monkeypatch):
    ws, _ = setup
    monkeypatch.setattr(auth, 'invoke', lambda *args: {'valid': False})
    with pytest.raises(KitError):
        auth.authenticate(ws, 'user')


def test_real_auth_protocol_identity_rejection(setup):
    ws, _ = setup
    spec = ws.cfg['auth']['user']
    spec['command'] = [sys.executable, '-c', 'import json;print(json.dumps({"valid":True,"account":"bob","target":"https://example.invalid"}))']
    with pytest.raises(KitError, match='mismatch'):
        auth.invoke(ws, spec, 'login')
