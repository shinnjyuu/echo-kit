"""CLI supervision for a persistent, project-owned visual worker."""

import json
import os
import queue
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import psutil

from . import __version__, browser, launches
from .auth import authenticate
from .core import KitError, Workspace, read, save
from .observation import MAX_LINE, emit, public_url, request_id, url, validate
from .operations import Journal, browser_resources, operation
from .services import Services
from .tray import startup_scope


def pump(stream, destination):
    try:
        while True:
            line = stream.readline(MAX_LINE + 1)
            if not line:
                break
            destination.put(line)
    finally:
        destination.put(None)


def receive(child, replies, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        launches.checkpoint()
        try:
            line = replies.get(timeout=.1)
        except queue.Empty:
            continue
        if line is None:
            raise KitError('Observation worker exited without confirming cleanup')
        if len(line.encode('utf-8')) > MAX_LINE:
            raise KitError('Observation worker response exceeds 256 KiB')
        try:
            value = json.loads(line)
            if not isinstance(value, dict) or value.get('protocol') != 1:
                raise ValueError()
            return value
        except ValueError as error:
            raise KitError('Invalid observation worker response') from error
    raise KitError('Observation worker timed out; inspect its operation')


def require_private_output(ws):
    probe = '.echo-kit/observations/probe'
    options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
    ignored = subprocess.run(['git', 'check-ignore', '-q', '--', probe], cwd=ws.root, timeout=5, **options)
    tracked = subprocess.run(['git', 'ls-files', '--', '.echo-kit'], cwd=ws.root,
                             capture_output=True, timeout=5, **options)
    if ignored.returncode or tracked.returncode or tracked.stdout.strip():
        raise KitError('Observations require .echo-kit/ to be Git-ignored and untracked')


def write_receipt(output, result):
    save(output / 'latest.json', result)
    emit(result, sys.stdout)


def close_interrupted(child, replies):
    request_id = 'echo-interrupt-' + uuid.uuid4().hex
    try:
        child.stdin.write(json.dumps({'op': 'close', 'id': request_id}) + '\n')
        child.stdin.flush()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            result = receive(child, replies, deadline - time.monotonic())
            if result.get('id') == request_id and result.get('status') == 'closed':
                return child.wait(timeout=2) == 0
    except (KitError, OSError, subprocess.TimeoutExpired):
        pass
    return False


def relay(child, replies, output, timeout):
    incoming = queue.Queue(maxsize=32)
    threading.Thread(target=pump, args=(sys.stdin, incoming), daemon=True).start()
    seen = set()
    observations = set()
    while True:
        launches.checkpoint()
        if child.poll() is not None:
            raise KitError('Observation worker exited; its cleanup is unconfirmed')
        try:
            line = incoming.get(timeout=.1)
        except queue.Empty:
            continue
        request = None
        try:
            if line is None:
                request = {'op': 'close', 'id': 'echo-eof-' + uuid.uuid4().hex}
            else:
                if len(line.encode('utf-8')) > MAX_LINE:
                    raise KitError('Request exceeds 256 KiB')
                request = json.loads(line)
            validate(request)
            if request['id'] in seen:
                raise KitError('Request id has already been used')
            seen.add(request['id'])
        except (KitError, ValueError) as error:
            result = {'protocol': 1, 'status': 'error', 'id': request_id(request),
                      'images': [], 'error': {'code': 'invalid_request', 'message': str(error) if isinstance(error, KitError) else 'Invalid JSON'}}
            write_receipt(output, result)
            continue
        child.stdin.write(json.dumps(request, ensure_ascii=False) + '\n')
        child.stdin.flush()
        result = receive(child, replies, timeout)
        if result.get('id') != request['id']:
            raise KitError('Observation worker response id mismatch')
        images = result.get('images', [])
        if not isinstance(images, list):
            raise KitError('Invalid observation image list')
        observation_id = result.get('observation_id')
        if images and (not isinstance(observation_id, str) or observation_id in observations):
            raise KitError('Observation image freshness could not be confirmed')
        observations.add(observation_id)
        for name in images:
            path = Path(name).resolve()
            if (not path.is_relative_to(output.resolve()) or not path.is_file() or path.suffix != '.png'
                    or not path.name.startswith(observation_id + '-')):
                raise KitError('Observation image is missing or outside this session')
        if request['op'] == 'close':
            if result.get('status') != 'closed':
                raise KitError('Observation cleanup was not confirmed')
            child.stdin.close()
            if child.wait(timeout=5) != 0:
                raise KitError('Observation worker failed after cleanup')
            write_receipt(output, result)
            return
        write_receipt(output, result)


@launches.guarded
@startup_scope
def session(ws, args):
    started = time.monotonic()
    url(args.url)
    if not 1 <= args.timeout_ms <= 120000:
        raise KitError('--timeout-ms must be between 1 and 120000')
    if ws.cfg.get('browser', {}).get('endpoint'):
        raise KitError('Visual observation requires the Kit-managed Docker browser')
    adapter = ws.path(args.adapter) if args.adapter else None
    if adapter and (not adapter.is_file() or adapter.suffix != '.py'):
        raise KitError('--adapter must name a project Python script')
    require_private_output(ws)
    manager = Services(ws)
    names, jobs = manager._plan(args.service, None)
    with manager.reservation(names, 'browser.observe', extra=browser_resources(ws)) as (oid, prepared):
        if names:
            manager._up_all(names, jobs)
        auth = authenticate(ws, args.auth)[1] if args.auth else None
        info = browser.manage(ws, 'up')
        prepared()
        current = read(ws.state / 'browser.json', {})
        if current.get('id') != info['id']:
            raise KitError('Browser instance changed during observation preparation')
        launches.checkpoint()
        output = ws.state / 'observations' / oid
        output.mkdir(parents=True, exist_ok=False)
        journal = Journal()
        journal.protect_evidence(oid, output)
        from .storage_metadata import own
        own(ws, output, 'observations', oid)
        settings = {'url': args.url, 'endpoint': info['endpoint'], 'viewer_url': info.get('viewer_url'),
                    'output': str(output), 'operation_id': oid, 'auth': auth,
                    'timeout_ms': args.timeout_ms, 'generation': launches.admitted_generation()}
        command = [sys.executable, '-u', str(adapter)] if adapter else [sys.executable, '-u', '-m', 'echo_kit.observation']
        options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
        confirmed = False
        cancelled = False
        metadata = {'version': __version__, 'operation_id': oid, 'status': 'starting',
                    'url': public_url(args.url), 'browser_id': info['id'], 'viewer_url': info.get('viewer_url')}
        save(output / 'session.json', metadata)
        try:
            with (output / 'worker.log').open('w', encoding='utf-8') as errors:
                def start():
                    return subprocess.Popen(command, cwd=ws.root, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                            stderr=errors, text=True, encoding='utf-8', bufsize=1,
                                            env={**os.environ, 'PYTHONIOENCODING': 'utf-8'}, **options)
                with launches.process(ws, 'task', start, show_tray=True) as (child, identity):
                    try:
                        replies = queue.Queue(maxsize=32)
                        threading.Thread(target=pump, args=(child.stdout, replies), daemon=True).start()
                        child.stdin.write(json.dumps(settings, ensure_ascii=False) + '\n')
                        child.stdin.flush()
                        ready = receive(child, replies, max(30, args.timeout_ms / 1000 * 4 + 5))
                        if ready.get('event') != 'ready' or ready.get('version') != __version__:
                            raise KitError('Observation worker did not become ready with the current Kit version')
                        ready['startup_ms'] = round((time.monotonic() - started) * 1000, 2)
                        ready['python_executable'] = sys.executable
                        metadata['status'] = 'ready'
                        save(output / 'session.json', metadata)
                        write_receipt(output, ready)
                        relay(child, replies, output, args.timeout_ms / 1000 * 4 + 10)
                        confirmed = True
                    except KeyboardInterrupt:
                        launches.checkpoint()
                        confirmed = close_interrupted(child, replies)
                        raise
                    finally:
                        if not confirmed and child.poll() is None:
                            from .processes import kill_tree
                            try:
                                kill_tree(identity, {})
                                child.wait(timeout=5)
                            except (KitError, OSError, psutil.Error, subprocess.TimeoutExpired):
                                pass  # The tracked child and unresolved operation retain protection.
        except launches.EmergencyInterrupted:
            cancelled = True
            raise
        finally:
            metadata['status'] = 'closed' if confirmed else 'emergency_stopped' if cancelled else 'unconfirmed'
            save(output / 'session.json', metadata)
            if not confirmed and not cancelled:
                journal.update(oid, requires_resolution=True)
            own(ws, output, 'observations', oid, {**metadata, 'finished': time.time()})


def observe(args):
    for stream in (sys.stdin, sys.stdout):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    ws = Workspace(args.workspace, args.environment)
    ws.actor, ws.tool_update, ws.startup_progress = args.actor, args._update, False
    session(ws, args)
    return 0
