import os
import time
from pathlib import Path

import pytest

from echo_kit import storage_fs as fs


def frozen(root, file):
    return {'path': file.relative_to(root).as_posix(), 'identity': fs.file_identity(file, file.lstat())}


def test_handle_anchored_delete_and_marker(tmp_path):
    root = tmp_path / '输出 空格'
    unit = root / '单元'
    unit.mkdir(parents=True)
    file = unit / 'payload.tar'
    file.write_bytes(b'payload')
    rid = fs.identity(root.stat())
    dirs = {'单元': fs.identity(unit.stat())}
    entry = frozen(root, file)
    fs.write_metadata(root, '单元/.echo-reclamation.json', rid, dirs, {'state': 'in_progress'})
    assert fs.remove(root, entry, rid, dirs) == ('deleted', None)
    assert not file.exists()
    assert (unit / '.echo-reclamation.json').is_file()


def test_change_and_hardlink_are_not_deleted(tmp_path):
    file = tmp_path / 'payload'
    file.write_text('first')
    entry = frozen(tmp_path, file)
    file.write_text('changed')
    with pytest.raises(fs.UnsafeStorage):
        fs.remove(tmp_path, entry, fs.identity(tmp_path.stat()), {})
    alias = tmp_path / 'alias'
    os.link(file, alias)
    with pytest.raises(fs.UnsafeStorage):
        fs.remove(tmp_path, frozen(tmp_path, file), fs.identity(tmp_path.stat()), {})
    assert alias.read_text() == 'changed'


def test_replaced_parent_and_symlink_never_followed(tmp_path):
    root = tmp_path / 'cache'
    root.mkdir()
    sub = root / 'unit'
    sub.mkdir()
    file = sub / 'file'
    file.write_text('original')
    rid, dirs, entry = fs.identity(root.stat()), {'unit': fs.identity(sub.stat())}, frozen(root, file)
    sub.rename(root / 'old')
    sub.mkdir()
    (sub / 'file').write_text('new directory')
    with pytest.raises(fs.UnsafeStorage):
        fs.remove(root, entry, rid, dirs)
    assert (sub / 'file').read_text() == 'new directory'


def test_nonempty_directory_cannot_remove_new_file(tmp_path):
    unit = tmp_path / 'unit'
    unit.mkdir()
    entry = frozen(tmp_path, unit)
    (unit / 'unplanned').write_text('retain')
    with pytest.raises(OSError):
        fs.remove(tmp_path, entry, fs.identity(tmp_path.stat()), {})
    assert (unit / 'unplanned').read_text() == 'retain'


def test_restored_mtime_does_not_hide_modification(tmp_path):
    file = tmp_path / 'payload'
    file.write_bytes(b'first')
    entry = frozen(tmp_path, file)
    # File clocks may have coarser resolution than rapid CI writes. Observe a
    # later filesystem tick instead of assuming a fixed sleep is sufficient.
    key = 'change_time' if os.name == 'nt' else 'ctime'
    probe = tmp_path / 'clock-probe'
    deadline = time.monotonic() + 5
    while True:
        probe.touch()
        if fs.file_identity(probe, probe.lstat())[key] > entry['identity'][key]:
            break
        if time.monotonic() >= deadline:
            pytest.fail('Filesystem change clock did not advance')
        time.sleep(.001)
    file.write_bytes(b'other')
    os.utime(file, ns=(entry['identity']['mtime'], entry['identity']['mtime']))
    assert fs.file_identity(file, file.lstat())[key] > entry['identity'][key]
    with pytest.raises(fs.UnsafeStorage):
        fs.remove(tmp_path, entry, fs.identity(tmp_path.stat()), {})
    assert file.read_bytes() == b'other'
