"""Real Chromium evidence, using only controlled local fixtures and owned contexts."""
import json
import os
import queue
import statistics
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from test_docker_observation import visual_fixture, send

pytestmark = pytest.mark.skipif(os.environ.get('ECHO_KIT_DOCKER_TESTS') != '1',
                                reason='Explicit real Docker reference/comparison evidence')

CSS = '''body{margin:0;font:16px Arial;background:white;color:#182230}
.topbar{height:62px;background:#dbeafe;display:block;padding:0;border:0}
.panel{max-width:1236px;margin:0 auto;padding:20px;background:#f6f8fb}
dialog{width:260px;height:90px;border:1px solid #aaa}
#pulse{display:block;width:160px;height:24px}'''


def document(reference=False):
    header = 'prototype-header' if reference else 'live-header'
    content = 'prototype-content' if reference else 'live-content'
    css = '<link rel="stylesheet" href="style.css">' if reference else '<style>' + CSS.replace('62px', '72px').replace('1236px', '1000px') + '</style>'
    tag = 'div' if reference else 'header'
    return f'''<!doctype html><html><head><meta charset="utf-8"><title>Controlled fidelity fixture</title>
<link rel="icon" href="data:,">{css}</head><body>
<{tag} id="{header}" class="topbar">Controlled dashboard</{tag}>
<section id="{content}" class="panel"><h1>Overview</h1><input id="draft" aria-label="Draft">
<span id="pulse">Waiting</span><div id="chart">Chart pending</div></section>
<dialog id="modal">Controlled open dialog</dialog>
<script>window.loadedAt=Date.now();setTimeout(()=>document.querySelector('#chart').textContent='Chart ready',60);
window.streamTimer=setInterval(()=>{{if(window.streaming)document.querySelector('#pulse').textContent=String(performance.now())}},30);</script>
</body></html>'''


def client(ws, base, *extra):
    args = [sys.executable, '-m', 'echo_kit', '--offline', '--workspace', str(ws.root),
            'browser', 'observe', '--url', base, '--service', 'web', *extra]
    process = subprocess.Popen(args, cwd=ws.root, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True, encoding='utf-8', bufsize=1)
    replies = queue.Queue()
    threading.Thread(target=lambda: [replies.put(json.loads(line)) for line in process.stdout], daemon=True).start()
    return process, replies


def close(process, replies):
    if process.poll() is None:
        result = send(process, replies, {'op': 'close', 'id': 'closing'})
        assert result['status'] == 'closed', result
    assert process.wait(timeout=20) == 0, process.stderr.read()


