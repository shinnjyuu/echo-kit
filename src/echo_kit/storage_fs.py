"""No-follow file identities and handle-anchored reclamation; never rmtree a root."""
import ctypes
import json
import os
import stat
import sys
import uuid
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path

from .core import KitError


class UnsafeStorage(KitError):
    pass


def linked(value):
    return stat.S_ISLNK(value.st_mode) or bool(getattr(value, 'st_file_attributes', 0) & 0x400)


def identity(value):
    return {'dev': value.st_dev, 'ino': value.st_ino, 'mode': stat.S_IFMT(value.st_mode),
            'size': value.st_size, 'mtime': value.st_mtime_ns,
            # Python 3.12 Windows lstat exposes legacy ctime as creation time,
            # while fstat may expose change time. Birth time is consistent.
            'ctime': getattr(value, 'st_birthtime_ns', value.st_ctime_ns) if os.name == 'nt' else value.st_ctime_ns,
            'links': value.st_nlink, 'attributes': getattr(value, 'st_file_attributes', 0)}


def same(value, expected, *, content=False, renamed=False):
    actual = identity(value)
    keys = ('dev', 'ino', 'mode', 'attributes')
    if content:
        keys += ('size', 'mtime', 'links') + (() if renamed else ('ctime',))
    return bool(actual['ino']) and not linked(value) and all(actual[k] == expected[k] for k in keys)


def plain_chain(path):
    """Validate existing ancestors without resolving away reparse points."""
    path = Path(os.path.abspath(path))
    for item in [*reversed(path.parents), path]:
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if linked(info):
            raise UnsafeStorage('linked_storage_path')
    return path


@lru_cache(maxsize=1)
def _windows_api():
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                  ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.SetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.SetFileInformationByHandle.restype = wintypes.BOOL
    kernel.GetFileInformationByHandleEx.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.GetFileInformationByHandleEx.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    return kernel


