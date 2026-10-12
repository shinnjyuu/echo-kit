"""User-local managed instances. References are not liveness or usage leases."""
import hashlib
import hmac
import json
import os
import secrets
import time
import uuid
from pathlib import Path

import psutil
from filelock import FileLock

from .core import KitError, read, save
from .operations import canonical_path, data_root, service_resources, Journal, _held
from .processes import fingerprint


def key(ws, name):
    spec = ws.cfg['services'][name]
    rid = spec.get('resource_id')
    if rid is not None and (not isinstance(rid, str) or not rid.strip()):
        raise KitError('resource_id must be a nonempty string')
    identity = ['shared', rid] if rid else ['local', canonical_path(ws.root), ws.environment, name]
    return hashlib.sha256(json.dumps(identity).encode()).hexdigest()


def process_state(identity):
    try:
        if psutil.Process(identity['pid']).status() == psutil.STATUS_ZOMBIE:
            return 'stopped'
        actual = fingerprint(identity['pid'])
        if 'command_digest' in identity:
            actual['command_digest'] = Registry().digest(actual.pop('command'), create=False)
        if actual != identity:
            return 'identity_mismatch'
        return 'stopped' if psutil.Process(identity['pid']).status() == psutil.STATUS_ZOMBIE else 'running'
    except psutil.NoSuchProcess:
        return 'stopped'
    except (psutil.Error, OSError, KitError, KeyError, TypeError):
        return 'unknown'


