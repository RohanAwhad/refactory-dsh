"""The operations design-v2's graph calls.

`create` emits a **package of modes**: a manifest, a `Mode` subclass, `score()`.
`design` emits **software**: no manifest, no `score()`, no graph. So the validator
and the check differ, and so does where the output lands.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from typing import Any

from refactory_dsh import delivery, projects, sandbox, target_guard, worktree
from refactory_dsh.paths import project_for_nodes
from refactory_dsh.worktree import AttemptRefused, _alive, _json

STAGING = "staging"

#: Project-owned software artifacts are separate from discoverable mode packages.
ARTIFACTS = Path(".factory") / "artifacts"

#: Which run owns each artifact name. Kept beside the artifacts rather than in them,
#: because materialise replaces an artifact directory whole.
OWNERS = Path(".factory") / "artifact-owners"

#: Spine states in which a run no longer writes its artifact. A failed run is among
#: them so a retry under the same name can start; if the failed run resumes, it is
#: refused before it writes or checks the artifact. `refused` is not: the spine writes
#: it when a gate question could not be put, and the gate is still due.
FINISHED = frozenset({"done", "halted", "failed"})

#: The agent node whose whole answer is `emit.txt`. Materialise writes the artifact
#: from it, so a change it makes to the target directly bypasses materialise, the
#: check and the reviews (#319); the agent-delivery bracket refuses that.
OUTPUT_ONLY = ("emit",)

#: The blocks `emit.txt` is written in: a whole file, edits to an existing one, or its
#: removal. Edits are what keep a small change to a long file small (#330 I4).
BLOCK = re.compile(r"^===(FILE|EDIT|DELETE):\s*([A-Za-z0-9_./-]+)\s*===\s*$", re.MULTILINE)

#: The lines that frame one edit in an `===EDIT:` block: text copied from the file, and
#: what replaces it.
SEARCH, DIVIDER, REPLACE = "<<<<<<< SEARCH", "=======", ">>>>>>> REPLACE"

#: What the read tool leaves where it cut text short. Copied into a file, it cuts the
#: file short too: one such file passed every gate because nothing ran it (#330 I4).
TRUNCATED = re.compile(r"\.\.\. \(line truncated to \d+ chars\)$"
                       r"|^\(Output capped\. Showing lines \d+-\d+\. Use offset=\d+ to continue\.\)$"
                       r"|^\(Showing lines \d+-\d+ of \d+\. Use offset=\d+ to continue\.\)$",
                       re.MULTILINE)

#: One emission's edits: for each file, the lines to find and the lines put in their place.
Edits = dict[Path, list[tuple[str, str]]]

#: Which emission the run's last attempt applied, on top of which commit and to which
#: commit's files, so the same emission given again applies to those files rather than
#: to its own result.
EMISSION = Path(".refactory") / "emission.json"

#: What a run's facts say in place of a worktree, so a prompt that names one never shows
#: a raw placeholder when the run has none.
NO_WORKTREE = "(this run has no worktree: `worktree` is off)"


def _artifact_name(name: str) -> str:
    if (not name or name in {".", ".."} or name != name.strip()
            or any(char in name for char in "/\\:")
            or any(ord(char) < 32 for char in name)):
        raise ValueError("artifact name must be one directory name, without separators, drives or '..'")
    return name


def _project_dir(project: Path, *parts: Path) -> None:
    """Refuse a project path that crosses a symlink or is not a directory."""
    for path in parts:
        if path.is_symlink() or not path.resolve().is_relative_to(project):
            raise ValueError(f"artifact path must not cross a symlink: {path}")
        if path.exists() and not path.is_dir():
            raise ValueError(f"artifact path is not a directory: {path}")


def _artifact_root(name: str) -> Path:
    """Use the configured project, never the lowered operation's working directory."""
    project = project_for_nodes().expanduser().resolve()
    root = project / ARTIFACTS / _artifact_name(name)
    _project_dir(project, project / ".factory", project / ARTIFACTS, root)
    return root


def _still_open(run: Path, claimant: Any = None) -> str | None:
    """What ``run`` is doing while it may still write its artifact; None once it cannot.

    A run that reported (``design.json``) or whose directory is gone is over. A run the
    spine drives says so in its status, unless the process the spine checkpoint names
    as its owner has exited (a terminal CEO that finished, a GUI that was restarted). A
    walked run is over once its progress records an error, or once ``claimant``, the
    walking process that claimed the name, has exited (Ctrl-C records no error). A run
    that left neither record never got far enough to matter.
    """
    if not run.is_dir() or (run / "design.json").exists():
        return None
    spine = _json(run / ".refactory" / "spine-status.json")
    if spine:
        status = str(spine.get("status") or "")
        owner = _json(run / ".refactory" / "spine-checkpoint.json").get("owner_pid")
        if status in FINISHED or not _alive(owner):
            return None
        return f"status {status!r} since {spine.get('at')}"
    progress = _json(run / ".refactory" / "progress.json")
    if progress and not progress.get("error") and _alive(claimant):
        return f"walking, last at {progress.get('node')!r}"
    return None


