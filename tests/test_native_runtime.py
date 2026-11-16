import hashlib
import io
import stat
import threading
import zipfile
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor

import pytest

from echo_kit import native_runtime as runtime
from echo_kit.core import KitError


def fake_assets(monkeypatch):
    monkeypatch.delenv('ECHO_KIT_OFFLINE', raising=False)
    monkeypatch.delenv('UV_OFFLINE', raising=False)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr('chrome/chrome.exe', b'isolated chrome')
        archive.writestr('chrome/resources.pak', b'packaged browser resource')
        archive.writestr('chrome/setup.exe', b'pinned Chrome sandbox permission helper')
    bodies = {'agent_browser': b'isolated agent browser', 'chrome': buffer.getvalue()}
    specs = {}
    for name, body in bodies.items():
        specs[name] = {'url': 'https://example.invalid/' + name,
                       'sha256': hashlib.sha256(body).hexdigest(), 'size': len(body),
                       'executable': 'agent-browser.exe' if name == 'agent_browser' else 'chrome/chrome.exe'}
    specs['chrome']['archive'] = 'zip'
    package = {'schema_version': 1, 'agent_browser_version': '0.43.0',
               'chrome_version': '157.0.8103.78', 'platforms': {'windows-x64': specs}}
    monkeypatch.setattr(runtime, 'manifest', lambda: package)
    monkeypatch.setattr(runtime, '_platform', lambda: 'windows-x64')
    requests = []

    class Response(io.BytesIO):
        def geturl(self):
            return 'https://example.invalid/download'

    def open_asset(request, **kwargs):
        name = request.full_url.rsplit('/', 1)[-1]
        requests.append(name)
        return Response(bodies[name])

    monkeypatch.setattr(runtime.urllib.request, 'urlopen', open_asset)
    monkeypatch.setattr(runtime, '_run_sandbox_setup', lambda helper, directory: 78)
    return package, bodies, requests


def test_windows_sandbox_uses_only_the_verified_chrome_helper_and_directory(monkeypatch):
    fake_assets(monkeypatch)
    invocations = []
    monkeypatch.setattr(runtime, '_run_sandbox_setup', lambda helper, directory: invocations.append((helper, directory)) or 78)
    installed = runtime.prepare()
    expected_directory = runtime.Path(installed['chrome']).parent
    assert invocations == [(expected_directory / 'setup.exe', expected_directory)]
    private = runtime.data_root() / 'browser-control/owned/profile'
    private.mkdir(parents=True)
    token = private.parent / 'token.json'
    token.write_text('private-connection-token')
    before = private.stat().st_mode, token.read_bytes(), token.stat().st_mode
    runtime.prepare(offline=True)
    assert invocations[-1] == (expected_directory / 'setup.exe', expected_directory)
    assert (private.stat().st_mode, token.read_bytes(), token.stat().st_mode) == before


@pytest.mark.parametrize('status', [0, 79, 1])
def test_sandbox_configuration_failure_blocks_preparation(monkeypatch, status):
    fake_assets(monkeypatch)
    monkeypatch.setattr(runtime, '_run_sandbox_setup', lambda helper, directory: status)
    with pytest.raises(KitError, match='sandbox permission configuration failed'):
        runtime.prepare()


def test_missing_verified_sandbox_helper_does_not_use_path_fallback(monkeypatch):
    package, _, _ = fake_assets(monkeypatch)
    installed = runtime.prepare()
    parent = runtime.Path(installed['chrome']).parent
    (parent / 'setup.exe').unlink()
    monkeypatch.setattr(runtime, '_run_sandbox_setup', lambda *a: pytest.fail('Missing bundled setup must not fall back to PATH'))
    with pytest.raises(KitError, match='helper is missing or unverified'):
        runtime._configure_chrome(parent.parent, package['platforms']['windows-x64']['chrome'])


