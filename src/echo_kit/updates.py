"""Latest stable release discovery and single-command execution through uv."""
import json
import math
import os
import shutil
import subprocess
import sys
import time
from urllib.request import Request, urlopen
from http.client import HTTPException

from filelock import FileLock, Timeout
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

from . import __version__
from .core import KitError, read, save
from .documentation import PACKAGE
from .operations import data_root

INDEX = 'https://pypi.org/simple'
METADATA = 'https://pypi.org/pypi/' + PACKAGE + '/json'
ERROR_CACHE_SECONDS = 300
MAX_METADATA_BYTES = 4 * 1024 * 1024
MIN_RUNTIME = Version('0.4.0')


def offline(requested=False):
    return requested or any(os.environ.get(key, '').lower() in ('1', 'true', 'yes')
                            for key in ('ECHO_KIT_OFFLINE', 'UV_OFFLINE'))


def release_version(value):
    try:
        parsed = Version(value)
    except (InvalidVersion, TypeError):
        raise KitError('Invalid release version')
    if (parsed.is_prerelease or parsed.is_devrelease or parsed.local or parsed.epoch
            or len(parsed.release) != 3 or parsed < MIN_RUNTIME):
        raise KitError('Automatic execution requires a stable Echo Kit version >= 0.4.0')
    return str(parsed)


def fetch_releases():
    request = Request(METADATA, headers={'Accept': 'application/json', 'User-Agent': f'echo-kit/{__version__}'})
    with urlopen(request, timeout=2) as response:
        body = response.read(MAX_METADATA_BYTES + 1)
    if len(body) > MAX_METADATA_BYTES:
        raise ValueError('Release metadata is too large')
    raw = json.loads(body)
    if not isinstance(raw, dict):
        raise ValueError('Invalid release metadata')
    releases = raw.get('releases')
    if not isinstance(releases, dict):
        raise ValueError('Invalid release metadata')
    candidates = {}
    for name, artifacts in releases.items():
        try:
            version = Version(name)
        except InvalidVersion:
            continue
        if version.is_prerelease or version.is_devrelease or version.local or not isinstance(artifacts, list):
            continue
        constraints = [item.get('requires_python') or '' for item in artifacts
                       if isinstance(item, dict) and not item.get('yanked')
                       and (item.get('requires_python') is None or isinstance(item['requires_python'], str))
                       and item.get('packagetype') in ('bdist_wheel', 'sdist')]
        if constraints:
            candidates[str(version)] = constraints
    return candidates


def compatible_versions(releases):
    python_version = '.'.join(map(str, sys.version_info[:3]))
    result = []
    for name, constraints in releases.items():
        if not isinstance(constraints, list) or not all(isinstance(spec, str) for spec in constraints):
            continue
        try:
            version = Version(name)
            if not (version.is_prerelease or version.is_devrelease or version.local) and any(
                    python_version in SpecifierSet(spec) for spec in constraints):
                result.append(version)
        except (InvalidVersion, InvalidSpecifier, TypeError):
            continue
    return sorted(result)


def _snapshot(refresh=False, no_network=False):
    root = data_root() / 'updates'
    path = root / 'pypi.json'

    def cached():
        try:
            value = read(path, {})
            if not isinstance(value, dict) or not isinstance(value.get('releases', {}), dict):
                return {}
            if not isinstance(value.get('attempted_at', 0), (int, float)) or not math.isfinite(value.get('attempted_at', 0)):
                return {}
            return value
        except (OSError, ValueError):
            return {}

    saved = cached()
    now = time.time()
    if offline(no_network):
        return saved, 'offline'
    if not refresh and saved.get('error') and 0 <= now - saved.get('attempted_at', 0) < ERROR_CACHE_SECONDS:
        return saved, 'cached'
    try:
        root.mkdir(parents=True, exist_ok=True)
        with FileLock(str(root / 'check.lock'), timeout=0):
            saved = cached()
            if not refresh and saved.get('error') and 0 <= now - saved.get('attempted_at', 0) < ERROR_CACHE_SECONDS:
                return saved, 'cached'
            try:
                saved = {'releases': fetch_releases(), 'checked_at': now, 'attempted_at': now}
                source = 'network'
            except (OSError, ValueError, TypeError, HTTPException):
                saved = {**saved, 'attempted_at': now, 'error': 'Release check unavailable; existing version retained.'}
                source = 'unavailable'
            save(path, saved)
            return saved, source
    except Timeout:
        return saved, 'busy'
    except OSError:
        return saved, 'unavailable'


