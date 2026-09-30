"""Real local HTTP + independent CLI processes; never uses business environments."""
import argparse
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    root = Path(args.output).resolve()
    root.mkdir(parents=True, exist_ok=False)
    env = {**os.environ, 'ECHO_KIT_DATA_HOME': str(root / 'user-data')}
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    for label in ('a', 'b'):
        path = root / label
        path.mkdir()
        config = f'''schema_version=1
name="concurrency-{label}"
[services.api]
mode="{'managed' if label == 'a' else 'external'}"
command=["{{python}}","-m","http.server","{port}","--bind","127.0.0.1"]
port={port}
[services.api.ready]
url="http://127.0.0.1:{port}/"
[cases.http]
services=["api"]
command=["{{python}}","adapter.py"]
timeout=30
[cases.unit]
protocol="command"
command=["{{python}}","-c","assert 2+2==4"]
'''
        (path / 'echo-kit.toml').write_text(config, encoding='utf-8')
        (path / 'adapter.py').write_text(f'''import os,json,time,urllib.request
from pathlib import Path
with urllib.request.urlopen('http://127.0.0.1:{port}/', timeout=3) as response:
    assert response.status == 200
Path('entered').touch()
while not Path('finish').exists(): time.sleep(.02)
Path(os.environ['ECHO_RESULT']).write_text(json.dumps({{'status':'passed','checks':[{{'name':'http','status':'passed'}}]}}))
''', encoding='utf-8')
    results = {}

    def command(label, *arguments):
        return [sys.executable, '-m', 'echo_kit', '--workspace', str(root / label), '--actor', label, '--json', *arguments]

    def invoke(name, label, *arguments):
        value = subprocess.run(command(label, *arguments), env=env, capture_output=True, text=True, timeout=15)
        results[name] = {'exit_code': value.returncode, 'result': json.loads(value.stdout)}
        return results[name]

    a = subprocess.Popen(command('a', 'verify', 'run', 'http'), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + 15
        while not (root / 'a/entered').exists():
            if a.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError('First CLI did not enter HTTP verification')
            time.sleep(.05)
        assert invoke('same-service', 'b', 'verify', 'run', 'http')['result']['reason'] == 'resource_busy'
        assert invoke('restart-blocked', 'a', 'services', 'restart', 'api')['result']['reason'] == 'resource_busy'
        assert invoke('readonly-status', 'b', 'services', 'status')['exit_code'] == 0
        assert invoke('independent-lab', 'b', 'lab', 'run', 'unit')['exit_code'] == 0
        (root / 'a/finish').touch()
        out, err = a.communicate(timeout=15)
        assert a.returncode == 0, err
        results['first-complete'] = json.loads(out)
        (root / 'b/finish').touch()
        assert invoke('second-after-release', 'b', 'verify', 'run', 'http')['exit_code'] == 0
    finally:
        (root / 'a/finish').touch()
        try:
            a.communicate(timeout=15)
        finally:
            invoke('owned-service-stop', 'a', 'services', 'down', 'api')
            (root / 'results.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'status': 'passed', 'evidence': str(root / 'results.json')}))


if __name__ == '__main__':
    main()
