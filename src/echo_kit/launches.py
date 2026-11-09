"""Admission and temporary ownership records for launch-capable Kit commands."""

import functools
import os
import subprocess
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar

import psutil
from filelock import FileLock, Timeout

from .core import KitError, read, save
from .processes import alive, fingerprint, kill_tree


FORWARDED_GENERATION = 'ECHO_KIT_FORWARDED_GENERATION'
_generation = ContextVar('echo_launch_generation', default=None)


class EmergencyInterrupted(KitError):
    def __init__(self):
        super().__init__('Interrupted by an emergency stop')
        self.details = {'status': 'interrupted', 'reason': 'emergency_stop'}


class EmergencyBusy(KitError):
    def __init__(self):
        super().__init__('An emergency stop is in progress')
        self.details = {'reason': 'emergency_in_progress'}


def directory():
    from .operations import data_root
    return data_root() / 'emergency'


def stop_lock():
    return FileLock(str(directory() / 'stop.lock'), timeout=0)


def set_gate(generation, active):
    save(directory() / 'generation.json', {'generation': generation, 'active': active})


def gate():
    state = read(directory() / 'generation.json', {'generation': 'initial', 'active': False})
    if not isinstance(state, dict) or not isinstance(state.get('generation'), str) or type(state.get('active')) is not bool:
        raise KitError('Invalid emergency launch gate')
    if state['active']:
        # A tray or CLI crash releases the OS lock even if its last marker remains.
        try:
            with stop_lock():
                state = read(directory() / 'generation.json')
        except Timeout as error:
            raise EmergencyBusy() from error
    return state['generation']


def admitted_generation():
    return _generation.get()


def checkpoint():
    generation = admitted_generation()
    if generation is None:
        return
    try:
        current = gate()
    except EmergencyBusy as error:
        raise EmergencyInterrupted() from error
    if generation != current:
        raise EmergencyInterrupted()


@contextmanager
def scope(inherited=None):
    if admitted_generation() is not None:
        checkpoint()
        yield
        return
    current = gate()
    if inherited is not None and inherited != current:
        raise EmergencyInterrupted()
    token = _generation.set(current)
    try:
        yield
    finally:
        _generation.reset(token)


def guarded(function):
    @functools.wraps(function)
    def wrapped(*args, **kwargs):
        with scope():
            return function(*args, **kwargs)
    return wrapped


@contextmanager
def launch_lock(lock):
    """Check for cancellation even while an old command is queued on a file lock."""
    deadline = time.monotonic() + lock.timeout
    while True:
        checkpoint()
        try:
            lock.acquire(timeout=max(0, min(.1, deadline - time.monotonic())))
            break
        except Timeout:
            if time.monotonic() >= deadline:
                raise
    try:
        checkpoint()
        yield
    finally:
        lock.release()


def pending_files():
    return (directory() / 'launches').glob('*.json')


class Attempt:
    def __init__(self, ws, kind):
        from .operations import _held
        self.path = directory() / 'launches' / (uuid.uuid4().hex + '.json')
        self.keep = False
        self.record = {'kind': kind, 'generation': admitted_generation(), 'owner': fingerprint(os.getpid()),
                       'workspace': str(ws.root) if ws else None, 'environment': ws.environment if ws else None,
                       'operations': [oid for oid, _ in _held.get()]}

    def publish(self, **fields):
        self.record.update(fields)
        save(self.path, self.record)


@contextmanager
def pending(ws, kind, **fields):
    with scope():
        attempt = Attempt(ws, kind)
        attempt.publish(**fields)
        try:
            # Publication precedes the last admission check. An older launch that
            # passes this check is discoverable even before Popen or Docker returns.
            checkpoint()
            yield attempt
        finally:
            if not attempt.keep:
                attempt.path.unlink(missing_ok=True)


@contextmanager
def process(ws, kind, start, *, journal=True, show_tray=False):
    with pending(ws, kind) as attempt:
        child = start()
        identity = None
        entered = False
        try:
            try:
                identity = fingerprint(child.pid)
            except psutil.NoSuchProcess:
                if child.poll() is None:
                    raise
            attempt.publish(identity=identity)
            checkpoint()
            if journal and identity:
                from .operations import track_child
                track_child(identity)
            if show_tray:
                from .tray import notify
                notify(ws)
            checkpoint()
            entered = True
            yield child, identity
            checkpoint()
        except BaseException:
            try:
                checkpoint()
            except EmergencyInterrupted:
                incomplete = False
                targets = []
                if identity:
                    try:
                        targets, incomplete = kill_tree(identity, {})
                    except (KitError, OSError, psutil.Error):
                        incomplete = child.poll() is None
                if child.poll() is None:
                    try:
                        child.kill()
                    except OSError:
                        incomplete = True
                try:
                    attempt.keep = incomplete or child.poll() is None or any(alive(target) for target in targets)
                except psutil.Error:
                    attempt.keep = incomplete = True
                if incomplete:
                    attempt.publish(uncertain='process_tree_unconfirmed')
                raise
            if child.poll() is None:
                if not entered:
                    try:
                        child.kill()
                    except OSError:
                        pass
                attempt.keep = child.poll() is None
            raise
        else:
            # Services transfer ownership to their normal registry in the body.
            attempt.keep = journal and child.poll() is None


def run_process(ws, command, *, timeout, input=None, show_tray=False, journal=True, **options):
    """Track a bounded, captured subprocess such as an auth adapter or Docker build."""
    if os.name == 'nt':
        options.setdefault('creationflags', subprocess.CREATE_NO_WINDOW)
    with process(ws, 'task', lambda: subprocess.Popen(
            command, stdin=subprocess.PIPE if input is not None else None,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, **options),
            journal=journal, show_tray=show_tray) as (child, identity):
        try:
            stdout, stderr = child.communicate(input, timeout=timeout)
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            checkpoint()
            if identity:
                from .processes import stop
                stop(identity)
            if child.poll() is None:
                child.kill()
            child.wait(timeout=3)
            raise
        return subprocess.CompletedProcess(command, child.returncode, stdout, stderr)
