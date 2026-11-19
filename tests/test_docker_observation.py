"""Explicit Docker evidence for persistent visual observation, with local fixtures."""

import json
import os
import queue
import socket
import statistics
import shutil
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from echo_kit import browser
from echo_kit.core import Workspace, init_workspace, save
from echo_kit.operations import Journal
from test_core import free_port

pytestmark = pytest.mark.skipif(os.environ.get('ECHO_KIT_DOCKER_TESTS') != '1',
                                reason='Explicit real Docker observation evidence')


PAGE = b'''<!doctype html><meta charset="utf-8"><title>Observation fixture</title>
<style>body{font:18px sans-serif;margin:30px;background:#f3f7fa}section{padding:24px;background:white;border:1px solid #ddd}
dialog{width:260px;height:160px}#rows{height:80px;overflow:auto}#stream{display:block;width:180px;height:28px}</style>
<h1>Controlled business page</h1><section id="panel"><label>Draft <input id="draft"></label>
<div id="rows"></div><span id="stream">Connecting</span></section><dialog id="modal">An open dialog</dialog>
<script>window.loadedAt=Date.now();
fetch('/stream').then(async r=>{const reader=r.body.getReader();let n=0;while(true){const v=await reader.read();
if(v.done)break;document.querySelector('#stream').textContent='Chunk '+(++n);document.querySelector('#stream').dataset.ready='yes';}});
</script>'''


