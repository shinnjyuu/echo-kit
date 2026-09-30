import socket
import subprocess
import time
import urllib.request

from .core import KitError, read, save, safe_name, versions
from .processes import fingerprint, matches, spawn, stop


def healthy(ws, spec):
    check = spec.get('ready', {})
    try:
        if 'url' in check:
            with urllib.request.urlopen(check['url'], timeout=2) as r:
                return r.status == check.get('status', 200)
        if 'port' in check:
            with socket.create_connection((check.get('host', '127.0.0.1'), check['port']), timeout=1):
                return True
        if 'command' in check:
            p = subprocess.run(ws.command(check), cwd=ws.project(spec.get('project')), env=ws.env(check),
                               capture_output=True, timeout=check.get('timeout', 3))
            return p.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False
    raise KitError('Service requires an explicit ready check')


class Services:
    def __init__(self, ws):
        self.ws = ws
        self.file = ws.state / ('services-' + safe_name(ws.environment) + '.json')

    def status(self):
        state = read(self.file, {})
        return {name: {'mode': spec.get('mode', 'managed'),
                       'owned': name in state and matches(state[name]['identity']),
                       'ready': healthy(self.ws, spec),
                       'instance': state.get(name)}
                for name, spec in self.ws.cfg.get('services', {}).items()}

    def up(self, names):
        for name in names:
            self._up(name, [])
        return self.status()

    def _up(self, name, stack):
        safe_name(name)
        specs = self.ws.cfg.get('services', {})
        if name not in specs:
            raise KitError('Unknown service: ' + name)
        if name in stack:
            raise KitError('Service dependency cycle: ' + ' -> '.join(stack + [name]))
        spec = specs[name]
        if spec.get('mode', 'managed') not in ('managed', 'external'):
            raise KitError('Unsupported service mode: ' + name)
        for dependency in spec.get('depends', []):
            self._up(dependency, stack + [name])
        state = read(self.file, {})
        own = name in state and matches(state[name]['identity'])
        if spec.get('mode', 'managed') == 'external':
            if not healthy(self.ws, spec):
                raise KitError('External service not ready: ' + name)
            return
        if not own:
            port = spec.get('port', spec.get('ready', {}).get('port'))
            if port:
                try:
                    with socket.create_connection(('127.0.0.1', port), timeout=.3):
                        raise KitError('Port occupied; declare external explicitly to reuse: ' + name)
                except (ConnectionError, TimeoutError, OSError):
                    pass
            if healthy(self.ws, spec):
                raise KitError('Unowned endpoint already available: ' + name)
            if 'prepare' in spec:
                prepare = spec['prepare']
                p = subprocess.run(self.ws.command(prepare), cwd=self.ws.project(spec.get('project')),
                                   env=self.ws.env(prepare), capture_output=True, timeout=prepare.get('timeout', 120))
                if p.returncode:
                    raise KitError('Prepare command failed: ' + name)
            log = self.ws.state / 'logs' / (name + '.log')
            log.parent.mkdir(parents=True, exist_ok=True)
            with log.open('ab') as handle:
                process = spawn(self.ws.command(spec), self.ws.project(spec.get('project')), self.ws.env(spec), handle)
            try:
                identity = fingerprint(process.pid)
            except Exception:
                raise KitError('Service exited at startup: ' + name)
            state[name] = {'identity': identity, 'log': str(log), 'started': time.time(), 'versions': versions(self.ws)}
            save(self.file, state)
        deadline = time.monotonic() + spec.get('timeout', 30)
        while time.monotonic() < deadline:
            if not matches(state[name]['identity']):
                raise KitError('Service exited: ' + name)
            if healthy(self.ws, spec):
                return
            time.sleep(.1)
        raise KitError('Service readiness timed out: ' + name)

    def down(self, names):
        # Unfinished business work must be resolved explicitly, not silently killed.
        if any(self.ws.runs.glob('*/active.json')):
            raise KitError('Unfinished runs exist; inspect and clean up before stopping services')
        state = read(self.file, {})
        selected = set(names or state)
        unknown = selected - self.ws.cfg.get('services', {}).keys() - state.keys()
        if unknown:
            raise KitError('Unknown services: ' + ', '.join(sorted(unknown)))
        for name in state:
            if name not in selected and selected.intersection(self.ws.cfg.get('services', {}).get(name, {}).get('depends', [])):
                raise KitError('A running dependent service was not selected: ' + name)
        pending = set(selected)
        while pending:
            leaves = [n for n in pending if not any(n in self.ws.cfg.get('services', {}).get(x, {}).get('depends', []) for x in pending)]
            if not leaves:
                raise KitError('Service dependency cycle')
            for name in leaves:
                if name in state:
                    if self.ws.cfg.get('services', {}).get(name, {}).get('mode') == 'external':
                        pending.remove(name)
                        continue
                    stop(state[name]['identity'])
                    del state[name]
                    save(self.file, state)
                pending.remove(name)
        return self.status()

    def logs(self, name):
        value = read(self.file, {}).get(name)
        if not value:
            raise KitError('No owned log for ' + name)
        from pathlib import Path
        return {'service': name, 'text': Path(value['log']).read_text(encoding='utf-8', errors='replace')[-16000:]}
