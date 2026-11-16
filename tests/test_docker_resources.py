"""Opt-in disposable local containers; never queries or changes business instances."""
import os
import subprocess

import pytest

from echo_kit import docker_host, resource_usage as usage, workspace_index
from echo_kit.core import Workspace, init_workspace, save

pytestmark = pytest.mark.skipif(os.environ.get('ECHO_KIT_DOCKER_TESTS') != '1',
                                reason='Separate real Docker resource evidence')


def test_owned_containers_batch_stopped_and_wrong_owner(tmp_path, monkeypatch):
    host = docker_host.selected_host()
    assert docker_host.is_local(host), 'Disposable resource tests require a local Docker engine'
    created, workspaces = [], []
    def docker(*args):
        argv, options = docker_host.command(args, host)
        return subprocess.check_output(argv, text=True, encoding='utf-8', timeout=20, **options).strip()
    try:
        for index in range(3):
            init_workspace(tmp_path / str(index))
            ws = Workspace(tmp_path / str(index))
            ws.cfg['services'] = {}
            owner = ws.identity if index != 2 else 'deliberate-test-mismatch'
            cid = docker('create', '--label', 'echo-kit.workspace=' + owner, 'node:22-bookworm-slim',
                         'node', '-e', 'const data=Buffer.alloc(2097152,1); setInterval(()=>data[0],1000)')
            created.append(cid)
            save(ws.state / 'browser.json', {'id': cid, 'docker_host': host})
            workspace_index.register(ws)
            docker('start', cid)
            workspaces.append(ws)
        original = usage._docker
        calls = []
        def observed(args, engine, deadline):
            calls.append(args)
            return original(args, engine, deadline)
        monkeypatch.setattr(usage, '_docker', observed)
        result = usage.status(limit=20)
        rows = {i['instance_id']: i for i in result['items']}
        assert result['status'] == 'unverified'
        assert rows[created[0]]['memory_bytes'] > 0 and rows[created[1]]['memory_bytes'] > 0
        assert rows[created[2]]['memory_bytes'] is None
        assert 'container_ownership_unverified' in str(rows[created[2]]['diagnostics'])
        stats, = [c for c in calls if c[0] == 'stats']
        assert created[0] in stats and created[1] in stats and created[2] not in stats
        docker('kill', created[0])
        stopped = usage.status(workspaces[0])['items'][0]
        assert stopped['memory_bytes'] == 0 and stopped['state'] == 'exited'
        docker('pause', created[1])
        paused = usage.status(workspaces[1])['items'][0]
        assert paused['state'] == 'paused' and paused['memory_bytes'] is not None
    finally:
        # Only exact IDs returned by this test's own docker create are removed.
        for cid in created:
            docker('rm', '-f', cid)
