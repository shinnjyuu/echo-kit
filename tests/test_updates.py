import io
import json
import time
from http.client import IncompleteRead

import pytest

from echo_kit import __version__, updates
from echo_kit.core import KitError, Workspace, init_workspace, save
from echo_kit.documentation import tool_info
from echo_kit.operations import data_root


@pytest.fixture
def online(monkeypatch):
    monkeypatch.delenv('ECHO_KIT_OFFLINE', raising=False)
    monkeypatch.delenv('UV_OFFLINE', raising=False)


def test_release_filter_and_python_compatibility(monkeypatch):
    def artifact(**extra):
        return {'packagetype': 'bdist_wheel', 'requires_python': '>=3.11', **extra}
    payload = {'releases': {'0.2.0': [artifact()], '0.2.1': [artifact()], '0.3.0': [artifact()],
                            '0.2.9': [artifact(yanked=True)], '0.4.0rc1': [artifact()],
                            '9.0.0': [artifact(requires_python='>=99')], 'garbage': [artifact()]}}
    monkeypatch.setattr(updates, 'urlopen', lambda *a, **kw: io.BytesIO(json.dumps(payload).encode()))
    releases = updates.fetch_releases()
    assert [str(v) for v in updates.compatible_versions(releases)] == ['0.2.0', '0.2.1', '0.3.0']


def test_cached_checks_force_refresh_and_policy(online, monkeypatch):
    calls = []
    def fetch():
        calls.append(True)
        return {'0.2.0': ['>=3.11'], '0.2.1': ['>=3.11'], '0.3.0': ['>=3.11']}
    monkeypatch.setattr(updates, 'fetch_releases', fetch)
    patch = updates.check('0.2.0', 'patch')
    assert patch['candidate_version'] == '0.2.1' and patch['latest_version'] == '0.3.0'
    assert updates.check('0.2.0', 'manual')['candidate_version'] is None
    assert updates.check('0.2.0', 'latest')['candidate_version'] == '0.3.0'
    assert len(calls) == 1
    updates.check(refresh=True)
    assert len(calls) == 2


def test_network_failure_and_offline_keep_cached_information(online, monkeypatch):
    monkeypatch.setattr(updates, 'fetch_releases', lambda: {'0.2.1': ['>=3.11']})
    first = updates.check('0.2.0', 'patch')
    def unavailable():
        raise OSError('network down')
    monkeypatch.setattr(updates, 'fetch_releases', unavailable)
    result = updates.check('0.2.0', 'patch', refresh=True)
    assert result['status'] == 'passed' and result['source'] == 'unavailable' and result['stale']
    assert result['checked_at'] == first['checked_at']
    assert updates.check('0.2.0', 'patch')['source'] == 'cached'
    monkeypatch.setattr(updates, 'fetch_releases', lambda: pytest.fail('offline made a network request'))
    assert updates.check(no_network=True, refresh=True)['source'] == 'offline'


def test_corrupt_cache_and_empty_offline_check(online, monkeypatch):
    save(data_root() / 'updates/pypi.json', {'releases': {}, 'attempted_at': 'bad'})
    monkeypatch.setattr(updates, 'fetch_releases', lambda: {})
    assert updates.check()['latest_version'] is None
    assert updates.check(no_network=True)['status'] == 'passed'


def test_interrupted_response_is_advisory(online, monkeypatch):
    def broken():
        raise IncompleteRead(b'partial')
    monkeypatch.setattr(updates, 'fetch_releases', broken)
    assert updates.check()['source'] == 'unavailable'
    assert updates.compatible_versions({'0.2.1': 123, '0.2.2': [None]}) == []


def test_candidate_refreshes_uv_metadata_and_replaces_parent_version(tmp_path, monkeypatch):
    monkeypatch.setenv('ECHO_KIT_EXPECTED_VERSION', '0.2.0')
    monkeypatch.delenv('ECHO_KIT_OFFLINE', raising=False)
    monkeypatch.delenv('UV_OFFLINE', raising=False)
    monkeypatch.setattr(updates.shutil, 'which', lambda name: 'uv')
    captured = []
    def run(command, **kwargs):
        captured.append((command, kwargs['env']))
        return type('Result', (), {'returncode': 0, 'stdout': '{}'})()
    monkeypatch.setattr(updates.subprocess, 'run', run)
    updates._json_command('0.2.1', ['--json', 'runtime', 'info'], tmp_path, refresh=True)
    command, env = captured[0]
    assert '--refresh-package' in command
    assert env['ECHO_KIT_EXPECTED_VERSION'] == '0.2.1'


def test_probe_rejects_wrong_runtime_before_selecting(tmp_path, monkeypatch):
    init_workspace(tmp_path)
    ws = Workspace(tmp_path)
    monkeypatch.setattr(updates, '_json_command', lambda *a, **kw: {**tool_info(), 'version': '0.2.0'})
    with pytest.raises(KitError, match='managed-task protocol'):
        updates.probe('0.2.1', ws)
    for version in ('0.1.2', '0.2.1rc1', 'git+https://example.invalid/repo', '--bad'):
        with pytest.raises(KitError):
            updates.command_prefix(version)


def test_candidate_validation_does_not_pass_undefined_default_environment(tmp_path, monkeypatch):
    init_workspace(tmp_path)
    commands = []
    def validate(version, args, *rest):
        commands.append(args)
        return {'status': 'passed'}
    monkeypatch.setattr(updates, '_json_command', validate)
    assert updates.probe(__version__, Workspace(tmp_path))['version'] == __version__
    assert '--environment' not in commands[0]
    assert commands[0][-2:] == ['workspace', 'check']
