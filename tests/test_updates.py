import io
import json
import os
import subprocess
import sys
from http.client import IncompleteRead
from types import SimpleNamespace

import pytest

from echo_kit import __version__, updates
from echo_kit.cli import main
from echo_kit.core import KitError, save
from echo_kit.operations import data_root


@pytest.fixture
def online(monkeypatch):
    monkeypatch.delenv('ECHO_KIT_OFFLINE', raising=False)
    monkeypatch.delenv('UV_OFFLINE', raising=False)
    monkeypatch.delenv(updates.FORWARDED, raising=False)


def test_release_filter_and_python_compatibility(monkeypatch):
    def artifact(**extra):
        return {'packagetype': 'bdist_wheel', 'requires_python': '>=3.11', **extra}
    payload = {'releases': {'0.4.0': [artifact()], '0.4.1': [artifact()], '0.5.0': [artifact()],
                            '0.4.9': [artifact(yanked=True)], '0.6.0rc1': [artifact()],
                            '9.0.0': [artifact(requires_python='>=99')], 'garbage': [artifact()]}}
    monkeypatch.setattr(updates, 'urlopen', lambda *a, **kw: io.BytesIO(json.dumps(payload).encode()))
    assert [str(v) for v in updates.compatible_versions(updates.fetch_releases())] == ['0.4.0', '0.4.1', '0.5.0']


def test_every_successful_check_refreshes_across_minor_versions(online, monkeypatch):
    calls = []
    def fetch():
        calls.append(True)
        return {'0.4.0': ['>=3.11'], '0.5.0': ['>=3.11']}
    monkeypatch.setattr(updates, 'fetch_releases', fetch)
    first = updates.check()
    assert first['latest_version'] == '0.5.0' and first['update_available']
    assert not {'policy', 'candidate_version', 'project_version'} & first.keys()
    updates.check()
    assert len(calls) == 2


def test_network_failure_is_throttled_offline_uses_cache(online, monkeypatch):
    monkeypatch.setattr(updates, 'fetch_releases', lambda: {'0.5.0': ['>=3.11']})
    first = updates.check()
    calls = []
    def unavailable():
        calls.append(True)
        raise OSError('network down')
    monkeypatch.setattr(updates, 'fetch_releases', unavailable)
    result = updates.check()
    assert result['status'] == 'passed' and result['source'] == 'unavailable' and result['stale']
    assert result['checked_at'] == first['checked_at']
    assert updates.check()['source'] == 'cached'
    assert len(calls) == 1
    updates.check(refresh=True)
    assert len(calls) == 2
    monkeypatch.setattr(updates, 'fetch_releases', lambda: pytest.fail('offline made a network request'))
    assert updates.check(no_network=True, refresh=True)['source'] == 'offline'


def test_corrupt_cache_and_interrupted_response(online, monkeypatch):
    save(data_root() / 'updates/pypi.json', {'releases': {}, 'attempted_at': 'bad'})
    def broken():
        raise IncompleteRead(b'partial')
    monkeypatch.setattr(updates, 'fetch_releases', broken)
    assert updates.check()['source'] == 'unavailable'
    assert updates.check(no_network=True)['latest_version'] is None
    assert updates.compatible_versions({'0.4.1': 123, '0.4.2': [None]}) == []


