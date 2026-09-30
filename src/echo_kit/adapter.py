"""File protocol for project-owned adapters; secrets only travel on stdin."""
import json
import os
import subprocess
import time
from pathlib import Path

from .core import KitError, read, save
from .processes import spawn, fingerprint, stop


def execute(ws, spec, directory, request, secret=None):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    req, result = directory / 'request.json', directory / 'result.json'
    save(req, request)
    env = ws.env(spec)
    env.update(ECHO_REQUEST=str(req), ECHO_RESULT=str(result), ECHO_RUN_DIR=str(directory),
               ECHO_ACTIVE=str(directory.parent / 'active.json'))
    start = time.monotonic()
    with (directory / 'stdout.log').open('wb') as output:
        p = spawn(ws.command(spec), ws.project(spec.get('project')), env, output, subprocess.PIPE)
        identity = fingerprint(p.pid)
        from .operations import track_child
        track_child(identity)
        try:
            p.communicate(json.dumps(secret or {}).encode(), timeout=spec.get('timeout', 60))
        except (subprocess.TimeoutExpired, KeyboardInterrupt) as error:
            stop(identity)
            p.wait(timeout=10)
            if isinstance(error, KeyboardInterrupt):
                return {'status': 'interrupted', 'checks': [], 'duration': time.monotonic() - start}
            return {'status': 'failed', 'reason': 'timeout', 'checks': [], 'duration': time.monotonic() - start}
    if not result.exists():
        return {'status': 'failed' if p.returncode else 'passed' if spec.get('protocol') == 'command' else 'unverified',
                'scope': 'command', 'exit_code': p.returncode, 'checks': [], 'duration': time.monotonic() - start}
    try:
        value = read(result)
        if not isinstance(value, dict) or value.get('status') not in {'passed', 'failed', 'blocked', 'unverified', 'interrupted'}:
            raise ValueError('Invalid result status')
        checks = value.get('checks', [])
        if not isinstance(checks, list) or any(not isinstance(c, dict) or not isinstance(c.get('name'), str) or c.get('status') not in {'passed', 'failed', 'blocked', 'unverified', 'not_applicable'} for c in checks):
            raise ValueError('Invalid checks')
        if len({c['name'] for c in checks}) != len(checks):
            raise ValueError('Duplicate check names')
        value['duration'] = time.monotonic() - start
        if p.returncode:
            value['status'] = 'failed'
        for item in value.get('artifacts', []):
            path = (directory / item).resolve()
            if not path.is_relative_to(directory.resolve()) or not path.is_file():
                raise ValueError('Artifact missing or outside adapter directory')
        return value
    except (ValueError, TypeError, KeyError) as e:
        raise KitError('Invalid adapter result: ' + str(e))
