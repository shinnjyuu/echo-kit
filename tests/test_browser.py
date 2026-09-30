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