def _claim(name: str, run: Path) -> None:
    """Own the artifact ``name`` for ``run``, or refuse while another open run owns it.

    Two runs with one name share `.factory/artifacts/<name>`, so one run's materialise
    rewrote the other's artifact mid-review (#330 K4). Claimed at prepare, and again at
    materialise: a run that resumes after another took its name must find out before it
    overwrites that run's work.
    """
    project = project_for_nodes().expanduser().resolve()
    _project_dir(project, project / ".factory", project / OWNERS)
    path = project / OWNERS / f"{_artifact_name(name)}.json"
    run = Path(run).resolve()
    claim = _json(path)
    owner = str(claim.get("run") or "")
    if owner == str(run) and claim.get("pid") == os.getpid():
        return
    if owner and owner != str(run):
        busy = _still_open(Path(owner), claim.get("pid"))
        if busy is not None:
            raise ValueError(
                f"artifact {name!r} belongs to another open run, {owner} ({busy}): two runs "
                f"with one name overwrite each other's {ARTIFACTS / name}. Choose another name, "
                f"or finish or halt that run first; if it was abandoned, delete {path}."
            )
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_name(f".{path.name}.{run.name}.tmp")
    # The claiming process is the walker itself on the walker, and a short-lived node
    # command on the spine, which `_still_open` reads from its checkpoint instead.
    pending.write_text(json.dumps({"run": str(run), "pid": os.getpid(),
                                   "claimed": datetime.now(timezone.utc).isoformat()}) + "\n")
    pending.replace(path)


def _require_owner(name: str, run: Path) -> None:
    """Refuse when another run has claimed ``name`` since this one wrote the artifact.

    Stricter than :func:`_claim`, which lets a run take a name over from a finished one:
    a step that checks or reports the artifact must not pass off a finished run's files
    as this run's own. A name nobody has claimed is left alone.
    """
    project = project_for_nodes().expanduser().resolve()
    path = project / OWNERS / f"{_artifact_name(name)}.json"
    owner = str(_json(path).get("run") or "")
    if owner and owner != str(Path(run).resolve()):
        raise ValueError(
            f"artifact {name!r} was claimed by another run, {owner}, after this run wrote it, "
            f"so {ARTIFACTS / name} may hold that run's files. Redo emit or materialise to "
            "write this run's artifact again before checking or reporting it."
        )


def _artifact_files(files: dict[str, str]) -> dict[Path, str]:
    return dict(zip(_artifact_paths(list(files)), files.values()))


def _artifact_paths(names: list[str]) -> list[Path]:
    """The emitted paths, refused unless each names a distinct file inside the artifact."""
    checked: list[Path] = []
    for relative in names:
        path = Path(relative)
        if (path.is_absolute() or PureWindowsPath(relative).drive
                or "\\" in relative or ".." in path.parts or not path.parts
                or relative.endswith("/") or any(ord(char) < 32 for char in relative)):
            raise AttemptRefused(f"{relative!r} is not a file inside the artifact directory; name it by its "
                                 "path from the artifact's root")
        if path in checked:
            raise AttemptRefused(f"{relative!r} duplicates an artifact file; write it once")
        checked.append(path)
    for path in checked:
        if any(parent in checked for parent in path.parents):
            raise AttemptRefused(f"{str(path)!r} conflicts with an artifact file used as its directory; "
                                 "rename one of them")
    return checked


def _run_id(workdir: Path) -> str:
    """This invocation's id, so the run's branch names the run; random for a walked run."""
    found = str(_json(Path(workdir) / worktree.INVOCATION).get("id") or "")
    return found[:12] if re.fullmatch(r"[0-9a-f]{12,}", found) else uuid.uuid4().hex[:12]


