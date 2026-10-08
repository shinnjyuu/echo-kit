"""Bounded startup scheduling; business-specific orchestration stays in projects."""
import sys
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from contextvars import copy_context

from .core import KitError, read, safe_name
from .operations import service_names, canonical_path, activity, Journal, service_resources


def validate(ws, names, jobs):
    config = ws.cfg.get('service_startup', {})
    if not isinstance(config, dict):
        raise KitError('service_startup must be a table')
    jobs = config.get('concurrency') if jobs is None else jobs
    if jobs is not None and (type(jobs) is not int or jobs < 1):
        raise KitError('Service concurrency must be a positive integer')
    specs = ws.cfg.get('services', {})
    # Validate before traversal so malformed dependencies fail without side effects.
    seen = set()
    visiting = set()
    def check(name):
        safe_name(name)
        if name in visiting:
            raise KitError('Service dependency cycle')
        if name in seen:
            return
        seen.add(name)
        if name not in specs:
            raise KitError('Unknown service: ' + name)
        spec = specs[name]
        if not isinstance(spec, dict) or spec.get('mode', 'managed') not in ('managed', 'external'):
            raise KitError('Invalid service configuration: ' + name)
        deps = spec.get('depends', [])
        if not isinstance(deps, list) or any(not isinstance(d, str) for d in deps):
            raise KitError('depends must be a list of service names: ' + name)
        visiting.add(name)
        for dep in deps:
            check(dep)
        visiting.remove(name)
        ws.project(spec.get('project'))
        ready = spec.get('ready', {})
        if not isinstance(ready, dict) or not any(k in ready for k in ('url', 'port', 'command')):
            raise KitError('Service requires an explicit ready check: ' + name)
        if 'port' in spec and (type(spec['port']) is not int or not 0 < spec['port'] < 65536):
            raise KitError('Invalid service port: ' + name)
        if 'port' in ready and (type(ready['port']) is not int or not 0 < ready['port'] < 65536):
            raise KitError('Invalid readiness port: ' + name)
        if 'url' in ready and (not isinstance(ready['url'], str) or not ready['url'].startswith(('http://', 'https://'))):
            raise KitError('Readiness URL must be HTTP or HTTPS: ' + name)
        if 'command' in ready:
            ws.command(ready)
            ws.env(ready)
        for value in (spec.get('timeout', 30), ready.get('timeout', 3)):
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise KitError('Service timeout must be positive: ' + name)
        if spec.get('mode', 'managed') == 'managed':
            ws.command(spec)
            ws.env(spec)
            artifacts = spec.get('artifacts', [])
            if not isinstance(artifacts, list) or any(not isinstance(a, str) for a in artifacts):
                raise KitError('artifacts must be a list of paths: ' + name)
            if 'prepare' in spec:
                if not isinstance(spec['prepare'], dict):
                    raise KitError('prepare must be a command table: ' + name)
                ws.command(spec['prepare'])
                ws.env(spec['prepare'])
                value = spec['prepare'].get('timeout', 120)
                if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                    raise KitError('Prepare timeout must be positive: ' + name)
    for name in names:
        check(name)
    selected = service_names(ws, names)
    return selected, jobs if jobs is not None else max(1, len(selected))