def test_probe_only_checks_startable_identity(online, monkeypatch):
    monkeypatch.setenv('ECHO_KIT_EXPECTED_VERSION', '0.4.0')
    monkeypatch.setattr(updates.shutil, 'which', lambda name: 'uv')
    captured = []
    def run(command, **kwargs):
        captured.append((command, kwargs['env']))
        return SimpleNamespace(returncode=0, stdout=json.dumps({'package': updates.PACKAGE, 'version': '0.5.0'}))
    monkeypatch.setattr(updates.subprocess, 'run', run)
    assert updates.probe('0.5.0')['version'] == '0.5.0'
    command, env = captured[0]
    assert '--refresh-package' in command and command[-3:] == ['--json', 'runtime', 'info']
    assert env['ECHO_KIT_EXPECTED_VERSION'] == env[updates.FORWARDED] == '0.5.0'
    assert len(captured) == 1  # No workspace or documentation compatibility probe.
    monkeypatch.setattr(updates.subprocess, 'run', lambda *a, **kw: SimpleNamespace(returncode=0, stdout='{}'))
    with pytest.raises(KitError):
        updates.probe('0.5.0')
    for version in ('0.3.0', '0.5.0rc1', 'git+https://example.invalid/repo', '--bad'):
        with pytest.raises(KitError):
            updates.command_prefix(version)


def test_new_command_routes_before_parser_and_never_retries_business_failure(online, monkeypatch, tmp_path, capfd):
    monkeypatch.setattr(updates, 'fetch_releases', lambda: {'0.5.0': ['>=3.11']})
    prepared = []
    monkeypatch.setattr(updates, 'probe', lambda v: prepared.append(v))
    helper = tmp_path / 'future.py'
    helper.write_text('import json,sys; print(json.dumps({"args":sys.argv[1:]})); sys.exit(7)', encoding='utf-8')
    monkeypatch.setattr(updates, 'command_prefix', lambda *a, **kw: [sys.executable, str(helper)])
    args = ['--json', 'future-command', '--new-option', 'value']
    assert main(args) == 7
    assert json.loads(capfd.readouterr().out)['args'] == args
    assert prepared == ['0.5.0']


def test_preparation_failure_falls_back_before_business(online, monkeypatch, capsys):
    monkeypatch.setattr(updates, 'fetch_releases', lambda: {'0.5.0': ['>=3.11']})
    def fail(*a):
        raise KitError('unavailable')
    monkeypatch.setattr(updates, 'probe', fail)
    assert main(['--json', 'skills', 'list']) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)['version'] == __version__
    assert 'could not prepare' in captured.err


def test_forwarded_child_does_not_check_again_or_propagate_guard(online, monkeypatch):
    monkeypatch.setenv(updates.FORWARDED, __version__)
    monkeypatch.setattr(updates, 'fetch_releases', lambda: pytest.fail('recursive update'))
    code, info = updates.startup(['docs', 'list'])
    assert code is None and info['source'] == 'forwarded'
    assert updates.FORWARDED not in os.environ
    monkeypatch.setenv(updates.FORWARDED, '9.9.9')
    with pytest.raises(KitError):
        updates.startup([])


@pytest.mark.parametrize('args', [['--version'], ['--help'], ['runtime', 'info'], ['updates', 'check']])
def test_diagnostics_do_not_forward(online, monkeypatch, args, capsys):
    monkeypatch.setattr(updates, 'startup', lambda *a: pytest.fail('diagnostic forwarded'))
    monkeypatch.setattr(updates, 'fetch_releases', lambda: {})
    try:
        assert main(args) == 0
    except SystemExit as e:
        assert e.code == 0


@pytest.mark.parametrize('args', [['task', 'start'], ['--task', 'old', 'docs', 'list'],
                                  ['updates', 'apply', '--version', '0.3.0'], ['updates', 'setup', '--policy', 'latest']])
def test_removed_interfaces_give_migration_help(monkeypatch, capsys, args):
    monkeypatch.setattr(updates, 'startup', lambda *a: pytest.fail('legacy command forwarded'))
    assert main(['--json'] + args) == 2
    assert 'updates setup' in json.loads(capsys.readouterr().out)['error']


def test_offline_fallback_is_explicit(monkeypatch, capsys):
    monkeypatch.setattr(updates, 'fetch_releases', lambda: pytest.fail('offline fetched'))
    assert main(['--offline', '--json', 'skills', 'list']) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)['version'] == __version__
    assert 'latest release not confirmed' in captured.err
