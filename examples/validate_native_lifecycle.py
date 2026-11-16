"""Explicit native lifecycle evidence, confined to a fresh private Kit data home.

Run with a prepared pinned runtime cache; this does not use business projects,
Docker, remote sites, or the user's normal emergency-stop scope. Dependency files
are hard-linked where possible into the private cache, with copying as a fallback.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from echo_kit import __version__
from echo_kit.core import init_workspace, save
from echo_kit.native_runtime import manifest
from echo_kit.processes import identity_exited, kill_tree, matches


def link_or_copy(source, destination):
    try:
        os.link(source, destination)
    except OSError:
        shutil.copy2(source, destination)
    return str(destination)


def wait_until(predicate, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.05)
    raise AssertionError('Timed out waiting for controlled lifecycle state')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination', type=Path, help='New empty evidence directory')
    parser.add_argument('--runtime-home', type=Path, required=True, help='Existing isolated Kit data home with prepared pinned runtimes')
    parser.add_argument('--headless', action='store_true', help='Use explicit headless mode when no desktop is available')
    args = parser.parse_args()
    destination = args.destination.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    if list(destination.iterdir()):
        raise SystemExit('Evidence destination must be empty')
    private = destination / 'data'
    private.mkdir()
    assert private.resolve().parent == destination and private.name == 'data'
    source = args.runtime_home.resolve() / 'native-runtime'
    if not source.is_dir() or source.is_relative_to(destination):
        raise SystemExit('A separate, already prepared runtime cache is required')
    pinned = manifest()
    for component in ('agent_browser', 'chrome'):
        version = pinned[component + '_version']
        for platform in pinned['platforms']:
            installed = source / component / version / platform
            if installed.is_dir():
                shutil.copytree(installed, private / 'native-runtime' / component / version / platform,
                                copy_function=link_or_copy)
    workspace = destination / 'workspace'
    init_workspace(workspace)
    subprocess.run(['git', 'init', '-q', str(workspace)], check=True)
    busy = threading.Event()
    counter_lock = threading.Lock()
    mutations = [0]

    class Handler(BaseHTTPRequestHandler):
        def handle(self):
            try:
                super().handle()
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, *_):
            pass

        def do_GET(self):
            if self.path.startswith('/identity'):
                payload = json.dumps({'cookie': 'controlled-cookie' in self.headers.get('Cookie', '')}).encode()
                content_type = 'application/json'
            elif self.path.startswith('/busy-start'):
                busy.set()
                payload, content_type = b'{}', 'application/json'
            elif self.path.startswith('/mutate'):
                with counter_lock:
                    mutations[0] += 1
                payload, content_type = b'{}', 'application/json'
            else:
                payload = b'''<!doctype html><meta charset="utf-8"><title>Controlled lifecycle</title>
<link rel="icon" href="data:,"><h1>Controlled native lifecycle</h1>
<input id="draft" aria-label="Draft"><div id="identity">loading</div><div id="path"></div>
<button id="mutate" onclick="fetch('/mutate')">Mutate once</button>
<script>document.querySelector('#path').textContent=location.pathname;
fetch('/identity').then(r=>r.json()).then(v=>{let e=document.querySelector('#identity');
e.textContent=v.cookie&&localStorage.getItem('identity')==='controlled-local-storage'?'authenticated':'guest';
e.dataset.ready='yes';});</script>'''
                content_type = 'text/html; charset=utf-8'
            self.send_response(200)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            try:
                self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{server.server_port}'
    url = base + '/actual'
    auth_script = workspace / 'prepare_auth.py'
    auth_script.write_text(
        'import json,sys\np=json.load(sys.stdin)\n'
        'state={"cookies":[{"name":"controlled","value":"controlled-cookie","domain":"127.0.0.1","path":"/",'
        '"httpOnly":True,"secure":False,"expires":-1,"sameSite":"Lax"}],'
        '"origins":[{"origin":' + repr(base) + ',"localStorage":[{"name":"identity","value":"controlled-local-storage"}]}]}\n'
        'print(json.dumps({"account":p["account"],"target":p["target"],"valid":True,"state":state}))\n',
        encoding='utf-8')
    with (workspace / 'echo-kit.toml').open('a', encoding='utf-8') as config:
        config.write('\n[browser]\nbackend="agent-browser"\nheadless=' + str(args.headless).lower() + '\n'
                     '\n[auth.controlled]\naccount="lifecycle-' + uuid.uuid4().hex + '"\n'
                     'target=' + json.dumps(base) + '\ncommand=["{python}","prepare_auth.py"]\n')
    env = {**os.environ, 'ECHO_KIT_DATA_HOME': str(private), 'ECHO_KIT_TRAY': '0',
           'PYTHONIOENCODING': 'utf-8', 'PYTHON_KEYRING_BACKEND': 'keyring.backends.null.Keyring'}
    report = {'version': __version__, 'headless': args.headless, 'data_home': str(private), 'checks': {}}
    known = []

    def cli(actor, *command, check=True, timeout=120, input=None):
        arguments = [sys.executable, '-I', '-m', 'echo_kit', '--offline', '--json', '--workspace', str(workspace),
                     '--actor', actor, *command]
        options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
        outcome = subprocess.run(arguments, env=env, capture_output=True, text=True, encoding='utf-8',
                                 timeout=timeout, input=input, **options)
        try:
            value = json.loads(outcome.stdout)
        except ValueError:
            raise AssertionError('CLI did not return one JSON result: ' + outcome.stderr[-1000:]) from None
        if check and outcome.returncode:
            raise AssertionError(value)
        return value

    def up(actor, auth=False):
        result = cli(actor, 'browser', 'up', '--url', url, *(['--auth', 'controlled'] if auth else []))
        assert result['backend'] == 'agent-browser'
        identities = [result['identity'], *result['children']]
        assert all(matches(identity) for identity in identities)
        known.extend(identities)
        return result

    def execute(actor, *tokens, check=True, timeout=30):
        return cli(actor, 'browser', 'exec', '--', *tokens, check=check, timeout=timeout)

    def emergency():
        # Never run emergency with a guessed/default user data directory.
        assert env['ECHO_KIT_DATA_HOME'] == str(private)
        assert private.resolve().parent == destination and private != args.runtime_home.resolve()
        return cli('lifecycle', 'emergency', 'stop', '--all', check=False, timeout=30)

    try:
        a = up('actor-a', auth=True)
        b = up('actor-b')
        assert a['session_id'] != b['session_id']
        assert {i['pid'] for i in a['children']}.isdisjoint({i['pid'] for i in b['children']})
        execute('actor-a', 'wait', '#identity[data-ready=yes]')
        execute('actor-b', 'wait', '#identity[data-ready=yes]')
        assert execute('actor-a', 'get', 'text', '#identity')['data']['text'] == 'authenticated'
        assert execute('actor-b', 'get', 'text', '#identity')['data']['text'] == 'guest'
        assert execute('actor-a', 'get', 'text', '#path')['data']['text'] == '/actual'
        report['checks']['private_auth_and_target_url'] = 'passed'
        execute('actor-a', 'fill', '#draft', 'actor-a unsaved state')
        execute('actor-b', 'fill', '#draft', 'actor-b unsaved state')
        assert execute('actor-a', 'get', 'value', '#draft')['data']['value'] == 'actor-a unsaved state'
        assert execute('actor-b', 'get', 'value', '#draft')['data']['value'] == 'actor-b unsaved state'
        report['checks']['actor_context_isolation'] = 'passed'
        report['checks']['startup_client_exit_preserves_worker'] = 'passed'
        public = (workspace / '.echo-kit/native-browsers' / (a['session_id'] + '.json')).read_text()
        assert 'controlled-cookie' not in public and 'controlled-local-storage' not in public
        count_before = mutations[0]
        kill_tree(b['children'][0], {})
        wait_until(lambda: all(identity_exited(i) for i in [b['identity'], *b['children']]))
        failed = execute('actor-b', 'click', '#mutate', check=False)
        assert failed['status'] == 'blocked'
        assert mutations[0] == count_before
        assert all(identity_exited(i) for i in [b['identity'], *b['children']])
        report['checks']['crash_does_not_recreate_or_replay'] = 'passed'
        cli('actor-a', 'browser', 'down')
        assert all(identity_exited(i) for i in [a['identity'], *a['children']])
        report['checks']['owned_session_down'] = 'passed'
        a = up('actor-a')
        b = up('actor-b')
        expression = "(()=>{if(!window.__busy){window.__busy=true;fetch('/busy-start')}return false})()"
        with ThreadPoolExecutor(max_workers=1) as executor:
            blocked = executor.submit(execute, 'actor-a', 'wait', '--fn', expression, '--timeout', '60000',
                                      check=False, timeout=80)
            assert busy.wait(15), 'Controlled wait did not enter the browser'
            stopped = emergency()
            assert stopped['status'] == 'passed', stopped
            wait_until(lambda: all(identity_exited(i) for i in [a['identity'], *a['children'], b['identity'], *b['children']]))
            interrupted = blocked.result(timeout=15)
            assert interrupted['status'] == 'blocked'
        report['checks']['emergency_busy_and_idle_sessions'] = 'passed'
        report['emergency_counts'] = stopped['counts']
        restarted = up('actor-a')
        assert restarted['instance_id'] != a['instance_id']
        execute('actor-a', 'wait', '#identity[data-ready=yes]')
        cli('actor-a', 'browser', 'down')
        report['checks']['restart_after_emergency'] = 'passed'
        report['status'] = 'passed'
    except BaseException as error:
        report['status'] = 'failed'
        report['error'] = str(error)
        raise
    finally:
        try:
            report['final_emergency'] = emergency()
            wait_until(lambda: all(identity_exited(i) for i in known), timeout=10)
            report['known_processes_exited'] = True
        finally:
            server.shutdown()
            server.server_close()
            save(destination / 'result.json', report)
            print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