def _open_worktree(ctx: Any, name: str, task: dict[str, Any]) -> dict[str, Any]:
    """Give the run its own worktree of its target, and say what that means (#351).

    The target is the repository ``target`` names -- relative to the project the run
    belongs to, not to the run directory a node starts in -- or a new project in the
    projects folder when it names none. The run branches from ``base`` -- the target's
    HEAD, or a new project's ``main`` -- pinned to a commit here, and never works in the
    target itself: what the person has there, committed or not, stays theirs.
    """
    launcher = project_for_nodes().expanduser().resolve()
    named = str(task.get("target") or ctx.knob("target", "") or "").strip()
    given = str(task.get("base") or ctx.knob("base", "") or "").strip()
    if not named and given and not (projects.projects_dir() / name).exists():
        raise ValueError(f"base {given!r} names a commit, but this run starts a new project, which has only "
                         "its empty main; pass target=<repository> with it, or leave base empty")
    target = worktree.repository_root(launcher / Path(named).expanduser()) if named else projects.new_project(name)
    rev = given or ("HEAD" if named else "main")
    # Pinned before anything else is touched, so a base that names nothing is refused
    # before an earlier run's worktree is pruned or this one's is made. A redo keeps
    # the base it pinned the first time, even if that branch has moved or gone since.
    run_id = _run_id(ctx.workdir)
    earlier = worktree.recorded(ctx.workdir)
    if earlier is not None and earlier.branch == worktree.BRANCH_PREFIX + run_id:
        base = earlier.base
    else:
        base = worktree.resolve_base(target, rev)
    for note in worktree.prune(target):
        # A worktree kept is one the person may want to look at; one removed is routine.
        if note.startswith("kept "):
            target_guard.warn(note)
        else:
            print(f"refactory: {note}", file=sys.stderr)
    found = worktree.create(target, base, run_id, ctx.workdir)
    # The checkouts a worker could still write to directly are watched while `emit`
    # runs, which may change none of them: the target, the run's own worktree, and the
    # checkout the run starts from, whose run directories hold the workers (#319).
    watched = [Path(found.path)]
    if launcher != target and (launcher / ".git").exists():
        watched.append(launcher)
    baseline = target_guard.record(ctx.workdir, target, output_only=OUTPUT_ONLY, also=tuple(watched))
    if baseline["changes"]:
        target_guard.warn(f"{len(baseline['changes'])} uncommitted change(s) in {target} are not part of "
                          f"this run, which starts from commit {found.base[:12]}: "
                          f"{target_guard._named(sorted(baseline['changes']))}")
    elif baseline["changes"] is None:
        target_guard.warn(f"the target {target} has more than {target_guard.LIMIT} uncommitted paths, "
                          "so this run cannot tell whether emit writes to it directly")
    if not named:
        entry = next((project for project in projects.load()["projects"]
                      if project.get("path") == str(target)), {})
        others = [note["branch"] for note in entry.get("runs", []) if note.get("branch") != found.branch]
        if others and not given:
            target_guard.warn(f"{target} has earlier runs, whose work is on their branches, not on main: "
                              f"{target_guard._named(others)}. This run starts from main; pass "
                              "base=<branch> to build on one of them instead.")
    projects.record_run(target, run=ctx.workdir, branch=found.branch, base=found.base, status="running")
    return {"target": str(target), "worktree": found.path, "branch": found.branch, "base": found.base,
            "source": found.path, "check_root": f"the root of the run's worktree, {found.path}",
            "emit_scope": (
                f"Paths are relative to the root of this run's worktree, {found.path}: a git worktree of "
                f"{target} on branch {found.branch}, from the base commit {found.base[:12]}. Each attempt "
                "builds on the last: the worktree holds the base and every earlier attempt, as committed "
                f"(`git -C {found.path} diff {found.base[:12]}` shows the change so far), so read the files "
                "there and change only what this attempt needs. Write one block per file, introduced by "
                "a line of exactly one of:\n"
                "===FILE: <relative path>=== followed by the whole file, for a new file or one you "
                "rewrite entirely;\n"
                "===EDIT: <relative path>=== followed by one or more edits to an existing file, each "
                f"written as\n{SEARCH}\n<lines copied exactly from the file as it is now>\n{DIVIDER}\n"
                f"<the lines that replace them>\n{REPLACE}\nwhere the copied lines appear in the file "
                "exactly once;\n"
                "===DELETE: <relative path>=== on its own, to remove a file.\n"
                "Prefer ===EDIT: for a file that exists: retyping a long file risks changing lines you "
                "did not mean to. Never copy the read tool's line numbers, or a note such as "
                "'(line truncated to 2000 chars)', into a block."
            )}


