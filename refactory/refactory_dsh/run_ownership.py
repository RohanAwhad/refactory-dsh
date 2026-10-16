"""Exclusive local ownership of a graph run's mutable directory.

The persistent file is only a kernel-lock rendezvous, not a PID lease. Closing the
last owning descriptor releases it even after a crash; never unlink the file or
explicitly unlock an inherited descriptor while an owned DSH child may still run.
"""
from __future__ import annotations

import os
import stat
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Iterator

LOCK = Path('.refactory/invocation.lock')
# Re-entry is confined to the same process/thread and call context. It is not an
# environment escape hatch that a second CLI invocation could inherit.
_HELD: ContextVar[tuple[tuple[Path, int, int, int], ...]] = ContextVar('run_ownership', default=())


@contextmanager
def ownership(workdir: Path) -> Iterator[int]:
    """Own preparation/execution, or refuse before changing an active invocation.

    The yielded descriptor may be passed to the owned DSH child on POSIX. A
    nested prepare in this same call shares it; another caller must acquire its
    own lock. Leftover lock files are harmless and are deliberately retained.
    """
    root = Path(workdir).expanduser().resolve()
    process, thread = os.getpid(), threading.get_ident()
    for path, fd, pid, tid in _HELD.get():
        if path == root and (pid, tid) == (process, thread):
            yield fd
            return
    runtime = root / '.refactory'
    if runtime.is_symlink() or not runtime.resolve().is_relative_to(root):
        raise ValueError('cannot own a run with .refactory outside its work directory')
    runtime.mkdir(parents=True, exist_ok=True)
    lock = root / LOCK
    if lock.is_symlink():
        raise ValueError('cannot own a run through a symlinked invocation lock')
    fd = os.open(lock, os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError('the invocation lock must be a regular file')
        try:
            if os.name == 'nt':
                import msvcrt

                if os.fstat(fd).st_size == 0:
                    os.write(fd, b'\0')
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, PermissionError) as error:
            raise ValueError(f'an active invocation owns {root}; wait for it to finish or choose another --workdir') from error
        token = _HELD.set((*_HELD.get(), (root, fd, process, thread)))
        try:
            yield fd
        finally:
            _HELD.reset(token)
    finally:
        # LOCK_UN would also unlock the child's duplicate. Close only our copy.
        os.close(fd)


def held(workdir: Path) -> bool:
    """Whether some invocation owns ``workdir`` now, asked without keeping its lock.

    An advisory lock can only be asked about by trying it, so this takes the lock for an
    instant; an invocation that starts in that instant is refused and can be retried.
    """
    lock = Path(workdir).expanduser() / LOCK
    if lock.is_symlink() or not lock.is_file():
        return False
    fd = os.open(lock, os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0))
    try:
        try:
            if os.name == 'nt':
                import msvcrt

                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, PermissionError):
            return True
        return False
    finally:
        os.close(fd)
