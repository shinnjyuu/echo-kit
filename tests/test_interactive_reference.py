import os
import re
from contextlib import contextmanager
from importlib.resources import files

import pytest

from echo_kit import browser
from echo_kit.core import Workspace, init_workspace
from echo_kit.operations import Journal, ResourceBusy, browser_resources, operation


def reference():
    text = files('echo_kit').joinpath('skills/references/interactive-session.md').read_text()
    scope = {}
    exec(re.search(r'```python\n(.*?)```', text, re.S)[1], scope)
    return scope['interactive_session']


@pytest.mark.parametrize('interrupted', [False, True])
def test_reference_holds_reservation_and_closes_context(tmp_path, monkeypatch, interrupted):
    init_workspace(tmp_path)
    ws = Workspace(tmp_path)
    events = []
    monkeypatch.setattr(browser, 'manage', lambda *a: {'status': 'running', 'endpoint': 'ws://example'})

    @contextmanager
    def session(endpoint, output, state):
        events.append('open')
        try:
            yield object()
        finally:
            events.append('close')

    monkeypatch.setattr(browser, 'session', session)
    try:
        with reference()(tmp_path) as (context, oid, output):
            assert Journal().show(oid)['owner_alive']
            assert output.is_relative_to(ws.state)
            with pytest.raises(ResourceBusy):
                Journal().acquire(ws, 'browser.down', {k: 'exclusive' for k in browser_resources(ws)})
            if interrupted:
                raise KeyboardInterrupt()
    except KeyboardInterrupt:
        assert interrupted
    assert events == ['open', 'close']
    record = Journal().show(oid)
    assert bool(record['held']) == interrupted
    assert record['state'] == ('needs_cleanup' if interrupted else 'finished')
    assert not list(ws.runs.glob('*/run.json'))


@pytest.mark.skipif(os.environ.get('ECHO_KIT_DOCKER_TESTS') != '1', reason='Disposable real Docker only')
def test_reference_real_browser_continuity_and_other_context(tmp_path):
    from playwright.sync_api import sync_playwright
    from test_core import free_port
    init_workspace(tmp_path)
    port, viewer = free_port(), free_port()
    while viewer == port:
        viewer = free_port()
    with (tmp_path / 'echo-kit.toml').open('a') as f:
        f.write(f'\n[browser]\nport={port}\nviewer_port={viewer}\nauto_open=false\n')
    ws = Workspace(tmp_path)
    try:
        info = browser.manage(ws, 'up')
        with sync_playwright() as p:
            connection = p.chromium.connect(info['endpoint'])
            other = connection.new_context()
            other_page = other.new_page()
            other_page.set_content('<p>other session</p>')
            # session() owns its Playwright loop; run it on another thread.
            from concurrent.futures import ThreadPoolExecutor
            def interact():
                with reference()(tmp_path) as (context, oid, output):
                    page = context.new_page()
                    page.set_content('<input><button onclick="this.textContent=document.querySelector(\'input\').value">copy</button>')
                    page.locator('input').fill('same context')
                    page.locator('button').click()
                    assert page.locator('button').inner_text() == 'same context'
                    with pytest.raises(ResourceBusy):
                        Journal().acquire(ws, 'browser.down', {k: 'exclusive' for k in browser_resources(ws)})
                assert (output / 'trace.zip').stat().st_size > 0
                assert not Journal().show(oid)['held']
            with ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(interact).result(timeout=45)
            assert other_page.locator('p').inner_text() == 'other session'
            other.close()
            connection.close()
        assert browser.manage(ws, 'status')['status'] == 'running'
    finally:
        if (ws.state / 'browser.json').exists():
            browser.manage(ws, 'down')
