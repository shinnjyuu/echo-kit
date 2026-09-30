"""Record controlled non-business failures without mocks or shared environments."""
import argparse
import json
import sys
from pathlib import Path

from echo_kit.core import Workspace, init_workspace, read, save
from echo_kit.adapter import execute
from echo_kit.runs import report
from echo_kit.runner import run


def main(root):
    root = Path(root).resolve()
    if not (root / 'echo-kit.toml').exists():
        init_workspace(root)
    ws = Workspace(root)
    write = 'import os,json;from pathlib import Path;Path(os.environ["ECHO_RESULT"]).write_text(json.dumps(%s))'
    ws.cfg['cases'] = {
        'check-failure': {'command': [sys.executable, '-c', write % repr({'status': 'passed', 'checks': [{'name': 'content', 'status': 'failed'}]})], 'required_checks': ['content']},
        'timeout': {'command': [sys.executable, '-c', 'import time;time.sleep(10)'], 'timeout': .2},
        'missing-evidence': {'command': [sys.executable, '-c', write % repr({'status': 'passed', 'checks': []})], 'required_checks': ['download']},
        'cleanup-failure': {'command': [sys.executable, '-c', 'import time;time.sleep(10)'], 'timeout': .2, 'creates_tasks': True,
                            'cleanup': {'command': [sys.executable, '-c', write % repr({'status': 'failed', 'terminal_confirmed': False})]}}}
    records = [run(ws, name)[0] for name in ws.cfg['cases']]
    # Recheck this local fixture through the cleanup protocol; retain failure history.
    path = ws.runs / records[-1]['id']
    recovery = execute(ws, {'command': [sys.executable, '-c', write % repr({'status': 'passed', 'terminal_confirmed': True})]},
                       path / 'cleanup-recheck', {'run_id': records[-1]['id'], 'active': read(path / 'active.json'),
                                                'scope': 'local sleeping subprocess only; no remote task submitted'})
    if recovery.get('terminal_confirmed'):
        record = read(path / 'run.json')
        record['cleanup_recheck'] = 'confirmed local fixture; original failure retained'
        save(path / 'run.json', record)
        report(path)
        (path / 'active.json').unlink()
    return [{'id': r['id'], 'status': r['status'], 'cleanup': r.get('cleanup')} for r in records]


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--output', required=True)
    print(json.dumps(main(p.parse_args().output), indent=2))
