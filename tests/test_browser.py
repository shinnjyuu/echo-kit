import json

import pytest

from echo_kit.core import Workspace, KitError, init_workspace, save
from echo_kit import browser


@pytest.fixture
def ws(tmp_path):
    init_workspace(tmp_path)
    return Workspace(tmp_path)


def test_external_never_removed(ws, monkeypatch):
    ws.cfg['browser'] = {'endpoint': 'ws://external:3000/'}
    monkeypatch.setattr(browser, 'docker', lambda *args: pytest.fail('External browser must not call Docker'))
    monkeypatch.setattr(browser, 'connect_check', lambda endpoint: None)
    assert browser.manage(ws, 'up')['mode'] == 'external'
    assert browser.manage(ws, 'down')['mode'] == 'external'


def test_wrong_owner_and_missing_container(ws, monkeypatch):
    save(ws.state / 'browser.json', {'id': 'test-id'})
    monkeypatch.setattr(browser, 'docker', lambda *args: json.dumps([{'Config': {'Labels': {'echo-kit.workspace': 'other'}}}]))
    with pytest.raises(KitError, match='ownership'):
        browser.manage(ws, 'down')


def test_version_rejected_before_launch(ws):
    ws.cfg['browser'] = {'image': 'mcr.microsoft.com/playwright:v0.1.0-noble'}
    with pytest.raises(KitError, match='version'):
        browser.manage(ws, 'up')


def test_active_run_blocks_browser_stop(ws):
    save(ws.runs / 'unfinished/active.json', {'run_id': 'unfinished'})
    with pytest.raises(KitError, match='Unfinished'):
        browser.manage(ws, 'down')

@pytest.fixture
def runtime(ws, monkeypatch):
    monkeypatch.delenv('CI', raising=False)
    from echo_kit.core import read
    calls = []
    monkeypatch.setattr(browser, 'assert_ports_free', lambda cfg: None)
    monkeypatch.setattr(browser, 'prepare_image', lambda cfg: 'echo-kit-browser:test')
    monkeypatch.setattr(browser, 'connect_check', lambda endpoint: None)
    monkeypatch.setattr(browser, 'viewer_check', lambda url: None)
    def docker(*args, **kwargs):
        calls.append(args)
        if args[0] == 'run':
            return 'owned-browser'
        if args[0] == 'inspect':
            return json.dumps([{'Config': {'Labels': {'echo-kit.workspace': ws.identity}},
                                'State': {'Status': 'running', 'Running': True}}])
        return ''
    monkeypatch.setattr(browser, 'docker', docker)
    return calls


def test_headed_loopback_readonly_and_open_only_once(ws, runtime, monkeypatch):
    opened = []
    monkeypatch.setattr(browser.webbrowser, 'open', lambda url, **kw: opened.append(url) or True)
    first = browser.manage(ws, 'up')
    second = browser.manage(ws, 'up')
    assert first['headless'] is False and first['viewer_read_only']
    assert opened == [first['viewer_url']]
    assert second['viewer_open_attempted']
    launch = next(c for c in runtime if c[0] == 'run')
    assert '127.0.0.1:19323:3000' in launch and '127.0.0.1:19324:6080' in launch
    assert browser.manage(ws, 'status')['viewer_url'] == first['viewer_url']
    browser.manage(ws, 'down')
    assert ('rm', '-f', 'owned-browser') in runtime


def test_headless_has_no_viewer_or_open(ws, runtime, monkeypatch):
    ws.cfg['browser'] = {'headless': True}
    monkeypatch.setattr(browser.webbrowser, 'open', lambda *a, **kw: pytest.fail('headless opened viewer'))
    monkeypatch.setattr(browser, 'viewer_check', lambda *a: pytest.fail('headless checked viewer'))
    result = browser.manage(ws, 'up')
    assert result['headless'] and result['viewer_url'] is None
    launch = next(c for c in runtime if c[0] == 'run')
    assert launch.count('-p') == 1
    assert 'ECHO_BROWSER_HEADLESS=true' in launch