def prepare_design(ctx: Any, *, worktrees: bool = True) -> dict[str, Any]:
    """Read the brief, and what the brief says counts as done.

    A design run works in its own worktree unless its ``worktree`` knob is off
    (`_open_worktree`), and its delivery is planned here, for the approval gate to show
    (`delivery.plan_run`). ``worktrees`` is False for design_fast, which keeps its own
    copy-and-overlay of ``source_project`` until it moves onto worktrees too.
    """
    task = dict(getattr(ctx, "task", {}) or {})
    # The walker passes these in the run's task; a run on DSH carries them as knobs.
    name = str(task.get("name") or ctx.knob("name", "") or "").strip()
    brief = str(task.get("brief") or ctx.knob("brief", "") or "").strip()
    check = str(task.get("check") or ctx.knob("check", "") or "").strip()
    if not name and brief:
        from refactory_dsh.create_ops import _slug

        name = _slug(brief)
    if not name:
        raise ValueError(
            "design-mode needs what to build: pass {'name': ..., 'brief': ..., 'check': ...} "
            "in the task (the walker) or the `name`/`brief`/`check` knobs (a run on DSH)"
        )
    _artifact_name(name)
    if not check:
        raise ValueError(
            "design-mode needs a check: the command that decides whether the artifact "
            "works, e.g. {'check': 'python -m pytest -q'}"
        )
    located: dict[str, Any] = {}
    if worktrees and ctx.knob("worktree", True) not in (False, 0, "0", "false"):
        # A refinement starts from its pull request's head, in a clone of its repository.
        task = delivery.refine_task(ctx, task)
        located = _open_worktree(ctx, name, task)
    else:
        given = [knob for knob in ("target", "base", "setup", "refine")
                 if str(task.get(knob) or ctx.knob(knob, "") or "").strip()]
        if given:
            raise ValueError(
                f"{', '.join(given)} need the run's worktree, which is off; turn it on, or leave them empty "
                "to write the artifact under .factory/artifacts" if worktrees else
                f"design_fast does not take {', '.join(given)}: it works on a copy of source_project")
        # A run without a worktree must not inherit one an earlier run left in this
        # directory; that worktree's own record (`worktree.slot`) still tracks it.
        (Path(ctx.workdir) / worktree.RECORD).unlink(missing_ok=True)
        _claim(name, ctx.workdir)
        # The baseline every later step compares with (#330 I1), and the bracket that
        # holds `emit` to its declared output (#319).
        project = project_for_nodes().expanduser()
        baseline = target_guard.record(ctx.workdir, project, output_only=OUTPUT_ONLY)
        if baseline["changes"] is None:
            target_guard.warn(f"the target {baseline['root']} has more than {target_guard.LIMIT} "
                              "files to watch, so this run cannot tell whether it moves or "
                              "whether emit writes to it directly")
        if worktrees:
            located = {"target": str(project), "worktree": NO_WORKTREE, "branch": NO_WORKTREE,
                       "base": NO_WORKTREE, "source": str(project), "check_root": "the artifact's own directory",
                       "emit_scope": "Paths are relative to the artifact's own directory, which holds "
                                     "only the files you write here. Write each file as a block "
                                     "introduced by a line of exactly ===FILE: <relative path>=== "
                                     "followed by its full contents."}
    # Match create/edit: inspect the exact nested route before launching research.
    # Other ModelClient implementations retain their own readiness contract.
    from refactory_dsh.model import DshModelClient

    client = getattr(ctx, "model", None)
    if client is None or isinstance(client, DshModelClient):
        readiness = sandbox.model_readiness(
            ctx.workdir / ".model-readiness",
            model_context=client.context() if isinstance(client, DshModelClient) else None,
        )
        if readiness.get("warning"):
            import sys

            print(f"refactory: warning: {readiness['warning']}", file=sys.stderr)
    ctx.write_json("brief.json", {"name": name, "brief": brief, "check": check})
    for feedback in ("research-review.txt", "check.txt"):
        ctx.write_text(feedback, "not run: initial authoring pass; no prior feedback\n")
    from refactory_dsh.create_ops import UNREFUSED

    # Resolved once, as create resolves its own inputs: the check reads the fact.
    setup = str(task.get("setup") or ctx.knob("setup", "") or "").strip()
    planned = delivery.plan_run(ctx, task=task) if worktrees else {}
    return {"name": name, "brief": brief, "check": check, "validation": "(no check has run yet)",
            "research_review": "(no research review has run yet)", **UNREFUSED, **located,
            **({"setup": setup} if worktrees else {}), **planned}


