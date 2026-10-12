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
from packaging.version import Version

NEXT_VERSION = f'{Version(__version__).major}.{Version(__version__).minor + 1}.0'


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
        return {'0.4.0': ['>=3.11'], NEXT_VERSION: ['>=3.11']}
    monkeypatch.setattr(updates, 'fetch_releases', fetch)
    first = updates.check()
    assert first['latest_version'] == NEXT_VERSION and first['update_available']
    assert not {'policy', 'candidate_version', 'project_version'} & first.keys()
    updates.check()
    assert len(calls) == 2


def test_network_failure_is_throttled_offline_uses_cache(online, monkeypatch):
    monkeypatch.setattr(updates, 'fetch_releases', lambda: {NEXT_VERSION: ['>=3.11']})
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
        return SimpleNamespace(returncode=0, stdout=json.dumps({'package': updates.PACKAGE, 'version': NEXT_VERSION}))
    monkeypatch.setattr(updates.subprocess, 'run', run)
    assert updates.probe(NEXT_VERSION)['version'] == NEXT_VERSION
    command, env = captured[0]
    assert '--refresh-package' in command and command[-3:] == ['--json', 'runtime', 'info']
    assert env['ECHO_KIT_EXPECTED_VERSION'] == env[updates.FORWARDED] == NEXT_VERSION
    assert len(captured) == 1  # No workspace or documentation compatibility probe.
    monkeypatch.setattr(updates.subprocess, 'run', lambda *a, **kw: SimpleNamespace(returncode=0, stdout='{}'))
    with pytest.raises(KitError):
        updates.probe(NEXT_VERSION)
    for version in ('0.3.0', '0.5.0rc1', 'git+https://example.invalid/repo', '--bad'):
        with pytest.raises(KitError):
            updates.command_prefix(version)


def test_new_command_routes_before_parser_and_never_retries_business_failure(online, monkeypatch, tmp_path, capfd):
    monkeypatch.setattr(updates, 'fetch_releases', lambda: {NEXT_VERSION: ['>=3.11']})
    prepared = []
    monkeypatch.setattr(updates, 'probe', lambda v: prepared.append(v))
    helper = tmp_path / 'future.py'
    helper.write_text('import json,sys; print(json.dumps({"args":sys.argv[1:]})); sys.exit(7)', encoding='utf-8')
    monkeypatch.setattr(updates, 'command_prefix', lambda *a, **kw: [sys.executable, str(helper)])
    args = ['--json', 'future-command', '--new-option', 'value']
    assert main(args) == 7
    assert json.loads(capfd.readouterr().out)['args'] == args
    assert prepared == [NEXT_VERSION]


def test_preparation_failure_falls_back_before_business(online, monkeypatch, capsys):
    monkeypatch.setattr(updates, 'fetch_releases', lambda: {NEXT_VERSION: ['>=3.11']})
    def fail(*a, **kw):
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


@pytest.mark.parametrize('source', [None, '0.6.0'])
def test_forwarded_notice_and_source_consumed(online, monkeypatch, capsys, source):
    import echo_kit.cli as cli
    monkeypatch.setenv(updates.FORWARDED, __version__)
    monkeypatch.setenv('ECHO_KIT_EXPECTED_VERSION', __version__)
    if source:
        monkeypatch.setenv('ECHO_KIT_SOURCE_VERSION', source)
    else:
        monkeypatch.delenv('ECHO_KIT_SOURCE_VERSION', raising=False)
    calls = []
    def business(args):
        assert not any(k in os.environ for k in ('ECHO_KIT_SOURCE_VERSION', updates.FORWARDED, 'ECHO_KIT_EXPECTED_VERSION'))
        calls.append(args._update)
        return {'status': 'failed'}
    monkeypatch.setattr(cli, 'dispatch', business)
    assert main(['--json', 'guide', 'echo-init']) == 1
    output = capsys.readouterr()
    assert json.loads(output.out)['status'] == 'failed'
    assert (source or 'unknown') in output.err and 'updates notes' in output.err
    assert len(calls) == 1 and calls[0]['source_version'] == source