class Startup:
    def __init__(self, manager, names, jobs):
        self.manager, self.ws = manager, manager.ws
        self.names, self.jobs = names, jobs
        self.cancel = threading.Event()
        self.started = time.monotonic()
        self.results = {}
        self.locks = {canonical_path(self.ws.project(s.get('project'))): threading.Lock()
                      for s in (self.ws.cfg['services'][n] for n in names)}

    def execute(self, name):
        started = time.monotonic()
        timings = dict(queue=started - self.started, build=0., start=0., ready=0.)
        result = {'result': 'failed', 'timings': timings}
        activity('service:' + name, 'started')
        if getattr(self.ws, 'startup_progress', False):
            print('[services] starting ' + name, file=sys.stderr, flush=True)
        try:
            spec = self.ws.cfg['services'][name]
            lock = self.locks[canonical_path(self.ws.project(spec.get('project')))]
            result['result'] = self.manager._up(name, self.cancel, timings, lock)
        except (Exception, KeyboardInterrupt) as error:
            result['error'] = str(error) or 'Startup interrupted'
            result['logs'] = 'echo-kit services logs ' + name
            result['log_paths'] = [str(path) for path in (
                self.ws.state / 'logs' / (name + '-prepare.log'),
                self.ws.state / 'logs' / (name + '.log')) if path.is_file()]
            result['stop'] = 'echo-kit services down ' + name
        finally:
            timings['total'] = time.monotonic() - self.started
            activity('service:' + name, result['result'])
            if getattr(self.ws, 'startup_progress', False):
                print(f"[services] {name}: {result['result']} ({timings['total']:.2f}s)", file=sys.stderr, flush=True)
        return result

    def run(self):
        pending = list(self.names)
        futures = {}
        failed = interrupted = False
        if not self.names:
            return self.manager.status()
        pool = ThreadPoolExecutor(max_workers=self.jobs)
        try:
            while pending or futures:
                if not failed:
                    for name in pending[:]:
                        if len(futures) >= self.jobs:
                            break
                        deps = self.ws.cfg['services'][name].get('depends', [])
                        if all(d in self.results and self.results[d]['result'] in ('ready', 'reused') for d in deps):
                            futures[pool.submit(copy_context().run, self.execute, name)] = name
                            pending.remove(name)
                if not futures:
                    break
                done, _ = wait(futures, timeout=.1, return_when=FIRST_COMPLETED)
                for future in done:
                    name = futures.pop(future)
                    self.results[name] = future.result()
                    failed |= self.results[name]['result'] == 'failed'
        except KeyboardInterrupt:
            interrupted = failed = True
            self.cancel.set()
        finally:
            # Never release the enclosing operation while a worker is still active.
            while futures:
                try:
                    done, _ = wait(futures, timeout=.1, return_when=FIRST_COMPLETED)
                    for future in done:
                        name = futures.pop(future)
                        self.results[name] = future.result()
                except KeyboardInterrupt:
                    interrupted = failed = True
                    self.cancel.set()
            pool.shutdown(wait=True)
        for name in pending:
            self.results[name] = {'result': 'not_started', 'reason': 'Startup interrupted' if interrupted else 'Another service failed',
                                  'timings': {'queue': time.monotonic() - self.started, 'build': 0., 'start': 0., 'ready': 0., 'total': time.monotonic() - self.started}}
        # Preserve the status mapping, including unselected services, without another
        # round of potentially slow readiness probes for every configured service.
        state = read(self.manager.file, {})
        from .processes import matches
        snapshot = {}
        operations = Journal().list()
        for name, spec in self.ws.cfg.get('services', {}).items():
            instance = state.get(name)
            item = {'mode': spec.get('mode', 'managed'), 'owned': bool(instance and matches(instance['identity'])),
                    'instance': instance, 'ready': None,
                    'source_changed_since_start': self.manager._source_changed(spec, instance),
                    'occupants': [op for op in operations if set(op['held']).intersection(
                        service_resources(self.ws, [name], builds=False))]}
            if name in self.results:
                item['startup'] = self.results[name]
                item['ready'] = self.results[name]['result'] in ('ready', 'reused')
                activity('startup:' + name, self.results[name]['result'], startup=self.results[name])
            snapshot[name] = item
        if failed:
            reasons = '; '.join(n + ': ' + r['error'] for n, r in self.results.items() if 'error' in r)
            error = KitError(('Service startup interrupted' if interrupted else 'Service startup failed') + ': ' + reasons)
            error.details = {'status': 'interrupted' if interrupted else 'failed', 'services': snapshot}
            raise error
        return snapshot
