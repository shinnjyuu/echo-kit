from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
import tomllib
import uuid
from pathlib import Path

from filelock import FileLock


class KitError(Exception):
    pass


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(tmp, path)


def read(path, default=None):
    path = Path(path)
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default


def merge(left, right):
    out = dict(left)
    for key, value in right.items():
        out[key] = merge(out[key], value) if isinstance(value, dict) and isinstance(out.get(key), dict) else value
    return out


def safe_name(name):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', name):
        raise KitError('Invalid identifier: ' + str(name))
    return name


class Workspace:
    def __init__(self, root='.', environment=None):
        self.root = Path(root).resolve()
        path = self.root / 'echo-kit.toml'
        if not path.exists():
            raise KitError('Missing echo-kit.toml; run workspace init')
        cfg = tomllib.loads(path.read_text(encoding='utf-8-sig'))
        local = self.root / '.echo-kit/local.toml'
        if local.exists():
            cfg = merge(cfg, tomllib.loads(local.read_text(encoding='utf-8-sig')))
        self.environment = environment or cfg.get('default_environment', 'local')
        envs = cfg.get('environments', {})
        if environment and environment not in envs:
            raise KitError('Unknown environment: ' + environment)
        self.cfg = merge(cfg, envs.get(self.environment, {}))
        if self.cfg.get('schema_version') != 1:
            raise KitError('Unsupported configuration schema_version')
        self.state = self.root / '.echo-kit'
        self.runs = self.path(self.cfg.get('output', '.echo-kit/runs'))
        self.identity = hashlib.sha256(str(self.root).encode()).hexdigest()[:20]
        from .tasks import CURRENT_TASK
        task = CURRENT_TASK.get()
        if task and Path(task['workspace']).resolve() != self.root:
            task = None
        self.task_id = task['id'] if task else None
        self.actor = task.get('label') if task else None
        self.tool_update = task.get('update') if task else None

    def path(self, value):
        return (self.root / value).resolve()

    def project(self, name=None):
        if not name:
            return self.root
        try:
            path = self.path(self.cfg['projects'][name]['path'])
        except KeyError:
            raise KitError('Unknown project: ' + name)
        if not path.is_dir():
            raise KitError('Missing project directory: ' + str(path))
        return path

    def lock(self):
        self.state.mkdir(parents=True, exist_ok=True)
        return FileLock(str(self.state / 'operations.lock'), timeout=1)

    def command(self, spec):
        command = spec.get('command')
        if not isinstance(command, list) or not command or not all(isinstance(x, str) for x in command):
            raise KitError('command must be a nonempty string array')
        def expand(value):
            if value == '{python}':
                return sys.executable
            return re.sub(r'\$\{([A-Za-z_][A-Za-z0-9_]*)\}', lambda m: required_env(m[1]), value)
        return [expand(x) for x in command]

    def env(self, spec):
        env = dict(os.environ)
        env.update({k: str(v) for k, v in spec.get('env', {}).items()})
        for key, source in spec.get('env_refs', {}).items():
            env[key] = required_env(source)
        return env


def required_env(name):
    if name not in os.environ:
        raise KitError('Missing environment variable: ' + name)
    return os.environ[name]


def versions(ws):
    result = {}
    for name in ws.cfg.get('projects', {}):
        path = ws.project(name)
        try:
            p = subprocess.run(['git', '-C', str(path), 'rev-parse', 'HEAD'], capture_output=True, text=True, timeout=5)
            dirty = subprocess.run(['git', '-C', str(path), 'status', '--porcelain'], capture_output=True, text=True, timeout=5)
            result[name] = {'commit': p.stdout.strip() if p.returncode == 0 else None, 'dirty': bool(dirty.stdout)}
        except (OSError, subprocess.TimeoutExpired):
            result[name] = {'commit': None}
    return result


def new_run(ws, case, kind, inputs=None):
    from .documentation import tool_info
    rid = time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:10]
    path = ws.runs / rid
    path.mkdir(parents=True)
    record = {'id': rid, 'case': case, 'kind': kind, 'environment': ws.environment,
              'started': time.time(), 'status': 'running', 'versions': versions(ws),
              'tool': tool_info(), 'task_id': ws.task_id, 'update': ws.tool_update,
              'input_keys': sorted((inputs or {}).keys()), 'checks': [], 'events': []}
    save(path / 'run.json', record)
    return path, record


def event(path, record, label, status):
    record['events'].append({'time': time.time(), 'label': label, 'status': status})
    save(path / 'run.json', record)


def init_workspace(root):
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    dest = root / 'echo-kit.toml'
    if dest.exists():
        raise KitError('Workspace exists; not overwriting')
    dest.write_text('schema_version = 1\nname = "my-workspace"\ndefault_environment = "local"\n\n[projects.main]\npath = "."\n', encoding='utf-8')
    (root / 'echo').mkdir(exist_ok=True)
    ignore = root / '.gitignore'
    body = ignore.read_text(encoding='utf-8') if ignore.exists() else ''
    if '.echo-kit/' not in body.splitlines():
        ignore.write_text(body.rstrip() + '\n.echo-kit/\n', encoding='utf-8')
    return {'status': 'passed', 'workspace': str(root)}