def test_auto_open_failure_is_nonfatal_and_not_repeated(ws, runtime, monkeypatch):
    calls = []
    monkeypatch.setattr(browser.webbrowser, 'open', lambda *a, **kw: calls.append(True) or False)
    result = browser.manage(ws, 'up')
    assert result['status'] == 'ready' and result['viewer_notice']
    browser.manage(ws, 'up')
    assert calls == [True]


def test_ci_and_explicit_auto_open(ws, runtime, monkeypatch):
    monkeypatch.setenv('CI', 'true')
    monkeypatch.setattr(browser.webbrowser, 'open', lambda *a, **kw: pytest.fail('CI opened viewer'))
    assert not browser.manage(ws, 'up').get('viewer_open_attempted')
    ws.cfg['browser'] = {'auto_open': True}
    monkeypatch.setattr(browser.webbrowser, 'open', lambda *a, **kw: True)
    assert browser.manage(ws, 'up')['viewer_opened']


def test_legacy_and_changed_mode_require_explicit_rebuild(ws, runtime):
    save(ws.state / 'browser.json', {'id': 'old', 'endpoint': 'ws://127.0.0.1:19323/', 'image': browser.IMAGE})
    result = browser.manage(ws, 'status')
    assert result['legacy'] and result['viewer_url'] is None and result['migration_required']
    with pytest.raises(KitError, match='browser down'):
        browser.manage(ws, 'up')
    assert not any(c[0] in ('run', 'start', 'rm') for c in runtime)
    browser.manage(ws, 'down')
    ws.cfg['browser'] = {'headless': True}
    browser.manage(ws, 'up')
    ws.cfg['browser'] = {'headless': False, 'auto_open': False}
    with pytest.raises(KitError, match='configuration/runtime'):
        browser.manage(ws, 'up')
    assert browser.manage(ws, 'status')['headless'] is True


@pytest.mark.parametrize('config', [{'headless': 'false'}, {'port': 0}, {'viewer_port': True},
                                    {'port': 19324}, {'auto_open': 'yes'}, {'timeout': float('inf')}])
def test_bad_config_has_no_docker_side_effect(ws, monkeypatch, config):
    ws.cfg['browser'] = config
    monkeypatch.setattr(browser, 'docker', lambda *a, **kw: pytest.fail('invalid config called Docker'))
    with pytest.raises(KitError):
        browser.manage(ws, 'up')


def test_viewer_port_occupied_before_build(ws, monkeypatch):
    import socket
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        sock.listen()
        ws.cfg['browser'] = {'viewer_port': sock.getsockname()[1]}
        monkeypatch.setattr(browser, 'prepare_image', lambda *a: pytest.fail('must check ports before build'))
        with pytest.raises(KitError, match='port occupied'):
            browser.manage(ws, 'up')


def test_viewer_resource_blocks_other_workspace_and_down(ws, runtime):
    from echo_kit.operations import Journal, browser_resources, ResourceBusy
    journal = Journal()
    oid = journal.acquire(ws, 'verify', browser_resources(ws))
    try:
        with pytest.raises(ResourceBusy):
            browser.manage(ws, 'down')
        assert 'endpoint:loopback:19324' in journal.show(oid)['held']
    finally:
        journal.release(oid)


def test_external_viewer_is_informational(ws, monkeypatch):
    ws.cfg['browser'] = {'endpoint': 'ws://external:3000', 'viewer_url': 'https://example.test/view'}
    monkeypatch.setattr(browser, 'connect_check', lambda *a: None)
    monkeypatch.setattr(browser, 'docker', lambda *a, **kw: pytest.fail('external uses Docker'))
    monkeypatch.setattr(browser.webbrowser, 'open', lambda *a, **kw: pytest.fail('external auto-open'))
    result = browser.manage(ws, 'up')
    assert result['viewer_url'] == ws.cfg['browser']['viewer_url']
    assert result['viewer_read_only'] is None


