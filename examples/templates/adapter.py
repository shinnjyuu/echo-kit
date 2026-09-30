import json
import os
import sys
import urllib.request
from pathlib import Path

q = json.loads(Path(os.environ['ECHO_REQUEST']).read_text())
secret = json.load(sys.stdin)
out = Path(os.environ['ECHO_RUN_DIR'])
checks, artifacts, metrics = [], [], {}
mode = sys.argv[1]
if mode == 'math':
    answer = sum(q['inputs']['numbers']) * q['inputs'].get('factor', 1)
    metrics['answer'] = answer
    checks.append({'name': 'sum', 'status': 'passed' if answer == 5 * q['inputs'].get('factor', 1) else 'failed'})
elif mode == 'api':
    req = urllib.request.Request(q['inputs']['url'] + '/download', headers={'Authorization': 'Bearer ' + secret['auth']['token']})
    with urllib.request.urlopen(req, timeout=5) as r:
        data = r.read()
    (out / 'echo.txt').write_bytes(data)
    checks.append({'name': 'download', 'status': 'passed' if data == b'Echo demo verified\n' else 'failed'})
    artifacts.append('echo.txt')
else:
    from echo_kit.browser import session
    # The demo's normal auth endpoint yielded the token. Initialize the demo page's normal token slot.
    with session(q['browser']['endpoint'], out) as context:
        context.add_init_script('localStorage.token=' + json.dumps(secret['auth']['token']))
        page = context.new_page()
        page.goto(q['inputs']['url'])
        checks.append({'name': 'page', 'status': 'passed' if page.title() == 'Echo demo' else 'failed'})
        with page.expect_download() as download:
            page.locator('#download').click()
        download.value.save_as(out / 'echo.txt')
        page.screenshot(path=str(out / 'page.png'))
        checks.append({'name': 'download', 'status': 'passed' if (out / 'echo.txt').read_bytes() == b'Echo demo verified\n' else 'failed'})
    artifacts.extend(['echo.txt', 'page.png', 'trace.zip'])
Path(os.environ['ECHO_RESULT']).write_text(json.dumps({'status': 'passed', 'checks': checks, 'artifacts': artifacts, 'metrics': metrics}), encoding='utf-8')