@pytest.fixture
def visual_fixture(tmp_path):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass
        def do_GET(self):
            if self.path == '/broken':
                self.send_error(503)
                return
            self.send_response(200)
            if self.path == '/stream':
                self.send_header('Content-Type', 'text/event-stream')
                self.end_headers()
                try:
                    for _ in range(1000):
                        self.wfile.write(b'data: fixture\n\n')
                        self.wfile.flush()
                        time.sleep(.05)
                except (OSError, ConnectionError):
                    pass
            else:
                self.send_header('Content-Type', 'text/html')
                body = (tmp_path / 'page.html').read_bytes()
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

    server = ThreadingHTTPServer(('0.0.0.0', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    init_workspace(tmp_path)
    (tmp_path / 'page.html').write_bytes(PAGE)
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    ws = Workspace(tmp_path)
    control, viewer = free_port(), free_port()
    while viewer == control:
        viewer = free_port()
    (tmp_path / 'echo-kit.toml').write_text(f'''schema_version = 1
name = "observation-fixture"
[browser]
backend = "playwright"
port = {control}
viewer_port = {viewer}
auto_open = false
[services.web]
mode = "external"
ready.port = {server.server_port}
''', encoding='utf-8')
    ws = Workspace(tmp_path)
    base = f'http://host.docker.internal:{server.server_port}'
    try:
        yield ws, base
    finally:
        browser.manage(ws, 'down')
        server.shutdown()
        server.server_close()


def start_client(ws, base, adapter=None, timeout_ms=10000):
    args = [sys.executable, '-m', 'echo_kit', '--offline', '--workspace', str(ws.root),
            'browser', 'observe', '--url', base, '--service', 'web', '--timeout-ms', str(timeout_ms)]
    if adapter:
        args.extend(['--adapter', str(adapter)])
    process = subprocess.Popen(args, cwd=ws.root, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True, encoding='utf-8', bufsize=1)
    replies = queue.Queue()
    threading.Thread(target=lambda: [replies.put(json.loads(line)) for line in process.stdout], daemon=True).start()
    return process, replies


def send(process, replies, request):
    process.stdin.write(json.dumps(request) + '\n')
    process.stdin.flush()
    result = replies.get(timeout=60)
    assert result['id'] == request['id'], result
    return result


def test_real_compatibility_observation_blocks_blank_app_before_capture(visual_fixture):
    from echo_kit.observation import read_reply

    ws, base = visual_fixture
    page_file = ws.root / 'page.html'
    page_file.write_text('<!doctype html><style>body{background:#f6f4f1}</style>'
                         '<script src="/broken" defer></script>', encoding='utf-8')
    process, replies = start_client(ws, base, timeout_ms=2500)
    try:
        ready = replies.get(timeout=60)
        assert ready['event'] == 'ready', ready
        blocked = send(process, replies, {'op': 'observe', 'id': 'before', 'target': '#toolbox'})
        assert blocked['status'] == 'error' and blocked['error']['code'] == 'page_not_ready'
        assert blocked['images'] == [] and blocked['page_check']['reason'] == 'target_missing'
        assert blocked['page_check']['document_status'] == 200
        assert 503 in blocked['page_check']['script_http_statuses']
        assert read_reply(ready['reply_file'], 'before')['diagnostic_images'] == blocked['diagnostic_images']
        assert Path(blocked['diagnostic_images'][0]).name.startswith(blocked['observation_id'])
        blank = send(process, replies, {'op': 'observe', 'id': 'blank'})
        assert blank['verification'] == 'unverified'
        assert blank['captures'][0]['unverified'] == ['uniform_viewport']

        page_file.write_text('<!doctype html><script>setTimeout(() => document.body.insertAdjacentHTML('
                             '"beforeend", "<h1 id=toolbox>Toolbox</h1>"), 250)</script>', encoding='utf-8')
        assert send(process, replies, {'op': 'reload', 'id': 'reload'})['status'] == 'ok'
        recovered = send(process, replies, {'op': 'observe', 'id': 'after', 'target': '#toolbox'})
        assert recovered['verification'] == 'captured'
        assert recovered['captures'][0]['page_check']['status'] == 'ready'
        assert recovered['operation_id'] == blocked['operation_id']
        assert send(process, replies, {'op': 'close', 'id': 'close'})['status'] == 'closed'
        assert process.wait(timeout=10) == 0
    finally:
        if process.poll() is None:
            process.stdin.close()
            process.wait(timeout=15)


def test_real_observation_state_reuse_and_protocol(visual_fixture):
    ws, base = visual_fixture
    adapter = ws.root / 'visual.py'
    adapter.write_text('''from echo_kit.observation import serve_observer
def prepare(page, options, auth):
    page.context.add_cookies([{'name':'fixture-login','value':'controlled','url':options['url']}])
    page.locator('#draft').fill('unsaved controlled draft')
    page.evaluate("""() => {
      const rows=document.querySelector('#rows');
      for(let i=0;i<10000;i++){const row=document.createElement('div');row.textContent='Row '+i;rows.appendChild(row);}
      document.querySelector('#modal').showModal();
      window.prepareCalls=(window.prepareCalls||0)+1;
    }""")
    return {'data':'controlled','purpose':'state preservation'}
def ready(page, request):
    if page.viewport_size['width'] == 240:
        raise RuntimeError('Controlled second-viewport failure')
    page.locator('#stream[data-ready=yes]').wait_for()
def diagnose(page, request):
    return page.evaluate("""() => ({draft:document.querySelector('#draft').value,
      modal:document.querySelector('#modal').open, loaded:window.loadedAt, prepared:window.prepareCalls||0,
      rows:document.querySelector('#rows').children.length,
      loggedIn:document.cookie.includes('fixture-login=controlled')})""")
serve_observer(prepare=prepare,ready=ready,diagnose=diagnose)
''', encoding='utf-8')
    process, replies = start_client(ws, base, adapter)
    try:
        startup = replies.get(timeout=120)
        assert startup['event'] == 'ready'
        first = send(process, replies, {'op': 'observe', 'id': 'first', 'target': '#modal', 'capture': 'element'})
        assert first['status'] == 'ok', first
        state = first['captures'][0]['diagnostics']['project']
        assert state['draft'] == 'unsaved controlled draft' and state['modal'] and state['loggedIn']
        from echo_kit.core import KitError
        with pytest.raises(KitError):
            browser.manage(ws, 'down')
        second, second_replies = start_client(ws, base)
        try:
            assert second_replies.get(timeout=60)['event'] == 'ready'
            assert send(second, second_replies, {'op': 'reload', 'id': 'other-reload'})['status'] == 'ok'
        finally:
            second.stdin.close()
            assert second.wait(timeout=20) == 0
        times = []
        for i in range(10):
            value = send(process, replies, {'op': 'observe', 'id': str(i)})
            assert value['status'] == 'ok', value
            assert value['captures'][0]['diagnostics']['project'] == state
            times.append(value['elapsed_ms'])
        multiple = send(process, replies, {'op': 'observe', 'id': 'views', 'viewports': [
            {'width': 1440, 'height': 900}, {'width': 390, 'height': 844}]})
        assert len(multiple['images']) == 2
        failure = send(process, replies, {'op': 'observe', 'id': 'missing', 'target': '#not-present'})
        assert failure['status'] == 'error' and failure['images'] == []
        recovered = send(process, replies, {'op': 'observe', 'id': 'recover'})
        assert recovered['captures'][0]['viewport'] == {'width': 1440, 'height': 900}
        assert recovered['captures'][0]['diagnostics']['project'] == state
        failed_multi = send(process, replies, {'op': 'observe', 'id': 'failed-multi', 'viewports': [
            {'width': 1440, 'height': 900}, {'width': 240, 'height': 240}], 'target': '#modal', 'capture': 'element'})
        assert failed_multi['status'] == 'error' and failed_multi['images'] == []
        assert send(process, replies, {'op': 'navigate', 'id': 'bad', 'url': base + '/broken'})['images'] == []
        assert send(process, replies, {'op': 'navigate', 'id': 'back', 'url': base})['status'] == 'ok'
        after = send(process, replies, {'op': 'observe', 'id': 'after'})
        assert after['captures'][0]['diagnostics']['project']['draft'] == ''
        assert send(process, replies, {'op': 'reload', 'id': 'reload'})['status'] == 'ok'
        close = send(process, replies, {'op': 'close', 'id': 'close'})
        assert close['status'] == 'closed' and process.wait(timeout=15) == 0
        output = Path(startup['output_dir'])
        assert (output / 'trace.zip').is_file()
        assert not Journal().list(unfinished=True)
        save(output / 'benchmark.json', {'samples_ms': times, 'median_ms': statistics.median(times),
                                        'p95_ms': sorted(times)[-1]})
        print('Observation benchmark', output, statistics.median(times), sorted(times)[-1])
    finally:
        if process.poll() is None:
            process.stdin.close()
            process.wait(timeout=20)


def test_real_default_observer_and_eof(visual_fixture):
    ws, base = visual_fixture
    process, replies = start_client(ws, base)
    try:
        startup = replies.get(timeout=120)
        assert startup['event'] == 'ready'
        result = send(process, replies, {'op': 'observe', 'id': 'current'})
        assert result['status'] == 'ok'
        image = Path(result['images'][0])
        assert image.read_bytes().startswith(b'\x89PNG')
        (ws.root / 'page.html').write_bytes(PAGE.replace(b'font:18px', b'font:24px'))
        assert send(process, replies, {'op': 'reload', 'id': 'code-update'})['status'] == 'ok'
        updated = send(process, replies, {'op': 'observe', 'id': 'updated'})
        assert updated['captures'][0]['diagnostics']['typography'][0]['font_size'] == '24px'
        assert updated['captures'][0]['sha256'] != result['captures'][0]['sha256']
        process.stdin.close()
        assert replies.get(timeout=20)['status'] == 'closed'
        assert process.wait(timeout=20) == 0
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


def test_real_bundled_node_client(visual_fixture):
    if not shutil.which('node'):
        pytest.skip('Node is needed only for this optional host-client example')
    from importlib.resources import files
    ws, base = visual_fixture
    client = files('echo_kit').joinpath('skills/references/observe-client.mjs')
    script = ws.root / 'client.mjs'
    script.write_text('''import {readFile} from 'node:fs/promises';
import {openObserver} from ''' + json.dumps(Path(str(client)).as_uri()) + ''';
const visual = await openObserver(''' + json.dumps({'workspace': str(ws.root), 'url': base,
        'executable': str(Path(sys.executable).with_name('echo-kit.exe' if os.name == 'nt' else 'echo-kit'))}) + ''');
const start=performance.now();
const result=await visual.observe();
if(result.status!=='ok')throw new Error(JSON.stringify(result));
const beforeRead=performance.now();
const image=await readFile(result.images[0]);
const afterRead=performance.now();
if(image[0]!==137)throw new Error('Not a PNG');
await visual.close();
await visual.close();
console.log(JSON.stringify({version:visual.startup.version,startup_ms:visual.startup.startup_ms,
  worker_ms:result.elapsed_ms,request_to_reply_ms:beforeRead-start,file_read_ms:afterRead-beforeRead}));
''', encoding='utf-8')
    result = subprocess.run(['node', str(script)], cwd=ws.root, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, (result.stdout, result.stderr)
    report = json.loads(result.stdout)
    save(ws.root / 'node-client-timing.json', report)
    print('Node client delivery', report)
