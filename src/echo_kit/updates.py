"""Release discovery and isolated, exact-version execution through uv."""
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
from .documentation import PACKAGE, RUNTIME_PROTOCOL, tool_info
from .operations import data_root

INDEX = 'https://pypi.org/simple'
METADATA = 'https://pypi.org/pypi/' + PACKAGE + '/json'
CACHE_SECONDS = 3600
ERROR_CACHE_SECONDS = 300
MAX_METADATA_BYTES = 4 * 1024 * 1024
MIN_RUNTIME = Version('0.2.0')
POLICIES = ('manual', 'patch', 'latest')


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
        raise KitError('Managed tasks require a stable Echo Kit version >= 0.2.0')
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
    if not refresh and 0 <= now - saved.get('attempted_at', 0) < (
            ERROR_CACHE_SECONDS if saved.get('error') else CACHE_SECONDS):
        return saved, 'cached'
    try:
        root.mkdir(parents=True, exist_ok=True)
        with FileLock(str(root / 'check.lock'), timeout=0):
            saved = cached()
            if not refresh and 0 <= now - saved.get('attempted_at', 0) < (
                    ERROR_CACHE_SECONDS if saved.get('error') else CACHE_SECONDS):
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


def check(current=None, policy='manual', refresh=False, no_network=False):
    current = current or __version__
    if policy not in POLICIES:
        raise KitError('Unknown update policy')
    snapshot, source = _snapshot(refresh, no_network)
    versions = compatible_versions(snapshot.get('releases', {}))
    running = Version(current)
    newer = [v for v in versions if v > running]
    allowed = [v for v in newer if policy == 'latest' or (policy == 'patch' and v.release[:2] == running.release[:2])]
    return {'status': 'passed', 'current_version': current, 'running_version': __version__, 'policy': policy,
            'latest_version': str(versions[-1]) if versions else None,
            'update_available': bool(newer), 'candidate_version': str(allowed[-1]) if allowed else None,
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
                 'ECHO_KIT_EXPECTED_VERSION'):
        env.pop(name, None)
    return env


def _json_command(version, args, cwd, no_network=False, refresh=False):
    try:
        env = child_environment()
        env['ECHO_KIT_EXPECTED_VERSION'] = version
        result = subprocess.run(command_prefix(version, no_network, refresh) + args, cwd=cwd,
                                env=env, capture_output=True, text=True, encoding='utf-8', timeout=120)
        if result.returncode:
            raise KitError('Candidate command failed; the selected project version was not changed')
        return json.loads(result.stdout)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        raise KitError('Candidate could not be prepared or returned invalid output; existing version retained')


def probe(version, ws, no_network=False):
    version = release_version(version)
    info = tool_info() if version == __version__ else _json_command(
        version, ['--json', 'runtime', 'info'], ws.root, no_network, refresh=True)
    if not isinstance(info, dict) or info.get('package') != PACKAGE or info.get('version') != version or (
            info.get('runtime_protocol') != RUNTIME_PROTOCOL or info.get('documentation_version') != version
            or not isinstance(info.get('documentation_digest'), str)
            or len(info['documentation_digest']) != 64
            or any(c not in '0123456789abcdef' for c in info['documentation_digest'])):
        raise KitError('Candidate does not support the managed-task protocol')
    # Read-only configuration validation: no services, login or business case.
    args = ['--workspace', str(ws.root)]
    if ws.environment in ws.cfg.get('environments', {}):
        args += ['--environment', ws.environment]
    result = _json_command(version, args + ['--offline', '--json', 'workspace', 'check'], ws.root, no_network)
    if not isinstance(result, dict) or result.get('status') != 'passed':
        raise KitError('Candidate workspace check failed; existing version retained')
    return info
