"""Opt-in native tray integration with an independent, isolated fixture launcher."""

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from echo_kit import tray
from echo_kit.core import Workspace, init_workspace, read, save
from echo_kit.processes import fingerprint, identity_exited, stop
from echo_kit.service_registry import Registry
from echo_kit.services import Services


def wait_until(predicate, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if result := predicate():
            return result
        time.sleep(.05)
    raise RuntimeError('Timed out waiting for the isolated tray fixture')


def start_fixture(root):
    ws = Workspace(root)
    ws.cfg['services'] = {'fixture': {
        'resource_id': 'tray-test',
        'command': [sys.executable, '-c', 'from pathlib import Path; import time; '
                    'Path("ready").touch(); time.sleep(60)'],
        'ready': {'command': [sys.executable, '-c', 'from pathlib import Path; '
                              'raise SystemExit(not Path("ready").exists())']}}}
    Services(ws).up(['fixture'])
    tray.register(ws)
    with (root / 'tray-fixture.log').open('ab') as log:
        subprocess.Popen([str(Path(sys.executable).with_name('pythonw.exe')), '-I', __file__, 'serve', str(root)],
                         stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                         creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
                         | subprocess.CREATE_BREAKAWAY_FROM_JOB, close_fds=True)
    print(json.dumps(fingerprint(os.getpid())))


def serve_fixture(root):
    import pystray

    class AcceptanceIcon(pystray.Icon):
        def __init__(self, name, image, title, menu):
            super().__init__(name + '-test', image, 'Echo Kit 急停验收', menu)

        def run(self, setup):
            def invoke(icon):
                try:
                    wait_until(lambda: (root / 'trigger').exists())
                    item = next(item for item in icon.menu if item.text == '急停所有工作区')
                    if not item.enabled:
                        raise RuntimeError('Emergency menu item is disabled')
                    item(icon)  # Invoke the registered action inside the real Windows tray process.
                except Exception as error:
                    save(root / 'result.json', {'error': str(error)})
                    icon.stop()

            def ready(icon):
                setup(icon)
                threading.Thread(target=invoke, args=(icon,), daemon=True).start()

            super().run(setup=ready)

        def notify(self, message, title=None):
            save(root / 'result.json', {'message': message, 'title': title})
            self.stop()

    pystray.Icon = AcceptanceIcon
    tray.serve('isolated-emergency-fixture')


@pytest.mark.skipif(sys.platform != 'win32' or os.environ.get('ECHO_KIT_TRAY_TEST') != '1',
                    reason='Explicit Windows tray acceptance only')
def test_real_windows_tray_action_after_starting_process_exits(tmp_path):
    if not tray.interactive():
        pytest.skip('Interactive Windows desktop required')
    init_workspace(tmp_path)
    try:
        launcher = subprocess.run([sys.executable, __file__, 'start', str(tmp_path)],
                                  capture_output=True, text=True, timeout=25)
        assert launcher.returncode == 0, (launcher.stdout, launcher.stderr)
        assert identity_exited(json.loads(launcher.stdout))
        service, = Registry().all()
        assert not identity_exited(service['identity'])
        assert wait_until(tray.instance)['token'] == 'isolated-emergency-fixture'
        (tmp_path / 'trigger').touch()
        result = wait_until(lambda: read(tmp_path / 'result.json'))
        assert '已停止 1' in result.get('message', '') and '未确认 0' in result.get('message', ''), result
        assert identity_exited(service['identity'])
    finally:
        if tray.instance():
            tray.stop()
            wait_until(lambda: tray.instance() is None)
        for record in Registry().all():
            if not identity_exited(record['identity']):
                stop(record['identity'])


if __name__ == '__main__':
    {'start': start_fixture, 'serve': serve_fixture}[sys.argv[1]](Path(sys.argv[2]))
