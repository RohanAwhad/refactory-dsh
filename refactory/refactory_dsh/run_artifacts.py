"""Owned output selection shared by redo and fresh graph invocations."""
from __future__ import annotations

from pathlib import Path
from typing import Iterable


def overlaps(left: Path, right: Path) -> bool:
    return left == right or left in right.parents or right in left.parents


def owned_outputs(
    workdir: Path, writes: Iterable[str], reads: Iterable[Path],
) -> tuple[list[Path], list[str]]:
    """Plan reversible moves, preserving inputs and refusing unsafe ownership."""
    from refactory_dsh.nn.executor import ITEMS_OVERRIDE

    targets: list[Path] = []
    retained: list[str] = []
    root = workdir.resolve()

    def canonical(path: Path) -> Path:
        resolved = (workdir / path).resolve()
        return resolved.relative_to(root) if resolved.is_relative_to(root) else resolved

    inputs = [value for path in reads for value in (path, canonical(path))]
    for name in sorted(set(writes), key=lambda value: (len(Path(value).parts), value)):
        relative = Path(name)
        if relative.is_absolute() or '..' in relative.parts or relative == Path('.'):
            raise ValueError(f'cannot reset output {name!r}: it must name an owned path inside the run')
        if relative.parts[0] == '.refactory' or relative in (Path('facts.json'), Path(ITEMS_OVERRIDE)):
            raise ValueError(f'cannot reset runtime-owned output {name!r}')
        resolved = canonical(relative)
        if any(overlaps(candidate, item) for candidate in (relative, resolved) for item in inputs):
            retained.append(name)
            continue
        path = workdir / relative
        if not path.exists() and not path.is_symlink():
            continue
        if path.is_symlink() or not path.resolve().is_relative_to(workdir.resolve()):
            raise ValueError(f'cannot reset output {name!r}: symlink or path outside the run')
        if any(parent in relative.parents for parent in targets):
            continue
        targets.append(relative)
    return targets, retained


def archive_root(workdir: Path, namespace: str) -> Path:
    """Refuse a redirected archive destination before any owned file is moved."""
    root = workdir / '.refactory' / namespace
    for path in (workdir / '.refactory', root):
        if path.is_symlink() or not path.resolve().is_relative_to(workdir.resolve()):
            raise ValueError(f'cannot archive through {str(path)!r}: symlink or path outside the run')
        if path.exists() and not path.is_dir():
            raise ValueError(f'archive directory {str(path)!r} is not a directory')
    return root