def check(current=None, refresh=False, no_network=False):
    current = current or __version__
    snapshot, source = _snapshot(refresh, no_network)
    versions = compatible_versions(snapshot.get('releases', {}))
    latest = str(versions[-1]) if versions else None
    return {'status': 'passed', 'current_version': current, 'running_version': __version__,
            'latest_version': latest, 'update_available': bool(latest and Version(latest) > Version(current)),
            'source': source, 'checked_at': snapshot.get('checked_at'),
            'attempted_at': snapshot.get('attempted_at'), 'error': snapshot.get('error'),
            'stale': source in ('offline', 'unavailable', 'busy') or bool(snapshot.get('error'))}


def command_prefix(version, no_network=False, refresh=False):
    version = release_version(version)
    if version == __version__:
        return [sys.executable, '-I', '-m', 'echo_kit']
    uv = shutil.which('uv')
    if not uv:
        raise KitError('uv is required to prepare another Echo Kit version')
    command = [uv, 'tool', 'run', '--no-config', '--default-index', INDEX,
               '--python', sys.executable, '--from', f'{PACKAGE}=={version}']
    if offline(no_network):
        command.append('--offline')
    elif refresh:
        command += ['--refresh-package', PACKAGE]
    return command + ['echo-kit']


def child_environment():
    env = dict(os.environ)
    # Discovery and installation use the same public package source. Do not
    # modify the user's uv configuration or the business interpreter.
    for name in ('PYTHONPATH', 'UV_INDEX', 'UV_INDEX_URL', 'UV_EXTRA_INDEX_URL', 'UV_DEFAULT_INDEX',
                 'UV_FIND_LINKS', 'UV_NO_INDEX', 'UV_CONSTRAINT', 'UV_OVERRIDE', 'UV_CONFIG_FILE',
                 'ECHO_KIT_EXPECTED_VERSION', 'ECHO_KIT_FORWARDED_VERSION', 'ECHO_KIT_SOURCE_VERSION'):
        env.pop(name, None)
    return env


# Internal subprocess guard, not a project setting or version policy.
FORWARDED = 'ECHO_KIT_FORWARDED_VERSION'


def probe(version, no_network=False):
    version = release_version(version)
    env = child_environment()
    env[FORWARDED] = version
    env['ECHO_KIT_EXPECTED_VERSION'] = version
    try:
        completed = subprocess.run(command_prefix(version, no_network, refresh=not no_network) +
                                   ['--json', 'runtime', 'info'], env=env, capture_output=True,
                                   text=True, encoding='utf-8', timeout=120)
        info = json.loads(completed.stdout)
        if completed.returncode or not isinstance(info, dict) or info.get('package') != PACKAGE or info.get('version') != version:
            raise KitError('New release could not start with the expected version')
    except (OSError, subprocess.TimeoutExpired, ValueError):
        raise KitError('New release preparation failed')
    return info


def startup(raw, no_network=False):
    """Return (child exit code or None, discovery). Never retry a business command."""
    source_version = os.environ.pop('ECHO_KIT_SOURCE_VERSION', None)
    forwarded = os.environ.pop(FORWARDED, None)
    if forwarded:
        if forwarded != __version__:
            raise KitError('Forwarded runtime does not match the prepared version')
        print(f'Echo Kit: executing {__version__} (source: {source_version or "unknown"}); '
              'read echo-kit updates notes and echo-kit guide echo-workbench (or echo-init / echo-lab).', file=sys.stderr)
        return None, {'source': 'forwarded', 'source_version': source_version, 'running_version': __version__, 'stale': False}
    discovery = check(no_network=no_network)
    if discovery['stale']:
        print(f'Echo Kit: latest release not confirmed ({discovery["source"]}); using {__version__}.', file=sys.stderr)
        return None, discovery
    if not discovery['update_available']:
        return None, discovery
    target = discovery['latest_version']
    try:
        probe(target)
        command = command_prefix(target, no_network=True)
    except KitError as error:
        discovery = {**discovery, 'preparation_failed': True, 'preparation_error': str(error)}
        print(f'Echo Kit: could not prepare {target} ({error}); using {__version__}.', file=sys.stderr)
        return None, discovery
    env = child_environment()
    env['ECHO_KIT_SOURCE_VERSION'] = __version__
    env[FORWARDED] = target
    env['ECHO_KIT_EXPECTED_VERSION'] = target
    # Preparation ended above. From this point failures are returned without fallback.
    return subprocess.call(command + raw, env=env), discovery