def _emitted(answer: str, *, edits: bool) -> tuple[dict[Path, str], Edits, tuple[Path, ...]]:
    """emit.txt as whole files, edits to existing files and deletions; refused when malformed.

    ``edits`` says whether this run takes edits and deletions at all: only a worktree has
    a last attempt for them to apply to.
    """
    kinds = "===FILE:, ===EDIT: or ===DELETE:" if edits else "===FILE:"
    headers = [line for line in answer.splitlines() if line.startswith(("===FILE:", "===EDIT:", "===DELETE:"))]
    if any(BLOCK.fullmatch(line) is None for line in headers):
        raise AttemptRefused(f"emit.txt has a block header that is not {kinds} followed by <relative path>===; "
                             "write each one on its own line, with a path of letters, digits, '_', '.', '/' or '-'")
    matches = list(BLOCK.finditer(answer))
    if not matches:
        raise AttemptRefused(f"emit.txt holds no {kinds} blocks; write each file's change as one, as the "
                             "prompt describes, with no other text")
    names = [match.group(2) for match in matches]
    if len(names) != len(set(names)):
        raise AttemptRefused("emit.txt names a file in more than one block; put all of a file's change in one block")
    files: dict[Path, str] = {}
    changed: Edits = {}
    deletes: list[Path] = []
    for index, (match, path) in enumerate(zip(matches, _artifact_paths(names))):
        kind = match.group(1)
        end = matches[index + 1].start() if index + 1 < len(matches) else len(answer)
        body = answer[match.end():end].strip("\n")
        if kind != "FILE" and not edits:
            raise AttemptRefused(f"{path}: this run has no worktree, so its artifact directory holds only "
                                 "whole files; write every file with ===FILE:")
        if kind == "FILE":
            _refuse_truncated(path, body)
            files[path] = body + "\n"
        elif kind == "EDIT":
            changed[path] = _pairs(path, body)
        elif body.strip():
            raise AttemptRefused(f"the ===DELETE: block for {path} has text after it; a deletion takes none, "
                                 "so leave the line after the header empty")
        else:
            deletes.append(path)
    return files, changed, tuple(deletes)


def _pairs(path: Path, body: str) -> list[tuple[str, str]]:
    """The edits of one ``===EDIT:`` block, as (lines to find, lines to put in their place)."""
    lines = body.split("\n")
    pairs: list[tuple[str, str]] = []
    index = 0
    while index < len(lines):
        if not lines[index].strip():
            index += 1
            continue
        if lines[index] != SEARCH:
            raise AttemptRefused(f"the edit to {path} has text outside its {SEARCH} ... {REPLACE} pairs: "
                                 f"{lines[index][:80]!r}; put every line inside a pair")
        try:
            end = lines.index(REPLACE, index + 1)
        except ValueError:
            raise AttemptRefused(f"an edit to {path} is missing its {REPLACE} line; close every {SEARCH} "
                                 f"with {DIVIDER} and {REPLACE}") from None
        dividers = [at for at in range(index + 1, end) if lines[at] == DIVIDER]
        if len(dividers) != 1:
            raise AttemptRefused(
                f"an edit to {path} has {len(dividers)} {DIVIDER} lines, so where its search text ends is "
                + ("missing; put one between the search and the replacement lines" if not dividers else
                   f"unclear; leave a line of exactly {DIVIDER} out of the edit, searching for the lines next "
                   "to it, or write the file whole with ===FILE:"))
        divider = dividers[0]
        search = "".join(line + "\n" for line in lines[index + 1:divider])
        replacement = "".join(line + "\n" for line in lines[divider + 1:end])
        if not search.strip():
            raise AttemptRefused(f"an edit to {path} has nothing to search for; to add a file, write it with "
                                 "===FILE:, and to add lines, search for the lines next to them")
        _refuse_truncated(path, replacement)
        pairs.append((search, replacement))
        index = end + 1
    if not pairs:
        raise AttemptRefused(f"the ===EDIT: block for {path} holds no {SEARCH} ... {REPLACE} edits; give it "
                             "at least one, or leave the file out")
    return pairs


def _edited(path: Path, text: str, pairs: list[tuple[str, str]], shown: str) -> tuple[str, int]:
    """``text`` with each edit applied in turn, and how many were already in it.

    Each edit is refused unless its search lines are there exactly once. Search lines
    match whole lines, from a line's start. The read tool shows a file with ``\r``
    stripped and every line ended, so a file whose lines all end in ``\r\n``, or whose
    last line has no newline, is matched as shown and written back as it was. An edit
    whose result is already where it would go -- a model re-sending an earlier attempt's
    edit -- is skipped rather than applied twice. ``shown`` is the command that prints
    ``text``, so a refusal names where to copy from.
    """
    crlf = "\r\n" in text and text.count("\r\n") == text.count("\n")
    work = text.replace("\r\n", "\n") if crlf else text
    open_end = not work.endswith("\n")
    work += "\n" if open_end else ""
    skipped = 0
    for search, replacement in pairs:
        starts = _line_starts(work, search)
        if _already_applied(work, search, replacement, starts):
            skipped += 1
            continue
        if len(starts) != 1:
            first = next((line.strip() for line in search.splitlines() if line.strip()), "")[:80]
            raise AttemptRefused(
                f"an edit to {path} searches for lines beginning {first!r}, which "
                + (f"are not in the file the edit applies to; copy them exactly from `{shown}`"
                   if not starts else f"appear {len(starts)} times; add lines around them until they appear once"))
        work = work[:starts[0]] + replacement + work[starts[0] + len(search):]
    if open_end and work.endswith("\n"):
        work = work[:-1]
    return (work.replace("\n", "\r\n") if crlf else work), skipped


