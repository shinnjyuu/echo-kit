import json
import subprocess
import time
from contextlib import contextmanager
from importlib.metadata import version

from .core import KitError, read, save

PLAYWRIGHT_VERSION = '1.63.0'
IMAGE = 'mcr.microsoft.com/playwright:v1.63.0-noble'


def docker(*args):
    try:
        p = subprocess.run(['docker', *args], capture_output=True, text=True, encoding='utf-8', timeout=90)
    except (OSError, subprocess.TimeoutExpired):
        raise KitError('Docker unavailable or timed out')
    if p.returncode:
        raise KitError('Docker command failed: ' + p.stderr.strip()[-600:])
    return p.stdout.strip()


def connect_check(endpoint):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.connect(endpoint, timeout=3000)
        browser.close()


def manage(ws, action):
    cfg = ws.cfg.get('browser', {})
    file = ws.state / 'browser.json'
    state = read(file, {})
    if action == 'down' and any(ws.runs.glob('*/active.json')):
        raise KitError('Unfinished runs exist; resolve them before stopping browser')
    if version('playwright') != PLAYWRIGHT_VERSION:
        raise KitError('Playwright client version mismatch')
    if cfg.get('endpoint'):
        if action != 'down':
            connect_check(cfg['endpoint'])
        return {'mode': 'external', 'endpoint': cfg['endpoint'], 'status': 'available'}
    if action == 'status' and not state:
        return {'status': 'stopped'}
    if state:
        try:
            instance = json.loads(docker('inspect', state['id']))[0]
        except KitError:
            if action == 'status':
                return {'status': 'missing', 'id': state['id']}
            raise KitError('Saved browser container missing; inspect state before replacement')
        if instance['Config'].get('Labels', {}).get('echo-kit.workspace') != ws.identity:
            raise KitError('Browser ownership mismatch')
        if action == 'down':
            docker('rm', '-f', state['id'])
            file.unlink()
            return {'status': 'stopped'}
        if action == 'status':
            return {**state, 'status': instance['State']['Status']}
        if not instance['State']['Running']:
            docker('start', state['id'])
    elif action == 'down':
        return {'status': 'stopped'}
    else:
        port = int(cfg.get('port', 19323))
        image = cfg.get('image', IMAGE)
        if not image.endswith(':v' + PLAYWRIGHT_VERSION + '-noble'):
            raise KitError('Docker image must match pinned Playwright version')
        cid = docker('run', '-d', '--init', '--label', 'echo-kit.workspace=' + ws.identity,
                     '-p', f'127.0.0.1:{port}:3000', '--add-host', 'host.docker.internal:host-gateway',
                     image, 'npx', '--yes', 'playwright@' + PLAYWRIGHT_VERSION,
                     'run-server', '--host', '0.0.0.0', '--port', '3000')
        state = {'id': cid, 'endpoint': f'ws://127.0.0.1:{port}/', 'image': image}
        save(file, state)
    deadline = time.monotonic() + cfg.get('timeout', 90)
    while time.monotonic() < deadline:
        try:
            connect_check(state['endpoint'])
            return {**state, 'status': 'ready'}
        except Exception:
            time.sleep(.5)
    raise KitError('Browser readiness timed out; container retained for diagnosis')


@contextmanager
def session(endpoint, output, storage_state=None):
    """Project adapter helper. Traces can contain business data; share deliberately."""
    from pathlib import Path
    from playwright.sync_api import sync_playwright
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.connect(endpoint)
        context = browser.new_context(storage_state=storage_state)
        context.tracing.start(screenshots=True, snapshots=True, sources=False)
        try:
            yield context
        finally:
            try:
                context.tracing.stop(path=str(output / 'trace.zip'))
            finally:
                context.close()
                browser.close()
