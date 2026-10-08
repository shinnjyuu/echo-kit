"""One-time removal of the 0.2/0.3 project version entry. No runtime task logic."""
import os
import subprocess
import uuid
from importlib.resources import files

from filelock import FileLock

from .core import KitError, read
from .documentation import PACKAGE, digest
from .operations import Journal, canonical_path

# Identical released launcher in 0.2.0 and 0.3.0, normalized for Git CRLF.
LEGACY_DIGEST = '30c9c26e4c71fd4d218a8f8c82010a29b7faa67a3b4585c031a320a21be4cdde'


def setup(ws):
    launcher, lockfile = ws.root / 'echo-kit.py', ws.root / 'echo-kit.lock.json'
    template = files('echo_kit').joinpath('resources/launcher.py').read_bytes()
    if not launcher.exists() and not lockfile.exists():
        return {'status': 'passed', 'changed': False, 'next_step': 'Use echo-kit directly. No project entry is needed.'}
    if launcher.is_symlink() or lockfile.is_symlink() or not ws.state.resolve().is_relative_to(ws.root):
        raise KitError('Preserve linked entry/state paths and migrate manually')
    ws.state.mkdir(parents=True, exist_ok=True)
    with FileLock(str(ws.state / 'entry-migration.lock'), timeout=1):
        original = launcher.read_bytes() if launcher.is_file() else b''
        if digest(original) == digest(template) and not lockfile.exists():
            return {'status': 'passed', 'changed': False, 'next_step': 'Use echo-kit directly or the migrated entry.'}
        selection = read(lockfile, {})
        if (digest(original) != LEGACY_DIGEST or not isinstance(selection, dict)
                or selection.get('schema_version') != 1 or selection.get('package') != PACKAGE
                or selection.get('launcher_digest') != LEGACY_DIGEST):
            raise KitError('Project-owned or unrecognized entry: preserve files and manually replace old calls with echo-kit')
        pending = [r['id'] for r in Journal().list() if canonical_path(r['workspace']) == canonical_path(ws.root)
                   and (r.get('held') or r.get('state') in ('running', 'needs_cleanup'))]
        active = [p.parent.name for p in ws.runs.glob('*/active.json')]
        if pending or active:
            error = KitError('Finish actual operations and clean up unfinished runs before entry migration')
            error.details = {'operations': pending, 'runs': active}
            raise error
        lock_bytes = lockfile.read_bytes()
        ignore = ws.root / '.gitignore'
        if ignore.is_symlink():
            raise KitError('Preserve symlinked .gitignore and migrate manually')
        body = ignore.read_text(encoding='utf-8-sig') if ignore.exists() else ''
        if '/.echo-kit/' not in body.splitlines():
            ignore.write_text(body.rstrip() + '\n/.echo-kit/\n', encoding='utf-8')
        backup = ws.state / 'migrations' / uuid.uuid4().hex
        if not backup.resolve().is_relative_to(ws.root):
            raise KitError('Migration backup must stay inside ignored local state')
        try:
            repo = subprocess.run(['git', '-C', str(ws.root), 'rev-parse', '--show-toplevel'], capture_output=True)
            if repo.returncode == 0:
                ignored = subprocess.run(['git', '-C', str(ws.root), 'check-ignore', '-q', '--', str(backup / 'echo-kit.py')],
                                         capture_output=True)
                if ignored.returncode:
                    raise KitError('Backup directory is not ignored; fix ignore rules before migrating')
        except FileNotFoundError:
            pass
        backup.mkdir(parents=True)
        (backup / launcher.name).write_bytes(original)
        (backup / lockfile.name).write_bytes(lock_bytes)
        if launcher.read_bytes() != original or lockfile.read_bytes() != lock_bytes:
            raise KitError('Project entry changed concurrently; preserved backup, retry after review')
        temporary = launcher.with_name('.echo-kit-entry-' + uuid.uuid4().hex + '.tmp')
        try:
            temporary.write_bytes(template)
            os.replace(temporary, launcher)
            lockfile.unlink()
        finally:
            temporary.unlink(missing_ok=True)
    return {'status': 'passed', 'changed': True, 'backup': str(backup),
            'next_step': 'Use installed Echo Kit >= 0.4.0. Historical task records are preserved and no longer required.'}
