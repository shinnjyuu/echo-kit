"""Pinned, privately installed native browser dependencies; never use a user's Chrome."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import time
import urllib.error
import urllib.request
import uuid
import zipfile
from importlib.resources import files
from pathlib import Path, PurePosixPath, PureWindowsPath

from filelock import FileLock, Timeout

from . import launches
from .core import KitError, read, save
from .operations import data_root

MAX_EXTRACTED_BYTES = 2 * 1024 ** 3
MAX_ARCHIVE_FILES = 20000


def manifest():
    return json.loads(files('echo_kit').joinpath('resources/native-browser.json').read_text(encoding='utf-8'))


def _platform():
    machine = platform.machine().lower()
    if machine not in ('amd64', 'x86_64'):
        raise KitError('Native browser runtime is unsupported on this architecture; supported: Windows x64, Linux x64')
    system = platform.system()
    if system == 'Windows':
        return 'windows-x64'
    if system == 'Linux':
        return 'linux-x64'
    raise KitError('Native browser runtime is unsupported on this platform; supported: Windows x64, Linux x64')


def _hash(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        while chunk := stream.read(1024 * 1024):
            launches.checkpoint()
            digest.update(chunk)
    return digest.hexdigest()


def _linked(path):
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, 'st_file_attributes', 0) & 0x400)


def _safe_relative(value):
    if not isinstance(value, str) or not value or '\\' in value:
        raise KitError('Unsafe native runtime archive path')
    path = PurePosixPath(value)
    if path.is_absolute() or '..' in path.parts or PureWindowsPath(value).drive or ':' in value:
        raise KitError('Unsafe native runtime archive path')
    return Path(*path.parts)


def _download(spec, target):
    """Fetch exactly the bundled official asset; hash before any extraction or use."""
    expected = int(spec['size'])
    if expected <= 0 or expected > 512 * 1024 ** 2 or not str(spec['url']).startswith('https://'):
        raise KitError('Invalid bundled native runtime asset')
    digest = hashlib.sha256()
    count = 0
    started = time.monotonic()
    try:
        request = urllib.request.Request(spec['url'], headers={'User-Agent': 'Echo-Kit-native-runtime'})
        with urllib.request.urlopen(request, timeout=20) as response, target.open('xb') as output:
            if not response.geturl().startswith('https://'):
                raise KitError('Native runtime download redirected to an insecure address')
            while chunk := response.read(1024 * 1024):
                launches.checkpoint()
                count += len(chunk)
                if count > expected or time.monotonic() - started > 300:
                    raise KitError('Native runtime download exceeded its size or time limit')
                digest.update(chunk)
                output.write(chunk)
        if count != expected or digest.hexdigest() != spec['sha256']:
            raise KitError('Native runtime download failed SHA256 or size verification')
    except (OSError, urllib.error.URLError, TimeoutError):
        raise KitError('Native runtime download failed; retry when the official release source is reachable') from None


def _extract(archive, target):
    with zipfile.ZipFile(archive) as source:
        entries = source.infolist()
        if len(entries) > MAX_ARCHIVE_FILES or sum(item.file_size for item in entries) > MAX_EXTRACTED_BYTES:
            raise KitError('Native runtime archive exceeds extraction limits')
        seen = set()
        for item in entries:
            launches.checkpoint()
            relative = _safe_relative(item.filename)
            identity = os.path.normcase(str(relative))
            if identity in seen:
                raise KitError('Duplicate native runtime archive entry')
            seen.add(identity)
            mode = item.external_attr >> 16
            if stat.S_ISLNK(mode) or (mode and stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR)):
                raise KitError('Native runtime archive contains a link or special file')
            destination = target / relative
            if item.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            with source.open(item) as incoming, destination.open('xb') as outgoing:
                shutil.copyfileobj(incoming, outgoing, 1024 * 1024)
            if os.name != 'nt':
                destination.chmod((mode & 0o755) or 0o644)


def _receipt(directory, spec):
    result = {}
    for current, dirs, names in os.walk(directory, followlinks=False):
        for name in dirs + names:
            if _linked(Path(current) / name):
                raise KitError('Native runtime contains an unexpected filesystem link')
        for name in sorted(names):
            path = Path(current) / name
            if path == directory / 'receipt.json':
                continue
            if not path.is_file():
                raise KitError('Native runtime contains an unexpected special file')
            result[path.relative_to(directory).as_posix()] = {'size': path.stat().st_size, 'sha256': _hash(path)}
    return {'asset_sha256': spec['sha256'], 'files': result}


def _valid(directory, spec):
    try:
        if not directory.is_dir() or _linked(directory):
            return False
        receipt = read(directory / 'receipt.json', {})
        expected = receipt.get('files', {})
        relative = _safe_relative(spec['executable'])
        if receipt.get('asset_sha256') != spec['sha256'] or not expected or relative.as_posix() not in expected:
            return False
        # The receipt binds every installed file, not just the launcher. A damaged
        # browser resource cannot be silently reused as an offline installation.
        actual = _receipt(directory, spec)['files']
        if any(actual.get(name) != details for name, details in expected.items()):
            return False
        # Chrome for Testing writes downloaded spelling dictionaries beside the
        # executable even when its user-data-dir is elsewhere. They are runtime
        # data, not part of the pinned distribution. Keep this exception narrow:
        # original files remain hash-bound and executable/library additions fail.
        dictionary_root = (PurePosixPath(spec['executable']).parent / 'Dictionaries').as_posix()
        for name in actual.keys() - expected.keys():
            relative = PurePosixPath(name)
            if (spec.get('archive') != 'zip' or relative.parent.as_posix() != dictionary_root or
                    not re.fullmatch(r'[A-Za-z]{2,3}(?:-[A-Za-z]{2,4})?-[0-9]+-[0-9]+\.bdic', relative.name) or
                    actual[name]['size'] > 32 * 1024 ** 2):
                return False
        return True
    except launches.EmergencyInterrupted:
        raise
    except (OSError, ValueError, TypeError, KeyError, KitError):
        return False


def _remove_stage(path, parent):
    # Only remove our unique staging directory, never a computed installation root.
    absolute = path.resolve()
    if absolute.parent != parent.resolve() or not path.name.startswith('.stage-') or _linked(path):
        raise KitError('Refused unsafe native runtime staging cleanup')
    shutil.rmtree(path)


def _run_sandbox_setup(helper, browser_directory):
    return subprocess.run(
        [str(helper), '--configure-browser-in-directory=' + str(browser_directory)],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        timeout=30, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
    ).returncode


def _configure_chrome(directory, spec):
    """Apply Chrome's own narrow AppContainer RX capabilities to public binaries.

    Chrome 157's setup.exe supports this sandbox-only operation and returns 78
    on success (chrome/installer/util/util_constants.h). Its implementation grants
    only the Chrome install-files and LPAC install-files capability SIDs read /
    execute access. It neither installs Chrome nor configures a browser profile.
    https://chromium.googlesource.com/chromium/src/+/refs/tags/157.0.8103.78/chrome/installer/setup/configure_app_container_sandbox.cc
    """
    browser_directory = directory / _safe_relative(spec['executable']).parent
    helper = browser_directory / 'setup.exe'
    try:
        relative = helper.relative_to(directory).as_posix()
        expected = read(directory / 'receipt.json', {}).get('files', {}).get(relative)
        if (not expected or not helper.is_file() or _linked(helper) or _linked(browser_directory)
                or not browser_directory.resolve().is_relative_to(directory.resolve())):
            raise KitError('Pinned Chrome sandbox configuration helper is missing or unverified')
        if helper.stat().st_size != expected['size'] or _hash(helper) != expected['sha256']:
            raise KitError('Pinned Chrome sandbox configuration helper failed integrity verification')
        launches.checkpoint()
        status = _run_sandbox_setup(helper.resolve(), browser_directory.resolve())
        launches.checkpoint()
    except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired):
        raise KitError('Could not configure Chrome sandbox permissions; no browser was started') from None
    if status != 78:
        raise KitError('Chrome sandbox permission configuration failed (exit ' + str(status) +
                       '); check access to the pinned runtime directory. No browser was started')


def _ready(directory, component, platform_name, spec):
    if component == 'chrome' and platform_name == 'windows-x64':
        _configure_chrome(directory, spec)
    return directory / _safe_relative(spec['executable'])


def _install(root, component, version, platform_name, spec, offline):
    parent = root / component / version
    parent.mkdir(parents=True, exist_ok=True)
    destination = parent / platform_name
    try:
        with FileLock(str(parent / (platform_name + '.lock')), timeout=60):
            launches.checkpoint()
            if _valid(destination, spec):
                return _ready(destination, component, platform_name, spec)
            if offline:
                raise KitError('Pinned native runtime is unavailable or damaged offline; run browser up without --offline to prepare')
            stage = parent / ('.stage-' + uuid.uuid4().hex)
            stage.mkdir()
            try:
                archive = stage / 'download.partial'
                _download(spec, archive)
                content = stage / 'content'
                content.mkdir()
                if spec.get('archive') == 'zip':
                    _extract(archive, content)
                else:
                    executable = content / _safe_relative(spec['executable'])
                    executable.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(archive, executable)
                executable = content / _safe_relative(spec['executable'])
                if not executable.is_file():
                    raise KitError('Pinned native runtime asset does not contain its expected executable')
                if os.name != 'nt':
                    executable.chmod(executable.stat().st_mode | 0o700)
                save(content / 'receipt.json', _receipt(content, spec))
                launches.checkpoint()
                if destination.exists():
                    # Preserve a damaged cache for diagnosis; never erase unknown files.
                    os.replace(destination, parent / ('.invalid-' + uuid.uuid4().hex))
                os.replace(content, destination)
                return _ready(destination, component, platform_name, spec)
            except (zipfile.BadZipFile, OSError):
                raise KitError('Could not install the pinned native runtime atomically') from None
            finally:
                if stage.exists():
                    _remove_stage(stage, parent)
    except Timeout:
        raise KitError('Timed out waiting for another native runtime installation') from None


def prepare(*, offline=False):
    """Prepare the pinned native binary and Chrome, without starting either."""
    from .updates import offline as offline_requested
    offline = offline_requested(offline)
    platform_name = _platform()
    package = manifest()
    selected = package['platforms'][platform_name]
    root = (data_root() / 'native-runtime').resolve()
    result = {}
    for component in ('agent_browser', 'chrome'):
        version = package[component + '_version']
        executable = _install(root, component, version, platform_name, selected[component], offline)
        result[component] = str(executable.resolve())
        result[component + '_version'] = version
    return result