def test_parent_passes_source_once(online, monkeypatch):
    monkeypatch.setattr(updates, 'check', lambda **kw: {'stale': False, 'update_available': True, 'latest_version': NEXT_VERSION})
    monkeypatch.setattr(updates, 'probe', lambda v: None)
    monkeypatch.setattr(updates, 'command_prefix', lambda *a, **kw: ['future'])
    calls = []
    def execute(command, env):
        calls.append(command)
        assert env['ECHO_KIT_SOURCE_VERSION'] == __version__
        return 7
    monkeypatch.setattr(updates.subprocess, 'call', execute)
    assert updates.startup(['guide', 'echo-init'])[0] == 7
    assert calls == [['future', 'guide', 'echo-init']]


def test_busy_cache_routes_offline(online, monkeypatch):
    from filelock import FileLock
    root = data_root() / 'updates'
    save(root / 'pypi.json', {'releases': {NEXT_VERSION: ['>=3.11']}})
    monkeypatch.setattr(updates, 'CHECK_WAIT_SECONDS', 0.05)
    probes, children = [], []
    monkeypatch.setattr(updates, 'probe', lambda v, **kw: probes.append((v, kw)))
    monkeypatch.setattr(updates, 'command_prefix', lambda *a, **kw: ['prepared'])
    monkeypatch.setattr(updates.subprocess, 'call', lambda cmd, **kw: children.append(cmd) or 7)
    with FileLock(str(root / 'check.lock')):
        code, info = updates.startup(['docs', 'map'])
    assert code == 7 and info['source'] == 'busy'
    assert probes == [(NEXT_VERSION, {'no_network': True})]
    assert children == [['prepared', 'docs', 'map']]


@pytest.mark.parametrize('failed', [False, True])
def test_waiter_reloads_completed_check(online, monkeypatch, failed):
    import threading
    from filelock import FileLock
    root = data_root() / 'updates'
    root.mkdir(parents=True)
    entered, release = threading.Event(), threading.Event()
    def fetch():
        entered.set()
        assert release.wait(5)
        if failed:
            raise OSError('offline')
        return {NEXT_VERSION: ['>=3.11']}
    monkeypatch.setattr(updates, 'fetch_releases', fetch)
    results = []
    first = threading.Thread(target=lambda: results.append(updates.check()))
    first.start()
    assert entered.wait(5)
    # Signal only when the second caller really attempts the held file lock.
    attempting = threading.Event()
    class ObservedLock(FileLock):
        def acquire(self, *args, **kwargs):
            attempting.set()
            return super().acquire(*args, **kwargs)
    monkeypatch.setattr(updates, 'FileLock', ObservedLock)
    second = threading.Thread(target=lambda: results.append(updates.check()))
    second.start()
    assert attempting.wait(5)
    release.set()
    first.join(5); second.join(5)
    assert not first.is_alive() and not second.is_alive()
    assert len(results) == 2
    assert any(r['source'] == 'coalesced' for r in results)
    assert all(r['stale'] == failed for r in results)
    if not failed:
        assert all(r['latest_version'] == NEXT_VERSION for r in results)


def test_prepared_runtime_survives_missing_metadata(online, monkeypatch):
    updates.remember_prepared(NEXT_VERSION)
    monkeypatch.setattr(updates, 'fetch_releases', lambda: (_ for _ in ()).throw(OSError()))
    probes = []
    monkeypatch.setattr(updates, 'probe', lambda v, **kw: probes.append((v, kw)))
    monkeypatch.setattr(updates, 'command_prefix', lambda *a, **kw: ['cached'])
    monkeypatch.setattr(updates.subprocess, 'call', lambda *a, **kw: 0)
    assert updates.startup(['skills', 'list'])[0] == 0
    assert probes == [(NEXT_VERSION, {'no_network': True})]
    probes.clear()
    assert updates.startup(['skills', 'list'], no_network=True)[0] is None
    assert probes == []


def test_prepare_failure_uses_previous_without_business_retry(online, monkeypatch):
    newest = '99.0.0'
    updates.remember_prepared(NEXT_VERSION)
    monkeypatch.setattr(updates, 'fetch_releases', lambda: {newest: ['>=3.11']})
    probes, calls = [], []
    def probe(v, **kw):
        probes.append((v, kw))
        if v == newest:
            raise KitError('installation failed')
    monkeypatch.setattr(updates, 'probe', probe)
    monkeypatch.setattr(updates, 'command_prefix', lambda *a, **kw: ['cached'])
    monkeypatch.setattr(updates.subprocess, 'call', lambda cmd, **kw: calls.append(cmd) or 9)
    code, info = updates.startup(['lab', 'run', 'example'])
    assert code == 9 and len(calls) == 1
    assert probes == [(newest, {}), (newest, {'no_network': True}), (NEXT_VERSION, {'no_network': True})]
    assert info['running_version'] == NEXT_VERSION