def test_real_reference_comparison_and_state_preservation(visual_fixture, capsys):
    ws, base = visual_fixture
    (ws.root / 'page.html').write_text(document(), encoding='utf-8')
    ref = ws.root / 'prototype'
    ref.mkdir()
    (ref / 'index.html').write_text(document(True), encoding='utf-8')
    (ref / 'style.css').write_text(CSS, encoding='utf-8')
    (ws.root / 'spec.md').write_text('# Dashboard\nHeader height: 62px. Content width: 1236px.\n', encoding='utf-8')
    adapter = ws.root / 'adapter.py'
    adapter.write_text('''from echo_kit.observation import serve_observer
def prepare(page, options, auth):
    page.context.add_cookies([{'name':'controlled-login','value':'test-identity','url':options['url']}])
    page.locator('#draft').fill('Unsaved controlled draft')
    page.evaluate("() => {document.querySelector('#modal').showModal();window.prepared=window.loadedAt;}")
def prepare_reference(page, options):
    assert 'auth' not in options, 'Private target authentication passed to reference hook'
    assert not page.context.cookies(), 'Target authentication leaked into reference context'
    page.locator('#draft').fill('Unsaved controlled draft')
    page.evaluate("() => document.querySelector('#modal').showModal()")
def ready(page, request):
    assert page.locator('#draft').input_value() == 'Unsaved controlled draft'
    assert page.evaluate('() => document.querySelector("#modal").open && window.prepared === window.loadedAt')
    assert page.context.cookies()[0]['name'] == 'controlled-login'
    page.locator('#chart').filter(has_text='Chart ready').wait_for(state='attached')
    if request.get('id') == 'correct':
        page.evaluate("() => {document.querySelector('.topbar').style.height='62px';document.querySelector('.panel').style.maxWidth='1236px';}")
def ready_reference(page, request):
    page.locator('#chart').filter(has_text='Chart ready').wait_for(state='attached')
    if request.get('state') == 'streaming': page.evaluate('() => window.streaming=true')
    if request.get('state') == 'missing-resource':
        page.evaluate("() => {const i=new Image();i.src='missing.png';i.width=60;i.height=30;document.body.appendChild(i);}")
serve_observer(prepare=prepare,ready=ready,prepare_reference=prepare_reference,ready_reference=ready_reference)
''', encoding='utf-8')
    process, replies = client(ws, base, '--reference', 'prototype/index.html', '--spec', 'spec.md', '--adapter', str(adapter))
    startup = replies.get(timeout=180)
    assert startup['event'] == 'ready', startup
    try:
        assert set(('inspect', 'reference', 'compare')).issubset(startup['capabilities'])
        assert 'body' not in startup['visual']['spec']
        info = send(process, replies, {'op': 'inspect', 'id': 'inspect', 'page': 'reference', 'target': '#prototype-header'})
        assert info['status'] == 'ok', info
        assert info['inspection']['elements'][0]['box']['height'] == 62
        mappings = [{'id': 'header', 'reference': '#prototype-header', 'target': '#live-header'},
                    {'id': 'content', 'reference': '#prototype-content', 'target': '#live-content'}]
        baseline = send(process, replies, {'op': 'reference', 'id': 'baseline', 'state': 'dialog', 'regions': mappings})
        assert baseline['status'] == 'ok' and baseline['verification'] == 'captured', baseline
        fixed = baseline['baseline_id']
        original = Path(baseline['images'][0]).read_bytes()
        wrong = send(process, replies, {'op': 'compare', 'id': 'wrong-state', 'baseline_id': fixed, 'state': 'closed'})
        assert wrong['status'] == 'error' and not wrong['images']
        drift = send(process, replies, {'op': 'compare', 'id': 'drift', 'baseline_id': fixed, 'state': 'dialog'})
        assert drift['status'] == 'ok', drift
        assert drift['comparisons'][0]['pixels']['different_pixels'] > 0
        assert any(d['property'] == 'box.height' and d['reference'] == 62 and d['actual'] == 72
                   for d in drift['comparisons'][0]['differences'])
        assert any(d['property'] == 'styles.max-width' and d['actual'] == '1000px'
                   for d in drift['comparisons'][1]['differences'])
        corrected = send(process, replies, {'op': 'compare', 'id': 'correct', 'baseline_id': fixed})
        assert corrected['status'] == 'ok', corrected
        assert all(c['pixels']['different_pixels'] == 0 for c in corrected['comparisons']), corrected
        assert Path(baseline['images'][0]).read_bytes() == original
        assert corrected['baseline_id'] == fixed
        latencies = []
        for i in range(10):
            result = send(process, replies, {'op': 'compare', 'id': 'hot-' + str(i), 'baseline_id': fixed})
            assert result['status'] == 'ok' and result['verification'] == 'compared', result
            latencies.append(result['elapsed_ms'])
        noisy = send(process, replies, {'op': 'reference', 'id': 'noisy', 'state': 'streaming'})
        assert noisy['verification'] == 'unverified', noisy
        masked = send(process, replies, {'op': 'reference', 'id': 'masked', 'state': 'streaming',
                                        'exclude': [{'reference': '#pulse', 'target': '#pulse'}]})
        assert masked['verification'] == 'captured', masked
        (ref / 'style.css').write_text(CSS.replace('#dbeafe', '#dcfce7'), encoding='utf-8')
        stale = send(process, replies, {'op': 'compare', 'id': 'stale', 'baseline_id': fixed})
        assert stale['status'] == 'error' and not stale['images']
        fresh = send(process, replies, {'op': 'reference', 'id': 'new-reference', 'regions': mappings, 'reload': True})
        assert fresh['status'] == 'ok' and fresh['baseline_id'] != fixed, fresh
        (ws.root / 'spec.md').write_text('# Dashboard\nUpdated selected requirements.\n', encoding='utf-8')
        outdated = send(process, replies, {'op': 'compare', 'id': 'outdated-spec', 'baseline_id': fresh['baseline_id']})
        assert outdated['status'] == 'error' and not outdated['images']
        new_spec = send(process, replies, {'op': 'reference', 'id': 'new-spec', 'regions': mappings})
        assert new_spec['spec']['sha256'] != baseline['spec']['sha256'], new_spec
        assert send(process, replies, {'op': 'compare', 'id': 'updated-spec', 'baseline_id': new_spec['baseline_id']})['status'] == 'ok'
        # A specification-only session remains useful, and cannot claim pixel fidelity.
        second, messages = client(ws, base, '--spec', 'spec.md')
        try:
            assert messages.get(timeout=60)['event'] == 'ready'
            inspected = send(second, messages, {'op': 'inspect', 'id': 'md-inspect', 'target': '.topbar'})
            assert inspected['status'] == 'ok'
            unavailable = send(second, messages, {'op': 'reference', 'id': 'md-reference'})
            assert unavailable['status'] == 'error' and not unavailable['images']
        finally:
            close(second, messages)
        remote, messages = client(ws, base, '--reference', base)
        try:
            assert messages.get(timeout=60)['event'] == 'ready'
            remote_base = send(remote, messages, {'op': 'reference', 'id': 'remote-base'})
            assert remote_base['status'] == 'ok', remote_base
            remote_match = send(remote, messages, {'op': 'compare', 'id': 'remote-match', 'baseline_id': remote_base['baseline_id']})
            assert remote_match['status'] == 'ok' and remote_match['comparisons'][0]['pixels']['different_pixels'] == 0, remote_match
        finally:
            close(remote, messages)
        broken = send(process, replies, {'op': 'reference', 'id': 'missing-resource', 'state': 'missing-resource'})
        assert broken['status'] == 'ok' and broken['verification'] == 'unverified', broken
        assert send(process, replies, {'op': 'observe', 'id': 'still-alive'})['status'] == 'ok'
        print(json.dumps({'startup_ms': startup['startup_ms'], 'hot_compare_median_ms': statistics.median(latencies),
                          'hot_compare_all_ms': latencies, 'viewer_url_present': bool(startup['viewer_url'])}))
    finally:
        close(process, replies)
    output = Path(startup['output_dir'])
    assert (output / 'trace.zip').is_file() and (output / 'reference-trace.zip').is_file()
    assert json.loads((output / 'session.json').read_text())['status'] == 'closed'