def _win_open(path, delete=False, append=False):
    import msvcrt
    kernel = _windows_api()
    # OPEN_REPARSE_POINT + BACKUP_SEMANTICS opens the link itself, including dirs.
    # Deny directory replacement/reparse mutation while using full child paths.
    handle = kernel.CreateFileW(str(path), 0x80 | (0x10000 if delete else 0) | (4 if append else 0), 1,
                                None, 3, 0x00200000 | 0x02000000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return msvcrt.open_osfhandle(handle, (os.O_WRONLY | os.O_APPEND if append else os.O_RDONLY) | os.O_BINARY)
    except BaseException:
        kernel.CloseHandle(handle)
        raise


def _win_change_time(fd):
    import msvcrt
    class BasicInfo(ctypes.Structure):
        _fields_ = [('creation', ctypes.c_longlong), ('access', ctypes.c_longlong),
                    ('write', ctypes.c_longlong), ('change', ctypes.c_longlong), ('attributes', ctypes.c_ulong)]
    value = BasicInfo()
    if not _windows_api().GetFileInformationByHandleEx(msvcrt.get_osfhandle(fd), 0,
                                                      ctypes.byref(value), ctypes.sizeof(value)):
        raise ctypes.WinError(ctypes.get_last_error())
    return value.change


def file_identity(path, info):
    result = identity(info)
    if os.name == 'nt' and stat.S_ISREG(info.st_mode):
        fd = _win_open(path)
        try:
            if not same(os.fstat(fd), result, content=True):
                raise UnsafeStorage('storage_file_changed_during_scan')
            # Include the NTFS change clock, independent of creation time and
            # user-restorable mtime. No file body is opened/read.
            result['change_time'] = _win_change_time(fd)
        finally:
            os.close(fd)
    return result


def _same_handle(fd, expected, content=True):
    return same(os.fstat(fd), expected, content=content) and (os.name != 'nt' or not content
            or 'change_time' not in expected or _win_change_time(fd) == expected['change_time'])


def read_metadata(root, relative, root_identity, directories, limit=2 * 1024 * 1024):
    with parent_handle(root, relative, root_identity, directories) as (parent, target):
        try:
            if os.name == 'nt':
                # Parents stay pinned; O_NOINHERIT/O_RDONLY opens only the small
                # metadata leaf after a no-follow identity check.
                before = target.lstat()
                if linked(before):
                    raise UnsafeStorage('linked_storage_metadata')
                fd = os.open(target, os.O_RDONLY | os.O_BINARY)
            else:
                before = os.stat(target.name, dir_fd=parent, follow_symlinks=False)
                fd = os.open(target.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
        except FileNotFoundError:
            return {}
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not same(info, identity(before), content=True) or not stat.S_ISREG(info.st_mode) or info.st_size > limit:
                raise UnsafeStorage('unreadable_storage_metadata')
            value = json.loads(stream.read(limit + 1).decode('utf-8'))
            if not same(os.fstat(stream.fileno()), identity(info), content=True) or not isinstance(value, dict):
                raise UnsafeStorage('changed_storage_metadata')
            return value


@contextmanager
def parent_handle(root, relative, root_identity, directories):
    """Pin every parent; POSIX uses dir_fd/O_NOFOLLOW, Windows no-share handles."""
    root = Path(os.path.abspath(root))
    relative = Path(relative)
    if relative.is_absolute() or not relative.parts or any(p in ('.', '..') for p in relative.parts):
        raise UnsafeStorage('invalid_storage_relative_path')
    target = root / relative
    parents = [*reversed(target.parent.parents), target.parent]
    handles = []
    previous = None
    try:
        for path in parents:
            if os.name == 'nt':
                fd = _win_open(path)
            elif sys.platform.startswith('linux'):
                fd = os.open(str(path) if previous is None else path.name,
                             os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                             **({} if previous is None else {'dir_fd': previous}))
            else:
                raise UnsafeStorage('safe_storage_deletion_unsupported')
            handles.append(fd)
            previous = fd
            info = os.fstat(fd)
            if linked(info) or not stat.S_ISDIR(info.st_mode):
                raise UnsafeStorage('storage_parent_is_not_plain_directory')
            if path == root:
                expected = root_identity
            elif path.is_relative_to(root):
                expected = directories.get(path.relative_to(root).as_posix())
                if expected is None:
                    raise UnsafeStorage('storage_parent_not_in_plan')
            else:
                expected = None
            if expected and not same(info, expected):
                raise UnsafeStorage('storage_directory_identity_changed')
        yield previous, target
    finally:
        for fd in reversed(handles):
            os.close(fd)


def _restore(parent, staged, original):
    """Never overwrite a concurrently-created original name during recovery."""
    libc = ctypes.CDLL(None, use_errno=True)
    rename = getattr(libc, 'renameat2', None)
    if rename is None:
        return False
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    return rename(parent, os.fsencode(staged), parent, os.fsencode(original), 1) == 0  # RENAME_NOREPLACE


def remove(root, entry, root_identity, directories):
    """Delete one frozen object; return retained staging name on an unconfirmed race."""
    relative, expected = entry['path'], entry['identity']
    directory = expected['mode'] == stat.S_IFDIR
    with parent_handle(root, relative, root_identity, directories) as (parent, target):
        if os.name == 'nt':
            import msvcrt
            try:
                fd = _win_open(target, delete=True)
            except FileNotFoundError:
                return 'already_missing', None
            try:
                info = os.fstat(fd)
                if not _same_handle(fd, expected, content=not directory) or (not directory and info.st_nlink != 1):
                    raise UnsafeStorage('storage_file_changed')
                value = ctypes.c_int(1)  # FILE_DISPOSITION_INFO.DeleteFile = TRUE
                handle = msvcrt.get_osfhandle(fd)
                if not _windows_api().SetFileInformationByHandle(handle, 4, ctypes.byref(value), ctypes.sizeof(value)):
                    raise ctypes.WinError(ctypes.get_last_error())
            finally:
                os.close(fd)
            try:
                target.lstat()
            except FileNotFoundError:
                return 'deleted', None
            except PermissionError:
                return 'unconfirmed', None  # Delete pending; do not invent reclaimed bytes.
            return 'unconfirmed', None
        name = target.name
        try:
            info = os.stat(name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            return 'already_missing', None
        if not same(info, expected, content=not directory) or (not directory and info.st_nlink != 1):
            raise UnsafeStorage('storage_file_changed')
        staged = '.echo-kit-reclaim-' + uuid.uuid4().hex
        os.rename(name, staged, src_dir_fd=parent, dst_dir_fd=parent)
        try:
            moved = os.stat(staged, dir_fd=parent, follow_symlinks=False)
            if not same(moved, expected, content=not directory, renamed=True):
                raise UnsafeStorage('storage_file_changed_during_delete')
            if directory:
                os.rmdir(staged, dir_fd=parent)
            else:
                os.unlink(staged, dir_fd=parent)
            return 'deleted', None
        except BaseException:
            if _restore(parent, staged, name):
                raise
            # Unknown or nonempty content is retained and explicitly reported.
            return 'unconfirmed', str(Path(relative).with_name(staged))


def write_metadata(root, relative, root_identity, directories, value):
    """Atomic small JSON marker write under pinned, verified parents."""
    with parent_handle(root, relative, root_identity, directories) as (parent, target):
        temporary = '.echo-kit-marker-' + uuid.uuid4().hex
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, 'O_NOFOLLOW', 0)
        if os.name == 'nt':
            fd = os.open(target.with_name(temporary), flags, 0o600)
        else:
            fd = os.open(temporary, flags, 0o600, dir_fd=parent)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(json.dumps(value, ensure_ascii=False).encode('utf-8'))
                stream.flush()
            if os.name == 'nt':
                os.replace(target.with_name(temporary), target)
            else:
                os.replace(temporary, target.name, src_dir_fd=parent, dst_dir_fd=parent)
        finally:
            try:
                if os.name == 'nt':
                    os.unlink(target.with_name(temporary))
                else:
                    os.unlink(temporary, dir_fd=parent)
            except FileNotFoundError:
                pass


def child_names(root, relative, root_identity, directories):
    """Enumerate an anchored plain directory; never traverse a swapped junction."""
    with parent_handle(root, Path(relative) / '.echo-kit-list-marker', root_identity, directories) as (parent, target):
        with os.scandir(target.parent if os.name == 'nt' else parent) as entries:
            for entry in entries:
                yield entry.name


def annotate_report(root, entry, root_identity, directories, partial=False):
    """Append a small truthful notice to a retained report without reading it."""
    with parent_handle(root, entry['path'], root_identity, directories) as (parent, target):
        fd = (_win_open(target, append=True) if os.name == 'nt' else
              os.open(target.name, os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW, dir_fd=parent))
        with os.fdopen(fd, 'ab') as stream:
            if not _same_handle(stream.fileno(), entry['identity']):
                raise UnsafeStorage('retained_report_changed')
            notice = ('Some attachments are missing / 部分附件缺失' if partial else
                      'Attachments reclaimed / 附件已回收')
            stream.write(('<p data-echo-reclamation="true">' + notice +
                          '. Original verdict unchanged. / 原始结论不变。</p>').encode('utf-8'))
