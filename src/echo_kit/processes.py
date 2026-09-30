"""Small OS boundary. Ownership must be verified before termination."""
import os
import subprocess
import time

import psutil


def spawn(command, cwd, env, stdout, stdin=None):
    options = {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {'start_new_session': True}
    return subprocess.Popen(command, cwd=cwd, env=env, stdout=stdout, stderr=subprocess.STDOUT,
                            stdin=stdin if stdin is not None else subprocess.DEVNULL, **options)


def fingerprint(pid):
    p = psutil.Process(pid)
    return {'pid': pid, 'created': p.create_time(), 'exe': p.exe(), 'command': p.cmdline()}


def matches(identity):
    try:
        p = psutil.Process(identity['pid'])
        return (p.status() != psutil.STATUS_ZOMBIE and abs(p.create_time() - identity['created']) < .01
                and p.exe() == identity['exe'] and p.cmdline() == identity['command'])
    except (psutil.Error, KeyError):
        return False


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
