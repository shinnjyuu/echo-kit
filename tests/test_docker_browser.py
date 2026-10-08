"""Opt in with ECHO_KIT_DOCKER_TESTS=1; runs only disposable local containers."""
import json
import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from echo_kit import browser
from echo_kit.core import Workspace, init_workspace
from test_core import free_port

pytestmark = pytest.mark.skipif(os.environ.get('ECHO_KIT_DOCKER_TESTS') != '1', reason='Explicit real Docker evidence only')

# This deliberately bypasses noVNC's view_only UI flag and sends real RFB input.
RFB_INPUT = r'''
import socket, struct, time
s = socket.create_connection(('127.0.0.1', 5900), timeout=5)
def take(n):
 b=b''
 while len(b)<n:
  p=s.recv(n-len(b))
  if not p: raise RuntimeError('VNC closed')
  b+=p
 return b
assert take(12).startswith(b'RFB ')
s.sendall(b'RFB 003.008\n')
security=take(take(1)[0]); assert 1 in security
s.sendall(b'\x01'); assert take(4)==b'\0'*4
s.sendall(b'\x01')
header=take(24); take(struct.unpack('!I',header[20:24])[0])
# Fixed pixel format + raw encoding: prove the browser page is in the desktop.
s.sendall(struct.pack('!BxxxBBBBHHHBBBxxx',0,32,24,0,1,255,255,255,16,8,0))
s.sendall(struct.pack('!BBHi',2,0,1,0))
w,h=struct.unpack('!HH',header[:4])
s.sendall(struct.pack('!BBHHHH',3,0,0,0,w,h))
assert take(1)==b'\0'; take(1)
count=struct.unpack('!H',take(2))[0]
pixels=b''
for _ in range(count):
 x,y,rw,rh,encoding=struct.unpack('!HHHHi',take(12)); assert encoding==0
 pixels+=take(rw*rh*4)
assert pixels.count(b'\x88\xcc\x22') > 1000, 'Live green page not visible in VNC'
for down in (1,0): s.sendall(struct.pack('!BBHI',4,down,0,ord('x')))
for mask in (1,0): s.sendall(struct.pack('!BBHH',5,mask,250,250))
text=b'FORBIDDEN-VNC-CLIPBOARD'
s.sendall(struct.pack('!BxxxI',6,len(text))+text)
time.sleep(.3)
s.close()
print('live-frame-and-input-attempts-confirmed')
'''


@pytest.fixture
def ws(tmp_path):
    init_workspace(tmp_path)
    ws = Workspace(tmp_path)
    port, viewer = free_port(), free_port()
    while viewer == port:
        viewer = free_port()
    ws.cfg['browser'] = {'port': port, 'viewer_port': viewer, 'auto_open': False, 'build_timeout': 600}
    yield ws
    if (ws.state / 'browser.json').exists():
        browser.manage(ws, 'down')


def test_real_headed_viewer_readonly_isolation_and_supervision(ws, tmp_path):
    from playwright.sync_api import sync_playwright
    result = browser.manage(ws, 'up')
    assert result['viewer_read_only'] and not result['headless']
    browser.viewer_check(result['viewer_url'])
    with sync_playwright() as p:
        connection = p.chromium.connect(result['endpoint'])
        context = connection.new_context(viewport={'width': 1100, 'height': 760})
        page = context.new_page()
        page.set_content('<body style="background:#22cc88"><h1>Echo Kit live readonly acceptance</h1><input autofocus><script>window.clicks=0;document.onclick=()=>window.clicks++;</script>')
        page.locator('input').focus()
        page.wait_for_timeout(500)
        # Health probes and another client closing must not terminate this session.
        with ThreadPoolExecutor(max_workers=1) as executor:
            executor.submit(browser.connect_check, result['endpoint']).result()
        other = p.chromium.connect(result['endpoint'])
        other_context = other.new_context()
        other_page = other_context.new_page()
        other_page.set_content('<h1>Other isolated context</h1>')
        other_context.close()
        other.close()
        page.bring_to_front()
        page.locator('input').focus()
        page.wait_for_timeout(300)
        check = subprocess.run(['docker', 'exec', result['id'], 'python3', '-c', RFB_INPUT], capture_output=True, text=True, timeout=15)
        assert check.returncode == 0, check.stdout + check.stderr
        page.wait_for_timeout(500)
        assert page.locator('input').input_value() == ''
        assert page.evaluate('window.clicks') == 0
        page.keyboard.press('Control+V')
        assert page.locator('input').input_value() == ''
        # Viewer has disconnected. Automation still works and can type.
        page.locator('input').fill('automation still works')
        assert page.locator('input').input_value() == 'automation still works'
        page.screenshot(path=str(tmp_path / 'headed.png'))
        context.close()
        connection.close()
    with browser.session(result['endpoint'], tmp_path / 'trace') as context:
        page = context.new_page()
        page.set_content('<h1>Compatible session helper</h1>')
        assert page.title() == ''
    assert (tmp_path / 'trace/trace.zip').is_file()
    assert browser.manage(ws, 'up')['id'] == result['id']
    subprocess.run(['docker', 'exec', result['id'], 'pkill', '-TERM', 'x11vnc'], check=True)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        status = browser.manage(ws, 'status')
        if status['status'] == 'exited':
            break
        time.sleep(.2)
    assert status['status'] == 'exited'


def test_real_headless_no_viewer_and_release_ports(ws):
    from playwright.sync_api import sync_playwright
    import socket
    ws.cfg['browser']['headless'] = True
    result = browser.manage(ws, 'up')
    assert result['viewer_url'] is None
    ports = json.loads(browser.docker('inspect', result['id']))[0]['NetworkSettings']['Ports']
    assert set(ports) == {'3000/tcp'}
    processes = browser.docker('exec', result['id'], 'ps', '-eo', 'comm')
    assert not any(name in processes for name in ('Xvfb', 'x11vnc', 'websockify'))
    with browser.session(result['endpoint'], ws.root / 'trace') as context:
        page = context.new_page()
        page.set_content('<h1>Headless works</h1>')
        assert page.locator('h1').inner_text() == 'Headless works'
    browser.manage(ws, 'down')
    assert browser.manage(ws, 'status')['status'] == 'stopped'
    for port in (ws.cfg['browser']['port'], ws.cfg['browser']['viewer_port']):
        with socket.socket() as probe:
            probe.settimeout(1)
            assert probe.connect_ex(('127.0.0.1', port)) != 0
    # Docker must actually be able to publish the same ports again, even on
    # Windows where binding a native socket can hit Docker's reserved ranges.
    assert browser.manage(ws, 'up')['id'] != result['id']
