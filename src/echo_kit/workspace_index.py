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
            'native_browsers_dir': str(ws.state / 'native-browsers'),
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
            'browser_file': str(state / 'browser.json'),
            'native_browsers_dir': str(state / 'native-browsers')}


def native_records(hint):
    """Read only this known workspace's native registrations, never hinted paths."""
    import math
    records, diagnostics = [], []
    try:
        hint = validate(hint)
        root = Path(hint['workspace'])
        directory = Path(hint['native_browsers_dir'])
        if not directory.exists():
            return records, diagnostics
        if (not directory.is_dir() or directory.is_symlink() or getattr(directory, 'is_junction', lambda: False)()
                or not directory.resolve().is_relative_to(root)):
            raise KitError('Linked native browser registration directory')
        paths = sorted(directory.glob('*.json'))
        for path in paths:
            try:
                if path.is_symlink() or path.stat().st_size > 2 * 1024 * 1024:
                    raise KitError('Invalid native browser registration file')
                record = read(path)
                if (not isinstance(record, dict) or record.get('backend') != 'agent-browser'
                        or safe_name(record.get('session_id', '')) != path.stem
                        or canonical_path(record['workspace']) != canonical_path(root)
                        or not isinstance(record.get('instance_id'), str) or not record['instance_id']
                        or not isinstance(record.get('environment'), str)
                        or type(record.get('headless')) is not bool
                        or not isinstance(record.get('children', []), list)
                        or record.get('actor') is not None and not isinstance(record['actor'], str)):
                    raise KitError('Invalid native browser registration')
                identities = [record.get('identity'), *record.get('children', [])]
                for identity in identities:
                    if (not isinstance(identity, dict) or type(identity.get('pid')) is not int
                            or identity['pid'] <= 0 or type(identity.get('created')) not in (int, float)
                            or not math.isfinite(identity['created'])):
                        raise KitError('Invalid native browser process identity')
                # Tokens, private IPC locations and arbitrary additional fields are never exposed.
                records.append({key: record.get(key) for key in (
                    'backend', 'session_id', 'instance_id', 'workspace', 'environment', 'actor', 'identity',
                    'children', 'status', 'headless', 'operation_id', 'output_dir', 'cdp_endpoint', 'trace_format')})
            except (OSError, ValueError, KeyError, TypeError, KitError):
                diagnostics.append({'reason': 'invalid_native_browser_registration', 'file': str(path)})
    except (OSError, ValueError, KeyError, TypeError, KitError):
        diagnostics.append({'reason': 'native_browser_discovery_unverified'})
    return records, diagnostics


def native_state(record):
    """No configuration, health commands or browser startup during UI polling."""
    try:
        from .native_sessions import inspect_record
        state = inspect_record(record).get('state')
        return state if state in ('running', 'stopped', 'unknown') else 'unknown'
    except (ImportError, OSError, ValueError, KeyError, TypeError, KitError):
        return 'unknown'


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
