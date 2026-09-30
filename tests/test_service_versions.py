import subprocess
import sys
import hashlib

import pytest

from echo_kit.core import Workspace, KitError, init_workspace
from echo_kit.services import Services, source_snapshot
from test_core import free_port


def setup(tmp_path):
    init_workspace(tmp_path)
    subprocess.run(['git', 'init', str(tmp_path)], capture_output=True, check=True)
    (tmp_path / '.gitignore').write_text('.echo-kit/\nbuild/\n')
    (tmp_path / 'source.txt').write_text('initial')
    ws = Workspace(tmp_path)
    port = free_port()
    ws.cfg['services'] = {'api': {'project': 'main', 'port': port, 'ready': {'port': port},
                                  'command': [sys.executable, '-m', 'http.server', str(port), '--bind', '127.0.0.1'],
                                  'prepare': {'command': [sys.executable, '-c',
                                      "from pathlib import Path; Path('build').mkdir(exist_ok=True); Path('build/app.bin').write_bytes(b'build-one')"]},
                                  'artifacts': ['build/app.bin']}}
    return ws


def test_source_changes_during_build_prevent_launch(tmp_path):
    ws = setup(tmp_path)
    ws.cfg['services']['api']['prepare']['command'] = [sys.executable, '-c', "from pathlib import Path; Path('source.txt').write_text('changed')"]
    with pytest.raises(KitError, match='Source changed'):
        Services(ws).up(['api'])
    assert not Services(ws).file.exists()


def test_running_artifact_stays_original_when_source_changes(tmp_path):
    ws = setup(tmp_path)
    manager = Services(ws)
    try:
        first = manager.up(['api'])['api']['instance']
        before = source_snapshot(ws.root)
        (ws.root / 'source.txt').write_text('different')
        assert before != source_snapshot(ws.root)
        second = manager.up(['api'])['api']['instance']
        assert first == second
        assert first['artifacts'][0]['sha256'] == hashlib.sha256(b'build-one').hexdigest()
        assert first['source'] == before
        assert first['version_scope'] == 'build_artifact'
    finally:
        manager.down(['api'])
