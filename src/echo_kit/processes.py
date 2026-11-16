"""Small OS boundary. Ownership must be verified before termination."""
import math
import os
import subprocess
import time

import psutil

from .core import KitError


def spawn(command, cwd, env, stdout, stdin=None):
    options = {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {'start_new_session': True}
    return subprocess.Popen(command, cwd=cwd, env=env, stdout=stdout, stderr=subprocess.STDOUT,
                            stdin=stdin if stdin is not None else subprocess.DEVNULL, **options)


def fingerprint(pid):
    p = psutil.Process(pid)
    return {'pid': pid, 'created': p.create_time(), 'exe': p.exe(), 'command': p.cmdline()}


def command_matches(process, identity):
    if 'command_digest' in identity:
        from .service_registry import Registry
        return Registry().digest(process.cmdline(), create=False) == identity['command_digest']
    return process.cmdline() == identity['command']


def matches(identity):
    try:
        p = psutil.Process(identity['pid'])
        return (p.status() != psutil.STATUS_ZOMBIE and abs(p.create_time() - identity['created']) < .01
                and p.exe() == identity['exe'] and command_matches(p, identity))
    except (psutil.Error, OSError, KeyError):
        return False


def alive(process):
    try:
        return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


def identity_exited(identity):
    """Confirm that a recorded PID/birth pair is gone without signalling it."""
    try:
        pid, created = identity['pid'], identity['created']
        if type(pid) is not int or pid < 1 or type(created) not in (int, float) or not math.isfinite(created):
            return False
        process = psutil.Process(pid)
        return process.create_time() != created or not alive(process)
    except psutil.NoSuchProcess:
        return True
    except (psutil.Error, KeyError, TypeError, ValueError):
        return False


def verified_process(identity, *, check_zombie=True):
    pid = identity.get('pid')
    created = identity.get('created')
    if type(pid) is not int or pid <= 1 or type(created) not in (int, float) or not math.isfinite(created):
        raise KitError('Invalid process identity')
    try:
        process = psutil.Process(pid)
        with process.oneshot():
            if process.create_time() != created:
                raise KitError('Process identity changed')
            if check_zombie and process.status() == psutil.STATUS_ZOMBIE:
                return None
            if process.exe() != identity.get('exe') or not command_matches(process, identity):
                raise KitError('Process identity changed')
        return process
    except psutil.NoSuchProcess:
        return None


def observed_process(identity):
    # Windows has no Unix zombie state. Its status() queries thread suspension
    # across the system and is expensive; observations need identity/liveness,
    # not suspension. Destructive operations keep verified_process's defaults.
    return verified_process(identity, check_zombie=os.name != 'nt')


def parent_snapshot():
    """One process-parent table per sample, without reading unrelated commands."""
    if os.name != 'nt':
        return {p.info['pid']: p.info['ppid'] for p in psutil.process_iter(['pid', 'ppid'])}
    import ctypes
    from ctypes import wintypes
    class Entry(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('usage', wintypes.DWORD), ('pid', wintypes.DWORD),
                    ('heap', ctypes.c_size_t), ('module', wintypes.DWORD), ('threads', wintypes.DWORD),
                    ('parent', wintypes.DWORD), ('priority', wintypes.LONG), ('flags', wintypes.DWORD),
                    ('name', wintypes.WCHAR * 260)]
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Process32FirstW.argtypes = kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    kernel.Process32FirstW.restype = kernel.Process32NextW.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.CreateToolhelp32Snapshot(2, 0)  # TH32CS_SNAPPROCESS
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        value, result = Entry(), {}
        value.size = ctypes.sizeof(value)
        found = kernel.Process32FirstW(handle, ctypes.byref(value))
        while found:
            result[value.pid] = value.parent
            found = kernel.Process32NextW(handle, ctypes.byref(value))
        if ctypes.get_last_error() != 18:  # ERROR_NO_MORE_FILES
            raise ctypes.WinError(ctypes.get_last_error())
        return result
    finally:
        kernel.CloseHandle(handle)


def process_owner(process):
    return process.username().casefold() if os.name == 'nt' else process.uids().real


def kill_tree(identity, sent, protected=(), owner=None):
    """Signal a verified process tree immediately, then let the caller confirm exits."""
    root = verified_process(identity)
    if root is None:
        return [], False
    if root.pid in protected or root.pid == os.getpid():
        raise KitError('Process is part of the emergency caller')
    owner = process_owner(psutil.Process()) if owner is None else owner
    if process_owner(root) != owner:
        raise KitError('Process belongs to another user')
    try:
        children = root.children(recursive=True)
        incomplete = False
    except psutil.NoSuchProcess:
        return [], False
    except psutil.Error:
        children, incomplete = [], True
    targets = []
    # Stop the parent from spawning further work, then signal the captured descendants.
    for process in [root, *reversed(children)]:
        try:
            if (process.pid in protected or process.pid == os.getpid()
                    or process_owner(process) != owner):
                incomplete = True
                continue
            key = (process.pid, process.create_time())
            if key not in sent:
                process.kill()  # psutil rechecks PID reuse on this Process object.
                sent[key] = process
            targets.append(sent[key])
        except psutil.NoSuchProcess:
            continue
        except psutil.Error:
            incomplete = True
    return targets, incomplete


def stop(identity):
    if not matches(identity):
        return False
    parent = psutil.Process(identity['pid'])
    children = []
    for p in parent.children(recursive=True):
        try:
            children.append(fingerprint(p.pid))
        except psutil.Error:
            pass
    for item in reversed(children + [identity]):
        if matches(item):
            try:
                psutil.Process(item['pid']).terminate()
            except psutil.NoSuchProcess:
                pass
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and any(matches(i) for i in children + [identity]):
        time.sleep(.05)
    for item in children + [identity]:
        if matches(item):
            psutil.Process(item['pid']).kill()
    return True
