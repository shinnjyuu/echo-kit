"""Explicit real-browser contract evidence, using only an isolated Kit data home."""
import json
import os
import queue
import statistics
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from echo_kit import __version__
from echo_kit.core import init_workspace


def main():
    destination = Path(sys.argv[1]).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    workspace = destination / 'workspace'
    if not (workspace / 'echo-kit.toml').exists():
        init_workspace(workspace)
    subprocess.run(['git', 'init', '-q', str(workspace)], check=True)
    css = 'body{margin:0;font:16px Arial}.top{height:62px;background:#dbeafe}.panel{max-width:1000px;margin:auto;padding:20px}dialog{width:240px;height:90px}'
    def html(reference=False):
        style = css if reference else css.replace('62px', '72px')
        return ('<!doctype html><meta charset="utf-8"><title>Controlled native fixture</title><link rel="icon" href="data:,">'
                '<style>' + style + '</style><header class="top">Overview</header>'
                '<main class="panel"><h1>Controlled page</h1><input id="draft" aria-label="Draft">'
                '<button id="open" onclick="document.querySelector(\'dialog\').showModal()">Open dialog</button></main>'
                '<button id="fix" onclick="document.querySelector(\'.top\').style.height=\'62px\'">Fix controlled height</button>'
                '<div id="chart">Chart pending</div><div id="pulse">0</div>'
                '<dialog>Controlled dialog<button id="close" onclick="document.querySelector(\'dialog\').close()">Close</button></dialog>'
                '<script>setTimeout(()=>document.querySelector("#chart").textContent="Chart ready",60);'
                'setInterval(()=>document.querySelector("#pulse").textContent=String(performance.now()),40)</script>')
    (workspace / 'prototype.html').write_text(html(True), encoding='utf-8')
    (workspace / 'spec.md').write_text('# Controlled requirements\nHeader height: 62px.\n', encoding='utf-8')
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass
        def do_GET(self):
            body = html().encode()
            self.send_response(200); self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    env = {**os.environ, 'ECHO_KIT_TRAY': '0', 'PYTHONIOENCODING': 'utf-8'}
    assert env.get('ECHO_KIT_DATA_HOME'), 'Explicit isolated data home required'
    prefix = [sys.executable, '-I', '-m', 'echo_kit', '--offline', '--json', '--workspace', str(workspace), '--actor', 'contract']
    args = prefix + ['browser', 'observe', '--url', 'http://127.0.0.1:' + str(server.server_port),
                     '--reference', 'prototype.html', '--spec', 'spec.md']
    process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, encoding='utf-8', bufsize=1, env=env)
    replies = queue.Queue()
    threading.Thread(target=lambda: [replies.put(json.loads(line)) for line in process.stdout], daemon=True).start()
    def ask(op, id, **params):
        process.stdin.write(json.dumps({'op': op, 'id': id, **params}) + '\n'); process.stdin.flush()
        result = replies.get(timeout=90)
        assert result.get('id') == id and result.get('status') in ('ok', 'closed'), result
        return result
    try:
        ready = replies.get(timeout=120)
        assert ready.get('event') == 'ready', ready
        assert ready['headless'] is False and ready['backend'] == 'agent-browser'
        snapshot = ask('exec', 'snapshot', command=['snapshot', '-i', '-c'])
        refs = snapshot.get('data', {}).get('refs', {})
        opener = next((key for key, value in refs.items() if value.get('name') == 'Open dialog'), None)
        assert opener, snapshot
        ask('exec', 'fill', command=['fill', '#draft', 'Controlled unsaved draft'])
        ask('exec', 'dialog', command=['click', '@' + opener])
        picture = ask('observe', 'dialog-picture')
        assert len(picture['images']) == 1
        ask('exec', 'close-dialog', command=['click', '#close'])
        draft = ask('exec', 'read-draft', command=['get', 'value', '#draft'])
        assert 'Controlled unsaved draft' in json.dumps(draft)
        ask('exec', 'chart-ready', command=['wait', '--text', 'Chart ready'])
        chart = ask('exec', 'read-chart', command=['get', 'text', '#chart'])
        assert 'Chart ready' in json.dumps(chart)
        ask('exec', 'read-box', command=['get', 'box', '.top'])
        ask('exec', 'read-styles', command=['get', 'styles', '.top'])
        baseline = ask('reference', 'reference', regions=[{'id':'header','reference':'.top','target':'.top'}])
        fixed = baseline['baseline_id']
        drift = ask('compare', 'drift', baseline_id=fixed)
        assert any(d['property'] == 'box.height' and d['actual'] == 72 and d['reference'] == 62
                   for d in drift['comparisons'][0]['differences']), drift
        ask('exec', 'fix-height', command=['click', '#fix'])
        corrected = ask('compare', 'corrected', baseline_id=fixed)
        assert not any(d['property'] == 'box.height' for d in corrected['comparisons'][0]['differences']), corrected
        assert corrected['baseline_id'] == fixed
        latencies = []
        for i in range(10):
            began = time.monotonic(); ask('observe', 'warm-' + str(i)); latencies.append((time.monotonic()-began)*1000)
        closed = ask('close', 'closing')
        assert closed['status'] == 'closed'
        assert process.wait(timeout=20) == 0, process.stderr.read()
        report = {'version':__version__, 'backend':'agent-browser', 'headed':True,
                  'state_preservation':'passed', 'prototype_measurement':'62px versus 72px detected',
                  'fixed_baseline_recheck':'passed', 'async_chart_and_streaming_page':'passed',
                  'hot_observe_median_ms':statistics.median(latencies),'hot_observe_all_ms':latencies,
                  'output_dir':ready['output_dir'],'dialog_image':picture['images'][0],
                  'comparison_images':drift['images']}
        (destination / 'result.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        print(json.dumps(report))
    finally:
        if process.poll() is None:
            process.stdin.close()
            try: process.wait(timeout=20)
            except subprocess.TimeoutExpired: process.kill(); process.wait()
        server.shutdown(); server.server_close()


if __name__ == '__main__':
    main()
