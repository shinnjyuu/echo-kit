"""Opt-in local Docker evidence using disposable sleep/restart fixtures."""

import json
import os
import subprocess
import sys
import time

import pytest

from echo_kit import docker_host
from echo_kit.core import Workspace, init_workspace, save
from echo_kit.operations import Journal


@pytest.mark.skipif(not os.environ.get('ECHO_KIT_EMERGENCY_DOCKER_IMAGE'), reason='Explicit disposable Docker fixture image required')
def test_emergency_kills_paused_and_restarting_owned_containers(tmp_path):
    image = os.environ['ECHO_KIT_EMERGENCY_DOCKER_IMAGE']
    host = docker_host.selected_host()
    if not docker_host.is_local(host):
        pytest.skip('A local Docker Engine is required')

    def docker(*args):
        command, options = docker_host.command(args, host)
        return subprocess.run(command, capture_output=True, text=True, timeout=10, check=True, **options).stdout.strip()

    def status(cid):
        return docker('inspect', '--format', '{{.State.Status}}', cid)

    docker('image', 'inspect', image)
    created = []
    journal = Journal()

    def start(name, args, body):
        root = tmp_path / name
        init_workspace(root)
        ws = Workspace(root)
        cid = docker('run', '--pull=never', '-d', '--label', f'echo-kit.workspace={ws.identity}', *args,
                     image, 'sh', '-c', body)
        created.append(cid)
        if name != 'unregistered':
            save(ws.state / 'browser.json', {'id': cid, 'docker_host': host})
            journal.release(journal.acquire(ws, 'browser.up', {}))
        return cid

    try:
        paused = start('paused', [], 'exec sleep 120')
        docker('pause', paused)
        unrelated = start('unregistered', [], 'exec sleep 120')
        restarting = start('restarting', ['--restart=always'], 'exit 1')
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            phase, count = docker('inspect', '--format', '{{.State.Status}} {{.RestartCount}}', restarting).split()
            if phase == 'restarting' and int(count) >= 5:
                break  # The restart backoff is long enough for a separate CLI to connect.
            time.sleep(.05)
        else:
            pytest.fail('Fixture did not reach a sustained restarting state')
        assert status(paused) == 'paused'

        result = subprocess.run([sys.executable, '-m', 'echo_kit', '--json', 'emergency', 'stop', '--all'],
                                cwd=tmp_path, capture_output=True, text=True, timeout=20)
        assert result.returncode == 0, (result.stdout, result.stderr)
        assert json.loads(result.stdout)['counts']['stopped'] == 2
        assert status(paused) == status(restarting) == 'exited'
        assert status(unrelated) == 'running'
        docker('start', paused)
        assert status(paused) == 'running'
    finally:
        for cid in created:
            docker('rm', '--force', cid)