def _already_applied(text: str, search: str, replacement: str, starts: list[int]) -> bool:
    """Whether an edit's replacement already stands where it would go, as after a re-sent edit.

    Either the search lines are gone and the replacement is there once, or the search
    lines are there once inside a copy of the replacement (an insertion sent again).
    An edit that removes lines is never taken as applied: nothing shows it was.
    """
    if not replacement.strip() or len(starts) > 1:
        return False
    placed = _line_starts(text, replacement)
    if not starts:
        return len(placed) == 1
    return any(at <= starts[0] and starts[0] + len(search) <= at + len(replacement) for at in placed)


def _line_starts(text: str, lines: str) -> list[int]:
    """Where ``lines`` occur in ``text`` starting at a line's start."""
    found: list[int] = []
    at = text.find(lines)
    while at != -1:
        if at == 0 or text[at - 1] == "\n":
            found.append(at)
        at = text.find(lines, at + 1)
    return found


def _refuse_truncated(path: Path, text: str) -> None:
    """Refuse text copied from a read that the read tool cut short, before it is written anywhere."""
    match = TRUNCATED.search(text)
    if match:
        line = text.count("\n", 0, match.start()) + 1
        raise AttemptRefused(
            f"{path} would contain, on line {line}, the read tool's note that it cut text short: "
            f"{match.group(0)!r}. Text copied from that read is incomplete, so the file would be too. Write "
            "only text that was shown in full; in a worktree, change the file with ===EDIT: blocks that "
            "leave out the lines you could not read whole.")


def materialise_artifact(ctx: Any) -> dict[str, Any]:
    """Make the emitted files the run's artifact: a commit in its worktree, when it has one.

    Each attempt is one commit on the run's branch (`worktree.commit_attempt`), building
    on the last: whole files, edits to files as the last attempt committed them, and
    deletions. ``changes.diff`` holds the branch's diff from its base, which is what the
    reviewers judge. A run without a worktree writes the artifact directory, as it always
    has. An emission that cannot be applied -- a malformed block, an edit whose text is
    not in its file, text copied from a truncated read -- does not end the run: it is
    recorded as ``emission_refused``, and the check sends the reason back to emit.
    """
    try:
        found = worktree.recorded(ctx.workdir)
        # Cleared only once set, so a run whose graph predates the fact still materialises.
        cleared = {"emission_refused": ""} if ctx.facts.get("emission_refused") else {}
        if found is None:
            return {**materialise_directory(ctx), "commit": NO_WORKTREE, **cleared,
                    "change": "The artifact directory holds only the files this run wrote."}
        return {**_commit_emission(ctx), **cleared}
    except AttemptRefused as refused:
        return {"emission_refused": str(refused)}


def _commit_emission(ctx: Any) -> dict[str, Any]:
    """Apply emit.txt to the run's worktree as its next attempt; one a prune removed is added back."""
    found = worktree.require_current(ctx.workdir)
    answer = ctx.read_text("emit.txt")
    files, changed, deletes = _emitted(answer, edits=True)
    path = Path(found.path)
    digest = hashlib.sha256(answer.encode()).hexdigest()
    rev = found.head
    last = _json(Path(ctx.workdir) / EMISSION)
    if (found.head != found.base and last.get("digest") == digest
            and worktree.git(path, "rev-parse", f"{found.head}^").strip() == last.get("attempt_on")):
        # The last attempt is this emission already -- materialise redone, or the same
        # answer given again -- so it applies to what it applied to then: its edits'
        # text, and the files it deletes, are gone from the last attempt itself.
        rev = str(last["applied_to"])
        deletes = tuple(name for name in deletes if worktree.committed_text(found, name) is not None)
    repeated = []
    for relative, pairs in changed.items():
        current = worktree.committed_text(found, relative, rev)
        if current is None:
            raise AttemptRefused(f"{relative} is not a file of the last attempt, so it cannot be edited; "
                                 "write it whole with ===FILE:")
        files[relative], skipped = _edited(relative, current, pairs, f"git -C {found.path} show {rev[:12]}:{relative}")
        if skipped:
            repeated.append(str(relative))
    if repeated:
        target_guard.warn(f"emit re-sent edits the last attempt already holds, so they were skipped rather than "
                          f"applied twice: {target_guard._named(repeated)}")
    # No drift warning: the run's base is pinned, so a moving checkout changes nothing it sees.
    attempt = len(worktree.git(path, "rev-list", f"{found.base}..HEAD").split()) + 1
    advanced = worktree.commit_attempt(ctx.workdir, files, f"{ctx.facts['name']}: attempt {attempt}", deletes)
    worktree._write_json(Path(ctx.workdir) / EMISSION, {"digest": digest, "attempt_on": found.head,
                                                         "applied_to": rev})
    _warn_if_shared_changed(advanced, "since the last check")
    ctx.write_text("changes.diff", worktree.diff(advanced))
    wrapper = Path(ctx.workdir).resolve() / worktree.WRAPPER
    delivered = worktree.git(path, "diff", "--name-only", "--diff-filter=d", advanced.base, advanced.head)
    return {"artifact": advanced.path, "files": sorted(delivered.splitlines()),
            "commit": advanced.head,
            "change": (
                f"The artifact is a git worktree of {advanced.target} on branch {advanced.branch}. "
                f"The change under review is `git -C {advanced.path} diff {advanced.base[:12]}`, also "
                f"saved as changes.diff in the run directory. Run commands with `{wrapper} <command>`, "
                "which uses the worktree's own environment, not refactory's."
            )}


