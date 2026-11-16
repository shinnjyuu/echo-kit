"""Discovery hints only: never evidence that a process or container is owned/alive."""
import hashlib
import sys
from pathlib import Path

from filelock import FileLock

from .core import KitError, read, save, safe_name
from .operations import canonical_path, data_root


def entry(ws):
    return {'workspace': str(ws.root), 'environment': ws.environment,
            'name': str(ws.cfg.get('name', ws.root.name)), 'owner': ws.identity,
            'services_file': str(ws.state / ('services-' + safe_name(ws.environment) + '.json')),
            'browser_file': str(ws.state / 'browser.json'),
            'services': [n for n, s in ws.cfg.get('services', {}).items() if s.get('mode', 'managed') == 'managed'],
            'browser_managed': not bool(ws.cfg.get('browser', {}).get('endpoint'))}


def key(value):
    return hashlib.sha256((canonical_path(value['workspace']) + ':' + value['environment']).encode()).hexdigest()


def register(ws):
    value = entry(ws)
    directory = data_root()
    directory.mkdir(parents=True, exist_ok=True)
    with FileLock(str(directory / 'workspaces.lock'), timeout=2):
        index = read(directory / 'workspaces.json', {})
        if not isinstance(index, dict):
            raise KitError('Invalid workspace discovery index')
        if index.get(key(value)) == value:
            return value
        index[key(value)] = value
        save(directory / 'workspaces.json', index)
    return value


def remember(ws):
    # Registration happens after ownership has been recorded. Discovery failure
    # cannot turn a successful launch into a failure or undo its ownership record.
    try:
        register(ws)
    except Exception:
        print('Echo Kit: workspace discovery index unavailable; current-workspace queries still work.', file=sys.stderr)


def validate(value):
    if not isinstance(value, dict):
        raise ValueError('Invalid workspace hint')
    root = Path(value['workspace'])
    environment = safe_name(value['environment'])
    if not root.is_absolute():
        raise ValueError('Workspace hint must be absolute')
    root = root.resolve()
    state = root / '.echo-kit'
    if not state.resolve().is_relative_to(root):
        raise ValueError('Linked state directory')
    # Never trust arbitrary file locations in a discovery hint.
    return {**value, 'workspace': str(root), 'environment': environment,
            'owner': hashlib.sha256(str(root).encode()).hexdigest()[:20],
            'services_file': str(state / ('services-' + environment + '.json')),
            'browser_file': str(state / 'browser.json')}


def known(references=()):
    result, diagnostics = {}, []
    files = [data_root() / 'workspaces.json', *sorted((data_root() / 'tray').glob('session-*/projects.json'))]
    for file in files:
        try:
            values = read(file, {})
            if not isinstance(values, dict):
                raise ValueError('Invalid index')
            for value in values.values():
                try:
                    value = validate(value)
                    # New cross-platform hints take precedence over old tray hints.
                    result.setdefault(key(value), value)
                except (KeyError, TypeError, ValueError, OSError, KitError):
                    diagnostics.append({'reason': 'invalid_workspace_hint', 'file': str(file)})
        except (ValueError, OSError):
            diagnostics.append({'reason': 'unreadable_workspace_index', 'file': str(file)})
    for ref in references:
        try:
            value = validate(ref)
            result.setdefault(key(value), value)
        except (KeyError, TypeError, ValueError, OSError, KitError):
            diagnostics.append({'reason': 'invalid_service_workspace_reference'})
    return list(result.values()), diagnostics