def test_subcommand_help_routes(online, monkeypatch):
    seen = []
    monkeypatch.setattr(updates, 'startup', lambda raw, offline: (seen.append(raw) or 0, {}))
    assert main(['docs', 'map', '--help']) == 0
    assert seen == [['docs', 'map', '--help']]


def test_failed_update_parser_hint(online, monkeypatch, capsys):
    monkeypatch.setattr(updates, 'fetch_releases', lambda: (_ for _ in ()).throw(OSError()))
    with pytest.raises(SystemExit) as error:
        main(['unknown-future-command'])
    assert error.value.code == 2
    output = capsys.readouterr()
    assert 'invalid choice' in output.err and 'update routing incomplete' in output.err
    assert 'uv tool install' in output.err


def test_prepared_cache_validation_and_monotonicity(monkeypatch):
    save(updates.prepared_path(), {'version': 'evil/path'})
    assert updates.prepared_version() is None
    updates.remember_prepared(NEXT_VERSION)
    updates.remember_prepared(__version__)
    assert updates.prepared_version() == NEXT_VERSION
    old = updates.prepared_path()
    monkeypatch.setattr(updates.sys, 'executable', 'another-python')
    assert updates.prepared_path() != old


def test_failed_refresh_can_use_same_cached_target(online, monkeypatch):
    monkeypatch.setattr(updates, 'fetch_releases', lambda: {NEXT_VERSION: ['>=3.11']})
    def probe(version, no_network=False):
        if not no_network:
            raise KitError('index unavailable')
    monkeypatch.setattr(updates, 'probe', probe)
    monkeypatch.setattr(updates, 'command_prefix', lambda *a, **kw: ['cached'])
    monkeypatch.setattr(updates.subprocess, 'call', lambda *a, **kw: 0)
    assert updates.startup(['skills', 'list'])[0] == 0


def test_forwarded_route_metadata_is_consumed(online, monkeypatch):
    monkeypatch.setenv(updates.FORWARDED, __version__)
    monkeypatch.setenv('ECHO_KIT_UPDATE_ROUTE', json.dumps({'check_source': 'busy', 'stale': True,
                                                        'latest_confirmed': False}))
    _, info = updates.startup([])
    assert info['check_source'] == 'busy' and info['stale']
    assert 'ECHO_KIT_UPDATE_ROUTE' not in os.environ
    assert 'ECHO_KIT_UPDATE_ROUTE' not in updates.child_environment()


def test_lock_owner_exit_releases_lock(online, monkeypatch, tmp_path):
    root = data_root() / 'updates'
    root.mkdir(parents=True)
    marker = tmp_path / 'held'
    script = ('from filelock import FileLock; from pathlib import Path; import sys; '
              'lock=FileLock(sys.argv[1]); lock.acquire(); Path(sys.argv[2]).touch(); '
              'sys.stdin.read(1)')
    child = subprocess.Popen([sys.executable, '-c', script, str(root / 'check.lock'), str(marker)],
                             stdin=subprocess.PIPE)
    import time
    try:
        deadline = time.monotonic() + 5
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(.01)
        assert marker.exists()
        child.kill(); child.wait(timeout=5)
        monkeypatch.setattr(updates, 'fetch_releases', lambda: {NEXT_VERSION: ['>=3.11']})
        assert updates.check()['latest_version'] == NEXT_VERSION
    finally:
        if child.poll() is None:
            child.kill(); child.wait(timeout=5)


def test_expired_prepared_and_no_uv_fall_back(online, monkeypatch):
    updates.remember_prepared(NEXT_VERSION)
    monkeypatch.setattr(updates, 'fetch_releases', lambda: (_ for _ in ()).throw(OSError()))
    monkeypatch.setattr(updates.shutil, 'which', lambda name: None)
    code, info = updates.startup(['skills', 'list'])
    assert code is None and info['preparation_failed']
    assert info['running_version'] == __version__


def test_confirmed_current_release_does_not_select_old_prepared_hint(online, monkeypatch):
    updates.remember_prepared(NEXT_VERSION)
    monkeypatch.setattr(updates, 'fetch_releases', lambda: {__version__: ['>=3.11']})
    monkeypatch.setattr(updates, 'probe', lambda *a, **kw: pytest.fail('ignored fresh metadata'))
    assert updates.startup(['skills', 'list'])[0] is None