def materialise_directory(ctx: Any) -> dict[str, Any]:
    """Write the emitted files into the artifact directory, replacing an earlier attempt's."""
    checked, _, _ = _emitted(ctx.read_text("emit.txt"), edits=False)
    root = _artifact_root(str(ctx.facts["name"]))
    _claim(str(ctx.facts["name"]), ctx.workdir)
    target_guard.warn_if_moved(ctx.workdir)
    # Validate and finish all writes privately before moving an earlier artifact.
    root.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".design-", dir=root.parent))
    preserve_backup = False
    previous = temporary / "previous"
    try:
        stage = temporary / "artifact"
        stage.mkdir()
        for relative, content in checked.items():
            target = stage / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
        replaced = root.exists()
        if replaced:
            root.rename(previous)
        try:
            stage.rename(root)
        except BaseException:
            if replaced:
                try:
                    previous.rename(root)
                except OSError as error:
                    preserve_backup = True
                    raise RuntimeError(f"artifact replacement failed; previous artifact retained at {previous}") from error
            raise
    finally:
        if not preserve_backup and not (previous.exists() and not root.exists()):
            shutil.rmtree(temporary)
    return {"artifact": str(root), "files": sorted(str(path) for path in checked)}


def check_artifact(ctx: Any) -> str:
    """Gate: run the brief's check, in a sandbox, and refuse what does not pass.

    This is the design-time equivalent of the package gate: a package must import and
    score, an artifact must build and run. Both refuse rather than warn, because an
    artifact nobody checked is not a deliverable.

    In a worktree, the check runs there with the worktree's own environment: its
    dependencies installed when its manifests call for them, then the ``setup`` knob's
    command, and nothing of refactory's own on the path (`worktree.check_environment`).
    An emission materialise could not apply is sent back to emit with the reason, as a
    failed check is, rather than ending the run (#330 E5).
    """
    from refactory_dsh import authoring_evidence as evidence
    from refactory_dsh.create_ops import repair

    refused = str(ctx.facts.get("emission_refused") or "")
    if refused:
        # Nothing was materialised, so there is nothing to record runtime evidence about;
        # the attempt the retry makes records its own.
        detail = f"materialise could not apply emit.txt, so nothing was checked: {refused}"
        ctx.write_text("check.txt", detail + "\n")
        return repair(ctx, [detail])
    command = str(ctx.facts["check"])
    timeout = int(ctx.knob("check_timeout_s", 180))
    found = worktree.recorded(ctx.workdir)
    if found is None:
        root = _artifact_root(str(ctx.facts["name"]))
        if Path(str(ctx.facts["artifact"])) != root:
            raise ValueError("artifact location is stale or ambiguous; materialise again before checking")
        _require_owner(str(ctx.facts["name"]), ctx.workdir)
        target_guard.warn_if_moved(ctx.workdir)
        result = sandbox.run_command(command, workdir=root, timeout_s=timeout,
                                     budget=getattr(ctx, "budget", None))
    else:
        found, restored = worktree.restore(ctx.workdir)
        if found.head != str(ctx.facts.get("commit")):
            raise ValueError("the worktree is not at the attempt materialise committed; materialise again before checking")
        if restored:
            target_guard.warn(f"tracked files in the run's worktree had changed since materialise and were put "
                              f"back, so the check runs the committed attempt: {target_guard._named(restored)}")
        result = _check_in_worktree(ctx, found, command, timeout)
        _warn_if_shared_changed(found, "while preparing for and running the check")
        # The reviews and the next attempt read the worktree, and edits apply to the
        # committed attempt: a check that reformats a file must not leave them apart.
        _, changed = worktree.restore(ctx.workdir)
        if changed:
            target_guard.warn(f"the check changed tracked files in the run's worktree; they were put back, so "
                              f"the reviews and the next attempt see the committed attempt: "
                              f"{target_guard._named(changed)}")
    detail = "\n".join((result.problem(), result.stdout or "", result.stderr or "")).strip()
    outcome = {"command": command, "status": "pass" if result.ok else evidence.failure_kind(
        detail, timed_out=result.timed_out, returncode=result.returncode),
        "stdout": result.stdout, "stderr": result.stderr, "returncode": result.returncode,
        "timed_out": result.timed_out, "elapsed_s": result.elapsed_s}
    ctx.write_text("check.txt", detail or "ok\n")
    evidence.record(ctx, outcome)
    if result.ok:
        ctx.set_fact("validation", "(the check passed)")
        return "proceed"
    evidence.stop_unrepairable(outcome["status"], detail)
    return repair(ctx, [detail])


