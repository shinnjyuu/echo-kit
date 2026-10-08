"""Offline documentation belonging to the running distribution."""
import hashlib
import json
from importlib.resources import files
from pathlib import Path
from packaging.version import Version, InvalidVersion

from . import __version__
from .core import KitError, read, safe_name, save

PACKAGE = 'shinnjyuu-echo-kit'
MANIFEST = '.echo-kit-skills.json'


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
            'documentation_digest': manifest()['digest']}


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


def notes(since=None):
    entries = json.loads(resources()['references/releases.json'])
    entries = sorted((e for e in entries if Version(e['version']) <= Version(__version__)),
                     key=lambda e: Version(e['version']))
    try:
        lower = Version(since) if since is not None else None
    except InvalidVersion as error:
        raise KitError('Invalid --since version') from error
    selected = [e for e in entries if (Version(e['version']) > lower if lower is not None
                                     else e['version'] == __version__)]
    warning = None
    if lower is not None and lower < Version(entries[0]['version']):
        warning = 'History before ' + entries[0]['version'] + ' is not bundled; this is not a complete upgrade history.'
    if lower is not None and lower > Version(__version__):
        warning = '--since is newer than the executing package; no later history is available.'
    return {'status': 'passed', **tool_info(), 'since': since, 'releases': selected,
            'coverage': {'first': entries[0]['version'], 'last': entries[-1]['version']}, 'warning': warning}


def guide(name):
    skill, protocol, release = show(name), show(), notes()
    return {'status': 'passed', **tool_info(), 'skill': skill, 'protocol': protocol, 'updates': release}


def render_notes(result):
    parts = [result['warning']] if result.get('warning') else []
    for entry in result['releases']:
        parts.append('## ' + entry['version'])
        parts.extend(key + ': ' + entry[key] for key in
                     ('changes', 'configuration', 'applies_when', 'steps', 'no_change_when'))
    return '\n\n'.join(parts) or 'No release notes in the requested range.'


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
