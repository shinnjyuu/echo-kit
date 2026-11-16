"""Small root-level storage declarations, preserving shared/private provenance."""
import hashlib
import json
import os
from contextlib import contextmanager
from pathlib import Path

from .core import KitError
from .operations import data_root
from .project_docs import absolute

DEFAULTS = {'runs': {'passed_days': 7, 'other_days': 30, 'keep_latest': 3},
            'observations': {'keep_days': 7, 'keep_latest': 3}}


def canonical(path):
    return os.path.normcase(os.path.abspath(path))


def token(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:32]


def _table(value, allowed, label):
    if not isinstance(value, dict) or set(value) - set(allowed):
        raise KitError('Invalid storage configuration: ' + label)
    return value


def settings(ws):
    cfg = _table(ws.cfg.get('storage', {}), ('runs', 'observations', 'caches'), 'storage')
    for label, defaults in DEFAULTS.items():
        values = _table(cfg.get(label, {}), defaults, label)
        if any(type(v) is not int or v < 0 for v in values.values()):
            raise KitError('Storage retention must contain nonnegative integers: ' + label)
    caches = cfg.get('caches', {})
    if not isinstance(caches, dict):
        raise KitError('storage.caches must contain named tables')
    # Check shared declarations before merged/private values hide their source.
    shared = ws.configuration.shared
    for table in [shared, *shared.get('environments', {}).values()]:
        storage = _table(table.get('storage', {}), ('runs', 'observations', 'caches'), 'shared storage')
        declared = storage.get('caches', {})
        if not isinstance(declared, dict):
            raise KitError('storage.caches must contain named tables')
        for value in declared.values():
            _table(value, ('path', 'include', 'keep_days', 'keep_latest'), 'shared cache')
            path = value.get('path')
            if path is not None and (not isinstance(path, str) or absolute(path)
                    or not (ws.root / path).resolve().is_relative_to(ws.root)):
                raise KitError('Shared storage cache paths must stay relative to the workspace')
    for name, cache in caches.items():
        _table(cache, ('path', 'include', 'keep_days', 'keep_latest'), 'cache:' + name)
        if not isinstance(cache.get('path'), str) or not cache['path'].strip():
            raise KitError('Storage cache requires a path: ' + name)
        value = cache['path']
        if os.name != 'nt' and (value.startswith('\\') or (len(value) > 1 and value[1] == ':')):
            raise KitError('Storage cache uses a foreign absolute path: ' + name)
        if value.startswith(('\\\\', '//')):
            raise KitError('Remote storage caches are outside the local cleanup scope')
        path = Path(os.path.abspath(ws.root / value))
        home = Path.home().resolve()
        kit_home = data_root().resolve()
        if (path == Path(path.anchor) or path == home or path == ws.root or path in ws.root.parents
                or path == ws.state or path == ws.state / 'private' or path == kit_home
                or path.is_relative_to(kit_home) or kit_home.is_relative_to(path)
                or any(part.casefold() == '.git' for part in path.parts)):
            raise KitError('Unsafe storage cache root: ' + name)
        patterns = cache.get('include', ['*'])
        if (not isinstance(patterns, list) or not patterns or any(not isinstance(p, str) or not p
                or '/' in p or '\\' in p or p in ('.', '..') or '**' in p for p in patterns)):
            raise KitError('Storage include must match immediate child names: ' + name)
        if any(type(cache.get(k, 0)) is not int or cache.get(k, 0) < 0 for k in ('keep_days', 'keep_latest')):
            raise KitError('Storage cache retention must use nonnegative integers: ' + name)
    return {**{k: {**v, **cfg.get(k, {})} for k, v in DEFAULTS.items()}, 'caches': caches}


def roots(ws):
    cfg = settings(ws)
    result = []
    # Preserve the declared lexical path: ws.runs is resolved for normal
    # execution and would otherwise conceal a junction in an output path.
    for kind, name, path, policy in [('runs', 'runs', ws.root / ws.cfg.get('output', '.echo-kit/runs'), cfg['runs']),
                                     ('observations', 'observations', ws.state / 'observations', cfg['observations']),
                                     *[('cache', 'cache.' + name, ws.root / spec['path'],
                                        {'keep_days': 7, 'keep_latest': 0, 'include': ['*'], **spec})
                                       for name, spec in cfg['caches'].items()]]:
        path = Path(os.path.abspath(path))
        result.append({'id': token([kind, canonical(path)]), 'kind': kind, 'name': name, 'path': str(path),
                       'workspace': str(ws.root), 'environment': ws.environment, 'policy': policy})
    return result


def writer_resources(ws, action, evidence=None):
    if action.startswith('resources.'):
        return {}
    result = {}
    if ws.cfg.get('storage', {}).get('caches'):
        for root in roots(ws):
            if root['kind'] == 'cache':
                result['storage.root:' + canonical(root['path'])] = 'shared'
    if evidence is not None:
        result['storage.unit:' + canonical(evidence)] = 'shared'
    return result


@contextmanager
def cleanup_lease(ws, path):
    # Do not invoke operation.check_legacy: this is also the entry for cleaning
    # a legacy active run. Its original cleanup checks remain authoritative.
    from .operations import Journal
    journal = Journal()
    oid = journal.acquire(ws, 'runs.cleanup.storage', writer_resources(ws, 'runs.cleanup', path))
    try:
        yield
    finally:
        journal.release(oid)
