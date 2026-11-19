"""Page readiness and blank evidence through the existing browser interfaces."""

import json
import os
import queue
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from echo_kit import native_runtime
from echo_kit.core import init_workspace, read
from echo_kit.page_readiness import PageReadiness, page_check


@pytest.mark.parametrize(('match_count', 'visible', 'reason'), [
    (0, None, 'target_missing'), (1, False, 'target_hidden'), (2, None, 'target_ambiguous'),
])
def test_page_check_reports_why_the_target_is_not_ready(match_count, visible, reason):
    def sample(_, __):
        return {'matches': match_count, 'visible': visible, 'document_status': 200,
                'url': 'https://user:private@fixture.invalid/toolbox?token=private#private'}

    result = page_check(sample, '#toolbox', time.monotonic() + .02)
    assert result['status'] == 'blocked'
    assert result['reason'] == reason and result['matches'] == match_count
    assert result['document_status'] == 200 and result['page_errors'] is None
    assert result['url'] == 'https://fixture.invalid/toolbox'


@pytest.fixture
def rendered_fixture():
    requests = []
    state = {'bundle_available': False}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            path = urlsplit(self.path).path
            requests.append(path)
            if path == '/redirect':
                self.send_response(302)
                self.send_header('Location', '/login?token=fixture-login-secret#private')
                self.end_headers()
                return
            if path == '/bundle.js':
                source = b'document.body.insertAdjacentHTML("beforeend", "<h1 id=toolbox>Toolbox</h1>");'
                code = 200 if state['bundle_available'] else 404
                content_type, body = 'text/javascript', source if code == 200 else b''
            else:
                code = 200
                content_type = 'text/html; charset=utf-8'
                markup = {
                    '/toolbox': '<script>console.error("fixture-console-secret");'
                                'throw new Error("fixture-runtime-secret");</script>'
                                '<script src="/bundle.js?token=fixture-script-secret" defer></script>',
                    '/delayed': '<script>setTimeout(() => document.body.insertAdjacentHTML('
                                '"beforeend", "<h1 id=toolbox>Toolbox</h1>"), 250)</script>',
                    '/login': '<h1>Login</h1>',
                    '/uniform': '<div id="toolbox" style="width:24px;height:24px"></div>',
                }
                body = ('<!doctype html><link rel="icon" href="data:,">'
                        '<style>body{background:#f6f4f1}</style>' + markup.get(path, '')).encode()
            self.send_response(code)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(body)

    with ThreadingHTTPServer(('127.0.0.1', 0), Handler) as server:
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            yield f'http://127.0.0.1:{server.server_port}', state, requests
        finally:
            server.shutdown()


@pytest.fixture
def prepared_runtime(monkeypatch):
    directory = os.environ.get('ECHO_KIT_NATIVE_READINESS_TEST_DATA_HOME')
    if not directory:
        pytest.skip('Requires an explicit isolated data home with the verified native runtime')
    monkeypatch.setenv('ECHO_KIT_DATA_HOME', directory)
    return native_runtime.prepare(offline=True)


