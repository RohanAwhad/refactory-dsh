"""Strict identity for generated deliverables, separate from raw/source trees."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import stat

VERSION = "create-fidelity-artifact-inventory/v1"


def strict_artifact_inventory(root: Path) -> dict[str, str]:
    """Hash every regular artifact file, including all generated/cache records.

    Reject symlinks and special files rather than following or omitting them.
    This is an observed inventory, not proof against transient changes between
    observations. Imports/runs should suppress bytecode before freezing files.
    """
    root = Path(root)
    if root.is_symlink():
        raise ValueError("Generated artifact contains a symlink")
    if not root.is_dir():
        raise FileNotFoundError(f"Generated artifact directory is absent: {root}")
    def entries(directory):
        # Path.rglob can silently skip unreadable subdirectories. A complete
        # artifact freeze must fail if any directory cannot be enumerated.
        with os.scandir(directory) as scan:
            children = sorted(scan, key=lambda entry: entry.name)
        for entry in children:
            path = Path(entry.path)
            mode = entry.stat(follow_symlinks=False).st_mode
            yield path, mode
            if stat.S_ISDIR(mode):
                yield from entries(path)
    inventory = {}
    for path, mode in entries(root):
        if stat.S_ISLNK(mode):
            raise ValueError(f"Generated artifact contains a symlink: {path.relative_to(root)}")
        if stat.S_ISDIR(mode):
            continue
        if not stat.S_ISREG(mode):
            raise ValueError(f"Generated artifact contains a nonregular file: {path.relative_to(root)}")
        # Refuse a link substituted between enumeration and open. Directory
        # ancestry is checked by the enclosing artifact containment boundary.
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError(f"Generated artifact file changed type: {path.relative_to(root)}")
            os.set_blocking(stream.fileno(), True)
            inventory[path.relative_to(root).as_posix()] = hashlib.file_digest(stream, "sha256").hexdigest()
    return inventory
