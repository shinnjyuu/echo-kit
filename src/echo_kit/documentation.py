"""Offline documentation belonging to the running distribution."""
import hashlib
import json
from importlib.resources import files
from pathlib import Path

from . import __version__
from .core import KitError, read, safe_name, save

PACKAGE = 'shinnjyuu-echo-kit'
MANIFEST = '.echo-kit-skills.json'
RUNTIME_PROTOCOL = 1


def resources():
    result = {}

    def visit(directory, prefix=''):
        for child in directory.iterdir():
            relative = prefix + child.name
            if child.is_dir():
                visit(child, relative + '/')
            elif child.name != '__pycache__' and not child.name.endswith('.pyc'):
                result[relative] = child.read_bytes()

    visit(files('echo_kit').joinpath('skills'))
    return result


def digest(data):
    # Git checkouts may use CRLF; line endings do not change instructions.
    return hashlib.sha256(data.replace(b'\r\n', b'\n')).hexdigest()


def manifest():
    hashes = {name: digest(data) for name, data in sorted(resources().items())}
    return {'schema_version': 1, 'package': PACKAGE, 'version': __version__, 'files': hashes,
            'digest': digest(json.dumps(hashes, sort_keys=True).encode())}


def tool_info():
    return {'package': PACKAGE, 'version': __version__, 'documentation_version': __version__,
            'documentation_digest': manifest()['digest'], 'runtime_protocol': RUNTIME_PROTOCOL}


def catalog():
    return {'status': 'passed', **tool_info(), 'skills': [
        {'name': name.split('/')[0], 'path': name}
        for name in sorted(resources()) if name.endswith('/SKILL.md')]}


def show(name=None):
    relative = safe_name(name) + '/SKILL.md' if name is not None else 'references/protocol.md'
    data = resources()
    if relative not in data:
        raise KitError('Unknown Skill: ' + str(name))
    return {'status': 'passed', **tool_info(), 'path': relative, 'content': data[relative].decode('utf-8')}


def export(destination):
    dest = Path(destination).resolve()
    if dest.exists() and (not dest.is_dir() or any(dest.iterdir())):
        raise KitError('Skill destination is not empty; not overwriting')
    dest.mkdir(parents=True, exist_ok=True)
    for name, data in resources().items():
        target = dest / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    save(dest / MANIFEST, manifest())
    return {'status': 'passed', **tool_info(), 'destination': str(dest), 'manifest': str(dest / MANIFEST)}


def export_status(destination):
    dest = Path(destination).resolve()
    baseline = read(dest / MANIFEST)
    if not isinstance(baseline, dict) or baseline.get('schema_version') != 1 or baseline.get('package') != PACKAGE:
        return {'status': 'unverified', 'reason': 'missing_or_unknown_manifest', 'destination': str(dest),
                'next_step': 'Export to a new empty directory and compare; preserve project edits.'}
    old = baseline.get('files')
    if not isinstance(old, dict) or not all(isinstance(n, str) and isinstance(h, str) for n, h in old.items()):
        raise KitError('Invalid Skill manifest')
    current = manifest()
    modified, missing = [], []
    for name, expected in old.items():
        path = (dest / name).resolve()
        if not path.is_relative_to(dest) or path == dest:
            raise KitError('Invalid Skill manifest path')
        if not path.is_file():
            missing.append(name)
        elif digest(path.read_bytes()) != expected:
            modified.append(name)
    upstream = sorted(n for n in old.keys() | current['files'].keys() if old.get(n) != current['files'].get(n))
    return {'status': 'passed', 'destination': str(dest), 'exported_version': baseline.get('version'),
            **tool_info(), 'update_available': baseline.get('version') != __version__ or bool(upstream),
            'modified': modified, 'missing': missing, 'upstream_changes': upstream,
            'next_step': 'Export to a new empty directory, compare and merge; no files were changed.'}