def test_sandbox_helper_command_is_hidden_bounded_and_not_shell_expanded(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(runtime.subprocess, 'run', lambda argv, **kw: seen.append((argv, kw)) or SimpleNamespace(returncode=78))
    helper = tmp_path / 'public runtime/setup.exe'
    directory = helper.parent
    assert runtime._run_sandbox_setup(helper, directory) == 78
    assert seen[0][0] == [str(helper), '--configure-browser-in-directory=' + str(directory)]
    assert seen[0][1]['timeout'] == 30
    assert not seen[0][1].get('shell')
    assert seen[0][1]['stdout'] == runtime.subprocess.DEVNULL


def test_prepare_pinned_assets_and_offline_reuse(monkeypatch):
    package, _, requests = fake_assets(monkeypatch)
    result = runtime.prepare()
    assert result['agent_browser_version'] == package['agent_browser_version']
    assert result['chrome_version'] == package['chrome_version']
    assert runtime.Path(result['agent_browser']).read_bytes() == b'isolated agent browser'
    assert runtime.Path(result['chrome']).read_bytes() == b'isolated chrome'
    assert all(runtime.Path(result[name]).is_absolute() for name in ('agent_browser', 'chrome'))
    assert runtime.prepare(offline=True) == result
    assert requests == ['agent_browser', 'chrome']


def test_offline_missing_does_not_download(monkeypatch):
    _, _, requests = fake_assets(monkeypatch)
    with pytest.raises(KitError, match='offline'):
        runtime.prepare(offline=True)
    assert requests == []


@pytest.mark.parametrize('variable', ['ECHO_KIT_OFFLINE', 'UV_OFFLINE'])
def test_offline_environment_missing_runtime_does_not_download(monkeypatch, variable):
    _, _, requests = fake_assets(monkeypatch)
    monkeypatch.setenv(variable, '1')
    with pytest.raises(KitError, match='offline'):
        runtime.prepare()
    assert requests == []


def test_corrupt_resource_cannot_be_reused_offline(monkeypatch):
    _, _, requests = fake_assets(monkeypatch)
    result = runtime.prepare()
    resource = runtime.Path(result['chrome']).parent / 'resources.pak'
    resource.write_bytes(b'bad')
    with pytest.raises(KitError, match='damaged offline'):
        runtime.prepare(offline=True)
    repaired = runtime.prepare()
    assert repaired == result
    assert resource.read_bytes() == b'packaged browser resource'
    assert requests == ['agent_browser', 'chrome', 'chrome']
    assert len(list(resource.parent.parent.parent.glob('.invalid-*'))) == 1


def test_hash_mismatch_never_publishes_installation(monkeypatch):
    package, _, _ = fake_assets(monkeypatch)
    package['platforms']['windows-x64']['agent_browser']['sha256'] = '0' * 64
    with pytest.raises(KitError, match='SHA256'):
        runtime.prepare()
    parent = runtime.data_root() / 'native-runtime/agent_browser/0.43.0'
    assert not (parent / 'windows-x64').exists()
    assert not list(parent.glob('.stage-*'))


def test_concurrent_prepare_installs_each_component_once(monkeypatch):
    _, _, requests = fake_assets(monkeypatch)
    barrier = threading.Barrier(3)

    def prepare():
        barrier.wait(timeout=3)
        return runtime.prepare()

    with ThreadPoolExecutor(max_workers=3) as executor:
        results = list(executor.map(lambda _: prepare(), range(3)))
    assert results[0] == results[1] == results[2]
    assert sorted(requests) == ['agent_browser', 'chrome']


@pytest.mark.parametrize('name', ['../outside', '/outside', 'C:/outside', 'x/../../outside'])
def test_archive_boundary_rejects_invalid_entries(tmp_path, name):
    archive = tmp_path / 'payload.zip'
    with zipfile.ZipFile(archive, 'w') as output:
        output.writestr(name, b'never extracted')
    destination = tmp_path / 'safe'
    destination.mkdir()
    with pytest.raises(KitError, match='Unsafe'):
        runtime._extract(archive, destination)
    assert list(destination.rglob('*')) == []


def test_backslash_in_archive_path_is_not_a_separator():
    with pytest.raises(KitError, match='Unsafe'):
        runtime._safe_relative('x\\outside')


def test_archive_rejects_symlinks(tmp_path):
    archive = tmp_path / 'link.zip'
    with zipfile.ZipFile(archive, 'w') as output:
        entry = zipfile.ZipInfo('chrome/link')
        entry.create_system = 3
        entry.external_attr = (stat.S_IFLNK | 0o777) << 16
        output.writestr(entry, '../outside')
    destination = tmp_path / 'safe'
    destination.mkdir()
    with pytest.raises(KitError, match='link'):
        runtime._extract(archive, destination)


def test_installed_extra_files_invalidate_receipt(monkeypatch):
    fake_assets(monkeypatch)
    installed = runtime.prepare()
    (runtime.Path(installed['chrome']).parent / 'injected.dll').write_bytes(b'not in the release')
    with pytest.raises(KitError, match='offline'):
        runtime.prepare(offline=True)


def test_chrome_generated_dictionary_does_not_invalidate_pinned_installation(monkeypatch):
    _, _, requests = fake_assets(monkeypatch)
    installed = runtime.prepare()
    dictionaries = runtime.Path(installed['chrome']).parent / 'Dictionaries'
    dictionaries.mkdir()
    (dictionaries / 'en-US-10-2.bdic').write_bytes(b'runtime spelling data')
    assert runtime.prepare(offline=True) == installed
    assert requests == ['agent_browser', 'chrome']
    # This exception must not hide a changed original distribution file.
    runtime.Path(installed['chrome']).write_bytes(b'changed launcher')
    with pytest.raises(KitError, match='offline'):
        runtime.prepare(offline=True)


@pytest.mark.parametrize('name', ['injected.dll', 'unrecognized.bdic', 'en-US-10-2.bdic.exe'])
def test_dictionary_cache_exception_does_not_allow_other_extra_files(monkeypatch, name):
    fake_assets(monkeypatch)
    installed = runtime.prepare()
    dictionaries = runtime.Path(installed['chrome']).parent / 'Dictionaries'
    dictionaries.mkdir()
    (dictionaries / name).write_bytes(b'unexpected file')
    with pytest.raises(KitError, match='offline'):
        runtime.prepare(offline=True)


def test_emergency_during_cache_verification_is_not_reported_as_corruption(monkeypatch):
    fake_assets(monkeypatch)
    runtime.prepare()

    def cancelled(path):
        raise runtime.launches.EmergencyInterrupted()

    monkeypatch.setattr(runtime, '_hash', cancelled)
    with pytest.raises(runtime.launches.EmergencyInterrupted):
        runtime.prepare(offline=True)


def test_unsupported_platform_is_explicit(monkeypatch):
    monkeypatch.setattr(runtime.platform, 'machine', lambda: 'arm64')
    with pytest.raises(KitError, match='unsupported'):
        runtime.prepare()


def test_manifest_fixed_official_sources():
    package = runtime.manifest()
    assert package['agent_browser_version'] == '0.43.0'
    assert package['chrome_version'] == '157.0.8103.78'
    assert set(package['platforms']) == {'windows-x64', 'linux-x64'}
    for platform in package['platforms'].values():
        assert platform['agent_browser']['url'].startswith('https://github.com/vercel-labs/agent-browser/releases/download/v0.43.0/')
        assert platform['chrome']['url'].startswith('https://storage.googleapis.com/chrome-for-testing-public/157.0.8103.78/')
        assert all(len(spec['sha256']) == 64 for spec in platform.values())