def _protect(data, decrypt=False):
    if os.name != 'nt':
        return data
    import ctypes
    from ctypes import wintypes
    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_char))]
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    target = Blob()
    fn = ctypes.windll.crypt32.CryptUnprotectData if decrypt else ctypes.windll.crypt32.CryptProtectData
    if not fn(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise KitError('Cannot protect local service comparison key')
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        ctypes.windll.kernel32.LocalFree(ctypes.cast(target.data, ctypes.c_void_p))


class Registry:
    def __init__(self):
        self.root = data_root() / 'services'

    def lock(self):
        self.root.mkdir(parents=True, exist_ok=True)
        return FileLock(str(self.root / 'registry.lock'), timeout=5)

    def file(self, identity):
        if len(identity) != 64 or any(c not in '0123456789abcdef' for c in identity):
            raise KitError('Invalid service registry identity')
        return self.root / (identity + '.json')

    def get(self, identity):
        return read(self.file(identity))

    def all(self):
        return [read(p) for p in sorted(self.root.glob('*.json'))]

    def digest(self, value, create=True):
        file = self.root / 'comparison.key'
        if create and not file.exists():
            with self.lock():
                if not file.exists():
                    fd = os.open(file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    with os.fdopen(fd, 'wb') as stream:
                        stream.write(_protect(secrets.token_bytes(32)))
        secret = _protect(file.read_bytes(), decrypt=True)
        return hmac.new(secret, json.dumps(value, sort_keys=True).encode(), hashlib.sha256).hexdigest()

    def cleanup_signature(self, ws, spec):
        env = ws.env(spec)
        return self.digest({'project': canonical_path(ws.project(spec.get('project'))),
                            'command': ws.command(spec),
                            'environment': {k: env[k] for k in set(spec.get('env', {})) | set(spec.get('env_refs', {}))}})

    def signature(self, ws, name):
        spec = ws.cfg['services'][name]
        def command(part):
            env = ws.env(part)
            return {'command': ws.command(part),
                    'environment': {k: env[k] for k in set(part.get('env', {})) | set(part.get('env_refs', {}))}}
        value = {'project': canonical_path(ws.project(spec.get('project'))), 'launch': command(spec),
                 'prepare': command(spec['prepare']) if spec.get('prepare') else None,
                 'artifacts': [canonical_path(ws.project(spec.get('project')) / p) for p in spec.get('artifacts', [])],
                 'endpoints': sorted(service_resources(ws, [name], builds=False)),
                 'dependencies': sorted(key(ws, dep) for dep in spec.get('depends', []))}
        return self.digest(value)

    def reference(self, ws, name, identity):
        file = ws.state / ('services-' + ws.environment + '.json')
        file.parent.mkdir(parents=True, exist_ok=True)
        with FileLock(str(file) + '.lock', timeout=5):
            state = read(file, {})
            state[name] = {'registry_id': identity}
            save(file, state)
        with self.lock():
            record = self.get(identity)
            ref = {'workspace': str(ws.root), 'environment': ws.environment, 'name': name}
            if ref not in record['references']:
                record['references'].append(ref)
                save(self.file(identity), record)

    def create(self, ws, name, instance):
        identity = key(ws, name)
        signature = self.signature(ws, name)
        process = dict(instance['identity'])
        if 'command' in process:
            process['command_digest'] = self.digest(process.pop('command'))
        with self.lock():
            previous = self.get(identity)
            if previous and process_state(previous['identity']) != 'stopped':
                raise KitError('Service instance already registered')
            record = {**instance, 'identity': process, 'registry_id': identity, 'instance_id': uuid.uuid4().hex,
                      'resource_id': ws.cfg['services'][name].get('resource_id'), 'signature': signature,
                      'origin': {'workspace': str(ws.root), 'environment': ws.environment, 'name': name},
                      'references': previous.get('references', []) if previous else [],
                      'dependencies': [key(ws, d) for d in ws.cfg['services'][name].get('depends', [])],
                      'resource_keys': list(service_resources(ws, [name], builds=False))}
            save(self.file(identity), record)
        self.reference(ws, name, identity)
        return record

    def compatible(self, ws, name, record):
        if record['signature'] != self.signature(ws, name):
            raise KitError('Shared service configuration differs; instance was not changed: ' + name)

    def migrate(self, ws, name, legacy):
        if not legacy or 'registry_id' in legacy:
            return
        current = self.get(key(ws, name))
        own_operations = {oid for oid, _ in _held.get()}
        if any(op['held'] and op['id'] not in own_operations and
               canonical_path(op['workspace']) == canonical_path(ws.root) for op in Journal().list()):
            raise KitError('Finish existing operations before migrating service registration')
        own_evidence = {Journal().show(oid).get('evidence') for oid in own_operations}
        if any(str(p.parent) not in own_evidence for p in ws.runs.glob('*/active.json')):
            raise KitError('Finish existing runs before migrating service registration')
        if process_state(legacy.get('identity')) != 'running':
            raise KitError('Legacy service identity is not confirmed; inspect original registration')
        legacy_identity = dict(legacy['identity'])
        legacy_identity['command_digest'] = self.digest(legacy_identity.pop('command'))
        same = current and current['identity'] == legacy_identity
        if current and not same and process_state(current['identity']) != 'stopped':
            raise KitError('Multiple instances for one shared identity; migration refused')
        spec = ws.cfg['services'][name]
        parent = psutil.Process(legacy['identity']['pid'])
        # Windows virtualenv redirectors can change their own cwd after launching
        # the interpreter. Check the verified tree's actual command as well.
        expected = ws.command(spec)
        candidates = [parent, *parent.children(recursive=True)]
        directory_matches = any(p.cmdline()[1:] == expected[1:] and
                                canonical_path(p.cwd()) == canonical_path(ws.project(spec.get('project')))
                                for p in candidates)
        if legacy['identity']['command'] != expected or not directory_matches:
            raise KitError('Legacy launch differs; migration refused')
        backup = ws.state / 'migrations' / ('services-' + uuid.uuid4().hex + '.json')
        save(backup, read(ws.state / ('services-' + ws.environment + '.json')))
        if same:
            self.compatible(ws, name, current)
            self.reference(ws, name, current['registry_id'])
        else:
            self.create(ws, name, legacy)

    def stop_record(self, record):
        from .processes import stop
        with self.lock():
            for other in self.all():
                if record['registry_id'] in other.get('dependencies', []) and process_state(other['identity']) != 'stopped':
                    raise KitError('A registered dependent service is still running')
        state = process_state(record['identity'])
        if state not in ('running', 'stopped'):
            raise KitError('Cannot confirm service identity; refusing to stop')
        if state == 'running':
            stop(record['identity'])
            deadline = time.monotonic() + 3
            while process_state(record['identity']) != 'stopped' and time.monotonic() < deadline:
                time.sleep(.05)
            if process_state(record['identity']) != 'stopped':
                for oid, _ in _held.get():
                    Journal().update(oid, requires_resolution=True)
                raise KitError('Service exit is unconfirmed; retain registration')
        with self.lock():
            current = self.get(record['registry_id'])
            if current['instance_id'] != record['instance_id']:
                raise KitError('Service generation changed while stopping')
            current['retired'] = True
            save(self.file(record['registry_id']), current)


def validate_run_instances(record):
    registry = Registry()
    for item in record.get('services', {}).values():
        original = item.get('instance') or {}
        if original.get('registry_id'):
            current = registry.get(original['registry_id'])
            if not current or current.get('instance_id') != original.get('instance_id'):
                raise KitError('Original service instance changed; cleanup requires manual inspection')