def _warn_if_shared_changed(found: worktree.Worktree, when: str) -> None:
    """Report a change to the git configuration or hooks the worktree shares with its target."""
    if worktree.shared_changed(found):
        target_guard.warn(f"the git configuration or hooks the run's worktree shares with {found.target} "
                          f"changed {when}; check them before your next commit there")


def _check_in_worktree(ctx: Any, found: worktree.Worktree, command: str, timeout: int) -> Any:
    """Run the check in the run's worktree, after preparing the environment it needs.

    An install that writes a lockfile amends the attempt (`worktree.ensure_environment`),
    so the commit fact and the reviewers' diff follow it to its new head.
    """
    home = Path(ctx.workdir).resolve() / worktree.CHECK_HOME
    home.mkdir(parents=True, exist_ok=True)
    budget = getattr(ctx, "budget", None)
    amended, problems = worktree.ensure_environment(found, ctx.workdir, budget)
    if amended.head != found.head:
        found = amended
        ctx.set_fact("commit", found.head)
        ctx.write_text("changes.diff", worktree.diff(found))
    worktree.write_wrapper(found, ctx.workdir)
    env = worktree.check_environment(Path(found.path), home)
    setup = str(ctx.facts.get("setup") or "").strip()
    if not problems and setup:
        prepared = sandbox.run_command(setup, workdir=Path(found.path), timeout_s=timeout,
                                       budget=budget, env=env, home=home, exact_env=True)
        if not prepared.ok:
            problems.append(f"the setup command `{setup}` failed: "
                            + "\n".join((prepared.problem(), prepared.stdout or "", prepared.stderr or "")).strip())
    if problems:
        return sandbox.SandboxResult(ok=False, returncode=1, elapsed_s=0.0, stderr=(
            "the worktree's environment could not be prepared:\n" + "\n".join(problems)))
    return sandbox.run_command(command, workdir=Path(found.path), timeout_s=timeout,
                               budget=budget, env=env, home=home, exact_env=True)


def report_artifact(ctx: Any) -> dict[str, Any]:
    """Say what was produced, so a run's output is discoverable rather than inferred."""
    from refactory_dsh.authoring_evidence import require_completion

    found = worktree.recorded(ctx.workdir)
    if found is None:
        require_completion(ctx)
        _require_owner(str(ctx.facts["name"]), ctx.workdir)
        target_guard.warn_if_moved(ctx.workdir)
    else:
        # Where the worktree stands first, so a moved HEAD is named as such and not as a
        # stale sign-off.
        found = worktree.require_current(ctx.workdir)
        require_completion(ctx)
        edited = worktree.tracked_edits(found)
        if edited:
            target_guard.warn(f"tracked files in the run's worktree differ from its last attempt and are not "
                              f"part of its branch {found.branch}: {target_guard._named(edited)}")
        _warn_if_shared_changed(found, "during the reviews")
    payload = {
        "name": ctx.facts.get("name"),
        "artifact": ctx.facts.get("artifact"),
        "files": ctx.facts.get("files", []),
        "check": ctx.facts.get("check"),
        "checked": True,
        "validation_evidence": ctx.read_json("authoring-evidence.json", None),
        "intent_audit": ctx.read_json("intent-audit.json", None),
    }
    if found is not None:
        payload.update(target=found.target, branch=found.branch, base=found.base, head=found.head,
                       diff_stat=worktree.git(Path(found.path), "diff", "--stat", found.base, "HEAD").strip())
    ctx.write_json("design.json", payload)
    if found is not None:
        worktree.record_outcome(found, "succeeded")
        projects.record_run(Path(found.target), run=ctx.workdir, branch=found.branch, base=found.base,
                            status="succeeded")
    return {"designed": json.dumps(payload, default=str)}