def test_image_key_changes_with_base_or_resources(monkeypatch):
    original = browser.image_key(browser.IMAGE)
    assert browser.image_key('mirror/playwright:v1.63.0-noble') != original
    monkeypatch.setattr(browser, 'build_resources', lambda: {'Dockerfile': b'changed', 'server.cjs': b'changed'})
    assert browser.image_key(browser.IMAGE) != original


def test_viewer_failure_retains_container_without_auto_open(ws, runtime, monkeypatch):
    ws.cfg['browser'] = {'timeout': .01}
    def fail(url):
        raise KitError('viewer transport unavailable')
    monkeypatch.setattr(browser, 'viewer_check', fail)
    monkeypatch.setattr(browser.webbrowser, 'open', lambda *a, **kw: pytest.fail('opened unready viewer'))
    with pytest.raises(KitError, match='viewer transport') as caught:
        browser.manage(ws, 'up')
    assert (ws.state / 'browser.json').exists()
    assert caught.value.details['logs_command'] == 'docker logs owned-browser'
    assert not any(c[0] == 'rm' for c in runtime)


def test_cached_image_needs_no_build(ws, monkeypatch):
    calls = []
    monkeypatch.setattr(browser, 'docker', lambda *a, **kw: calls.append(a) or '{}')
    tag = browser.prepare_image(browser.settings(ws))
    assert tag.startswith('echo-kit-browser:')
    assert calls == [('image', 'inspect', tag)]


def test_image_build_failure_before_container_creation(ws, monkeypatch):
    calls = []
    def fail(*args, **kwargs):
        calls.append(args[0])
        raise KitError('build unavailable')
    monkeypatch.setattr(browser, 'docker', fail)
    monkeypatch.setattr(browser, 'assert_ports_free', lambda cfg: None)
    with pytest.raises(KitError, match='image build failed') as caught:
        browser.manage(ws, 'up')
    assert calls == ['image', 'build']
    assert 'build_log' in caught.value.details
    assert not (ws.state / 'browser.json').exists()


def test_verify_persists_browser_metadata(ws, monkeypatch):
    import sys
    from echo_kit import runner
    from echo_kit.core import read
    metadata = {'endpoint': 'ws://127.0.0.1:19323/', 'headless': False,
                'viewer_url': 'http://127.0.0.1:19324/vnc.html', 'viewer_read_only': True}
    monkeypatch.setattr(runner, 'manage', lambda *args: metadata)
    ws.cfg['cases'] = {'web': {'browser': True, 'command': [sys.executable, '-c', 'pass'], 'protocol': 'command'}}
    record, = runner.run(ws, 'web', 'verify')
    assert record['status'] == 'passed' and record['browser'] == metadata
    assert read(ws.runs / record['id'] / 'browser.json') == metadata
    assert read(ws.runs / record['id'] / 'adapter/request.json')['browser'] == metadata


@pytest.mark.parametrize('fails', [False, True])
@pytest.mark.parametrize('action', ['up', 'status'])
def test_external_capability_context(ws, monkeypatch, fails, action):
    from echo_kit import __version__
    ws.cfg['browser'] = {'endpoint': 'ws://external:3000/', 'viewer_url': 'http://external/view'}
    monkeypatch.setattr(browser, 'docker', lambda *a: pytest.fail('external called Docker'))
    def connect(endpoint):
        if fails:
            raise RuntimeError('not reachable')
    monkeypatch.setattr(browser, 'connect_check', connect)
    if fails:
        with pytest.raises(KitError) as caught:
            browser.manage(ws, action)
        result = caught.value.details
        assert result['status'] == 'blocked'
    else:
        result = browser.manage(ws, action)
    assert result['mode'] == 'external' and result['tool_version'] == __version__
    assert result['endpoint'] == ws.cfg['browser']['endpoint']
    assert result['viewer_url'] == ws.cfg['browser']['viewer_url']
    assert not any(result['capabilities'].values())
    assert result['next_steps'] and result['viewer_read_only'] is None
