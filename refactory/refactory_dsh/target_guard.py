"""Watch the target a run works on: say when it moves, refuse writes nobody declared.

The target is the project a design run builds for (`project_for_nodes()`). Today it is
also the checkout other sessions work in and the source the runtime imports, so a run
can find its baseline moved under it (#330 I1), and a worker with full write access can
edit it directly instead of answering through its declared output (#319). Nothing here
isolates the target -- that is a worktree's job (#351). It makes both visible: the
baseline is recorded at `prepare`, later steps warn when the target differs from it,
and an output-only agent node fails when the target changed while it ran.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

#: Where a run keeps its target's baseline: runtime metadata, beside the spine's own.
RECORD = Path(".refactory") / "target.json"

#: Names a walk of a target that is not a git checkout skips, as directories or files:
#: installs, caches and editor state that running anything rewrites, which say nothing
#: about what a worker chose to change. A git checkout is read through its own ignore
#: rules instead.
UNWATCHED = frozenset({".git", ".factory", ".venv", "venv", "node_modules", "__pycache__",
                       ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox", ".idea",
                       ".DS_Store", ".coverage"})

#: The most uncommitted paths a target may have and still be watched. Every one is
#: hashed before and after each output-only node, so a run launched from a home
#: directory or an unignored data tree is reported as unwatched rather than walked.
LIMIT = 20_000

#: Files larger than this are compared by size and modification time, not content.
HASHED_BYTES = 16 * 1024 * 1024

#: How many changed paths one message names before it counts the rest.
SHOWN = 8

#: What starts a warning meant for the person, not the log. The workflow spine passes
#: lines carrying it to the CEO under `ran.warnings`, and leaves a routine
#: `refactory: warning:` (an unverifiable credential, say) in the log, where it was.
WARNING = "refactory: run warning: "


def _git(root: Path, *args: str) -> str | None:
    """What a git command prints in ``root``, or None when git cannot answer there.

    ``--no-optional-locks`` keeps `status` from refreshing the index, so watching the
    target never writes to it.
    """
    try:
        result = subprocess.run(["git", "--no-optional-locks", "-C", str(root), *args],
                                capture_output=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.decode("utf-8", "surrogateescape") if result.returncode == 0 else None


def _digest(path: Path) -> str:
    """What a path holds now: a content hash, or ``-`` when it is gone.

    Content rather than metadata, so a file put back exactly as it was reads as
    unchanged.
    """
    try:
        if path.is_symlink():
            return "symlink:" + os.readlink(path)
        if path.is_dir():
            # git lists an untracked nested checkout as one directory entry.
            return "directory"
        stat = path.stat()
        if stat.st_size > HASHED_BYTES:
            return f"large:{stat.st_size}:{stat.st_mtime_ns}"
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except FileNotFoundError:
        return "-"
    except OSError as error:
        return f"unreadable:{error.errno}"


def _porcelain(listing: str) -> list[str]:
    """The paths `git status --porcelain=v1 -z` names; a rename's old path is skipped.

    A rename or copy is followed by its old path as a field of its own, whether it is
    staged (`R `) or only in the work tree (` R`, after `git add -N`), so both status
    columns are read; reading only the first takes the old path for an entry.
    """
    fields = listing.split("\0")
    paths: list[str] = []
    index = 0
    while index < len(fields):
        entry = fields[index]
        index += 1
        if len(entry) < 4:
            continue
        paths.append(entry[3:])
        if "R" in entry[:2] or "C" in entry[:2]:
            index += 1
    return paths


def state(root: Path, *, exclude: tuple[Path, ...] = ()) -> dict[str, Any]:
    """Where the target stands: its commit, its branch, and what is uncommitted.

    ``changes`` maps each uncommitted path, relative to the target, to what it holds,
    so a further edit to an already-dirty file is still a change. In a git checkout
    that is what `git status` lists; anywhere else it is every file, there being no
    commit to compare with. The target's ``.factory/`` and the ``exclude``d
    directories (the run's own) are never part of it, because a run writes there by
    design. ``changes`` is None when there are more than :data:`LIMIT` paths to watch.
    """
    root = Path(root).resolve()
    skipped = [root / ".factory", *(Path(path).resolve() for path in exclude)]
    found: dict[str, Any] = {"root": str(root), "git": False, "head": None, "branch": None, "tree": None}
    if root in skipped:
        # The run's own directory is the target: everything in it is the run's.
        return {**found, "changes": {}}
    inside = _git(root, "rev-parse", "--is-inside-work-tree")
    # A directory an enclosing checkout ignores is not that checkout's to report on:
    # its status would list nothing there, and the guard would be off without a word.
    if inside is not None and inside.strip() == "true" and _git(root, "check-ignore", "-q", str(root)) is None:
        top = Path((_git(root, "rev-parse", "--show-toplevel") or str(root)).strip()).resolve()
        specs = [f":(exclude,literal){path.relative_to(root)}" for path in skipped if path.is_relative_to(root)]
        listing = _git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--", ".", *specs)
        if listing is None:
            raise ValueError(f"git cannot report the state of the target {root}")
        paths = [os.path.relpath(top / path, root) for path in _porcelain(listing)]
        prefix = "" if root == top else root.relative_to(top).as_posix()
        found.update(git=True, head=(_git(root, "rev-parse", "-q", "--verify", "HEAD") or "").strip() or None,
                     branch=(_git(root, "symbolic-ref", "-q", "--short", "HEAD") or "").strip() or None,
                     # The committed content under the target alone: a commit elsewhere in
                     # the checkout, or a new branch at the same commit, leaves it as it was.
                     tree=(_git(root, "rev-parse", "-q", "--verify", f"HEAD:{prefix}") or "").strip() or None)
    else:
        paths = []
        for directory, names, files in os.walk(root):
            here = Path(directory)
            names[:] = [name for name in names if name not in UNWATCHED and here / name not in skipped]
            paths.extend(str((here / name).relative_to(root)) for name in files if name not in UNWATCHED)
            if len(paths) > LIMIT:
                break
    if len(paths) > LIMIT:
        return {**found, "changes": None}
    return {**found, "changes": {path: _digest(root / path) for path in sorted(paths)}}


def _read(workdir: Path) -> dict[str, Any] | None:
    """The baseline this run recorded, or None when it recorded none."""
    try:
        loaded = json.loads((Path(workdir) / RECORD).read_text())
    except (OSError, ValueError):
        return None
    return loaded if isinstance(loaded, dict) else None


def _commit(sha: str | None) -> str:
    """A commit as a reader recognises it: abbreviated, or said to be absent."""
    return sha[:12] if sha else "no commit"


def _named(paths: list[str]) -> str:
    """A list of paths short enough to read in one message."""
    shown = ", ".join(paths[:SHOWN])
    return shown if len(paths) <= SHOWN else f"{shown} and {len(paths) - SHOWN} more"


def _differ(before: dict[str, str], after: dict[str, str]) -> list[str]:
    """The paths whose content, presence or absence differs between two states."""
    return sorted(path for path in before.keys() | after.keys() if before.get(path) != after.get(path))


def _compare(before: dict[str, Any], after: dict[str, Any], *, commits: bool = True) -> list[str]:
    """What differs between two states of one target, as phrases one message can list.

    With ``commits`` a moved HEAD or branch is a difference of its own. Without, only
    what is in the target is: its files, and its committed content (so a worker that
    commits its edits is still caught), but not a commit elsewhere in the checkout or a
    new branch at the same commit. A target that went over :data:`LIMIT` is itself a
    difference: a flood of new files is exactly what must not pass as no change.
    """
    found = []
    if commits and after["head"] != before.get("head"):
        found.append(f"HEAD moved from {_commit(before.get('head'))} to {_commit(after['head'])}")
    if commits and after["branch"] != before.get("branch"):
        found.append(f"the branch changed from {before.get('branch') or 'a detached HEAD'} "
                     f"to {after['branch'] or 'a detached HEAD'}")
    if not commits and after.get("tree") != before.get("tree"):
        found.append(f"the committed files under it changed (HEAD {_commit(before.get('head'))} "
                     f"is now {_commit(after['head'])})")
    if before.get("changes") is not None and after["changes"] is None:
        found.append(f"more than {LIMIT} paths now differ, too many to compare one by one")
    elif before.get("changes") is not None:
        changed = _differ(before["changes"], after["changes"])
        if changed:
            kind = "uncommitted changes differ" if after["git"] else "files differ"
            found.append(f"{kind} in {_named(changed)}")
    return found


def record(workdir: Path, root: Path, *, output_only: tuple[str, ...] = ()) -> dict[str, Any]:
    """Record the target's baseline for this run, and which nodes may not touch it.

    ``output_only`` names agent nodes whose only output is their declared file: the
    agent-delivery bracket refuses one of them that changed the target while it ran.
    """
    workdir = Path(workdir).resolve()
    baseline = {**state(root, exclude=(workdir,)), "output_only": list(output_only),
                "recorded": datetime.now(timezone.utc).isoformat()}
    path = workdir / RECORD
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(".tmp")
    pending.write_text(json.dumps(baseline, indent=2) + "\n")
    pending.replace(path)
    return baseline


def moved(workdir: Path) -> str | None:
    """How the target differs from this run's baseline, or None when it does not."""
    baseline = _read(workdir)
    if baseline is None:
        return None
    try:
        now = state(Path(baseline["root"]), exclude=(Path(workdir),))
    except (OSError, ValueError) as error:
        # This path only warns, so a target it cannot read is reported, not fatal.
        return f"the target {baseline['root']} could not be compared with this run's baseline: {error}"
    differences = _compare(baseline, now)
    if not differences:
        return None
    return (f"the target {baseline['root']} changed since this run recorded it at prepare: "
            f"{'; '.join(differences)}. Another session or person may be working in this "
            "checkout, so the artifact, its check and its reviews may no longer see the "
            "baseline the run started from.")


def warn(message: str) -> None:
    """Tell the person driving the run, through the step that noticed."""
    print(f"{WARNING}{message}", file=sys.stderr)


def warn_if_moved(workdir: Path) -> str | None:
    """Warn when the target moved, so the step that noticed reports it.

    A warning, not a refusal: the change may be the person's own and intended, and
    only they can say whether the run should go on.
    """
    message = moved(workdir)
    if message is not None:
        warn(message)
    return message


def watch(workdir: Path, node: str) -> dict[str, Any] | None:
    """The target's state before an output-only node runs; None when it is not watched."""
    baseline = _read(workdir)
    if baseline is None or node not in baseline.get("output_only", ()):
        return None
    return state(Path(baseline["root"]), exclude=(Path(workdir),))


def strayed(workdir: Path, before: dict[str, Any] | None) -> list[str]:
    """What changed in the target since :func:`watch` took ``before``."""
    if before is None:
        return []
    return _compare(before, state(Path(before["root"]), exclude=(Path(workdir),)), commits=False)


def refusal(node: str, root: str, changed: list[str], writes: list[str]) -> str:
    """Why an output-only node is refused, and what the person should do about it.

    The target can change while a node runs for reasons that are not the node's (another
    session in the same checkout), and nothing here can tell which, so the message says
    what changed and when, not who changed it.
    """
    outputs = ", ".join(writes) or "none"
    return (f"the target {root} changed while agent {node!r} ran, outside its declared "
            f"outputs ({outputs}): {'; '.join(changed)}. Its whole answer belongs in its "
            "declared output, which a later step turns into the artifact, so a direct change "
            "skips that step, the check and the reviews (#319). Nothing was reverted. If the "
            f"agent made the change, revert it and redo {node}; if another session or person "
            f"did, redo {node} once they are done.")