def test_real_native_readiness_and_recovery(rendered_fixture, prepared_runtime, tmp_path):
    base, state, requests = rendered_fixture
    init_workspace(tmp_path)
    subprocess.run(['git', '-C', str(tmp_path), 'init', '-q'], check=True)
    with (tmp_path / 'echo-kit.toml').open('a', encoding='utf-8') as config:
        config.write('\n[browser]\nbackend = "agent-browser"\nheadless = true\n')
    prefix = [sys.executable, '-I', '-m', 'echo_kit', '--offline', '--json', '--workspace', str(tmp_path),
              '--actor', 'readiness-test', 'browser']

    def run(*args, code=0):
        result = subprocess.run(prefix + list(args), capture_output=True, text=True, encoding='utf-8', timeout=75)
        assert result.returncode == code, (result.stdout, result.stderr)
        return json.loads(result.stdout)

    observer = subprocess.Popen(prefix + ['observe', '--url', base + '/toolbox?token=fixture-page-secret',
                                          '--timeout-ms', '3500'],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, encoding='utf-8')
    replies = queue.Queue()

    def collect_replies():
        for line in observer.stdout:
            replies.put(line)

    threading.Thread(target=collect_replies, daemon=True).start()

    def observe(id, **options):
        observer.stdin.write(json.dumps({'op': 'observe', 'id': id, **options}) + '\n')
        observer.stdin.flush()
        reply = json.loads(replies.get(timeout=30))
        assert reply['id'] == id
        return reply

    try:
        started = json.loads(replies.get(timeout=120))
        assert started['event'] == 'ready', started
        blocked = run('exec', '--', 'screenshot', '--ready-selector', '#toolbox', code=2)
        assert blocked['status'] == 'blocked' and blocked['images'] == []
        assert blocked['page_check']['reason'] == 'target_missing'
        assert blocked['page_check']['document_status'] == 200
        assert 404 in blocked['page_check']['script_http_statuses']
        assert blocked['page_check']['page_errors'] >= 1
        assert blocked['page_check']['console_errors'] >= 1
        diagnostic = Path(blocked['diagnostic_images'][0])
        assert diagnostic.is_file() and diagnostic.name.startswith(blocked['observation_id'])
        assert blocked['page_check']['url'] == base + '/toolbox'
        assert '-secret' not in json.dumps(blocked)
        assert '-secret' not in json.dumps(read(Path(started['output_dir']) / 'latest.json'))
        assert requests.count('/toolbox') == 1

        blocked_observation = observe('before-observe', target='#toolbox')
        assert blocked_observation['status'] == 'blocked' and blocked_observation['images'] == []
        assert blocked_observation['page_check']['reason'] == 'target_missing'
        assert blocked_observation['diagnostic_images'] != blocked['diagnostic_images']

        blank = run('exec', '--', 'screenshot')
        assert blank['verification'] == 'unverified' and blank['unverified'] == ['uniform_viewport']
        assert blank['id'] != blocked['id'] and blank['images'] != blocked['diagnostic_images']
        assert requests.count('/toolbox') == 1

        state['bundle_available'] = True
        run('exec', '--', 'open', base + '/toolbox')
        recovered = run('exec', '--', 'screenshot', '--ready-selector', '#toolbox')
        assert recovered['verification'] == 'captured' and recovered['page_check']['status'] == 'ready'
        assert recovered['session_id'] == blocked['session_id']
        assert recovered['processes'] == blocked['processes']
        recovered_observation = observe('after-observe', target='#toolbox')
        assert recovered_observation['verification'] == 'captured'
        assert recovered_observation['captures'][0]['page_check']['status'] == 'ready'

        run('exec', '--', 'open', base + '/uniform')
        uniform = run('exec', '--', 'screenshot', '--ready-selector', '#toolbox')
        assert uniform['page_check']['status'] == 'ready' and uniform['verification'] == 'unverified'
        assert Path(uniform['images'][0]).is_file()
    finally:
        observer.stdin.close()
        assert observer.wait(timeout=25) == 0, observer.stderr.read()


def test_real_playwright_adapter_readiness(rendered_fixture, prepared_runtime, tmp_path):
    from playwright.sync_api import sync_playwright

    base, _, requests = rendered_fixture
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=prepared_runtime['chrome'], headless=True)
        try:
            page = browser.new_page()
            with PageReadiness(page) as readiness:
                navigation = page.goto(base + '/toolbox?token=fixture-page-secret')
                result = readiness.check('#toolbox', timeout_ms=150, navigation=navigation, output=tmp_path)
                assert result['check']['status'] == 'blocked' and result['page_check']['reason'] == 'target_missing'
                assert result['page_check']['document_status'] == 200
                assert 404 in result['page_check']['script_http_statuses']
                assert result['page_check']['page_errors'] >= 1
                assert result['page_check']['console_errors'] >= 1
                assert len(result['artifacts']) == 2 and all((tmp_path / path).is_file() for path in result['artifacts'])
                assert '-secret' not in json.dumps(result)
                assert '-secret' not in (tmp_path / result['artifacts'][-1]).read_text(encoding='utf-8')
                assert requests.count('/toolbox') == 1

                page.goto(base + '/delayed', wait_until='domcontentloaded')
                delayed = readiness.check('#toolbox', timeout_ms=3000)
                assert delayed['check']['status'] == 'passed'

                navigation = page.goto(base + '/redirect')
                redirected = readiness.check('#toolbox', timeout_ms=150, navigation=navigation)
                assert redirected['check']['status'] == 'blocked'
                assert redirected['page_check']['url'] == base + '/login'
        finally:
            browser.close()
