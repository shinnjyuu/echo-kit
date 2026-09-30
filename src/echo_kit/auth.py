"""Normal project authentication via stdin/stdout and OS-backed credential vault."""
import hashlib
import json
import subprocess

import keyring

from .core import KitError, required_env


def binding(ws, name):
    cfg = ws.cfg.get('auth', {}).get(name)
    if not cfg or not cfg.get('account') or not cfg.get('target'):
        raise KitError('Auth adapter requires account and target')
    key = hashlib.sha256(json.dumps([ws.identity, ws.environment, name, cfg['account'], cfg['target']]).encode()).hexdigest()
    return cfg, key


def secure_backend():
    backend = keyring.get_keyring()
    module = type(backend).__module__
    # No plaintext / fallback / arbitrary user-installed backend persistence.
    return module.startswith(('keyring.backends.Windows', 'keyring.backends.macOS', 'keyring.backends.SecretService', 'keyring.backends.kwallet'))


def invoke(ws, cfg, action, state=None):
    payload = {'action': action, 'account': cfg['account'], 'target': cfg['target'], 'state': state,
               'credentials': {key: required_env(source) for key, source in cfg.get('credentials', {}).items()}}
    p = subprocess.run(ws.command(cfg), cwd=ws.project(cfg.get('project')), env=ws.env(cfg),
                       input=json.dumps(payload), capture_output=True, text=True, encoding='utf-8', timeout=cfg.get('timeout', 30))
    if p.returncode:
        raise KitError('Authentication adapter failed; private output suppressed')
    try:
        result = json.loads(p.stdout)
        if result.get('account') != cfg['account'] or result.get('target') != cfg['target']:
            raise KitError('Authentication identity or target mismatch')
        return result
    except (ValueError, AttributeError):
        raise KitError('Invalid authentication response')


def authenticate(ws, name, action='login'):
    from .operations import operation
    cfg, _ = binding(ws, name)
    key = hashlib.sha256(json.dumps([ws.identity, ws.environment, cfg['account'], cfg['target']]).encode()).hexdigest()
    with operation(ws, 'auth.' + action, {'auth:' + key: 'exclusive'}):
        return _authenticate(ws, name, action)


def _authenticate(ws, name, action='login'):
    cfg, key = binding(ws, name)
    persistent = secure_backend()
    state = None
    if persistent:
        try:
            stored = keyring.get_password('echo-kit', key)
            state = json.loads(stored) if stored else None
        except Exception:
            persistent = False
    if action == 'logout':
        if state:
            result = invoke(ws, cfg, 'logout', state)
            if not result.get('valid'):
                raise KitError('Remote logout not confirmed')
        if persistent and state:
            keyring.delete_password('echo-kit', key)
        return {'valid': False, 'logged_out': True}, None
    result = invoke(ws, cfg, 'check', state) if state else {'valid': False}
    reused = bool(result.get('valid'))
    if not reused and action != 'check':
        if state:
            result = invoke(ws, cfg, 'refresh', state)
        if not result.get('valid'):
            result = invoke(ws, cfg, 'login')
    if not result.get('valid'):
        raise KitError('Authentication not valid')
    state = result.get('state', state)
    if state is None:
        raise KitError('Authentication returned no state')
    if persistent:
        try:
            keyring.set_password('echo-kit', key, json.dumps(state))
        except Exception:
            persistent = False
    return {'valid': True, 'account': cfg['account'], 'target': cfg['target'], 'reused': reused,
            'persistent': persistent, 'scope': 'stored' if persistent else 'current invocation only'}, state
