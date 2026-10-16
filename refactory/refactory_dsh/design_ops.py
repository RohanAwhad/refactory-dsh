"""The operations design-v2's graph calls.

`create` emits a **package of modes**: a manifest, a `Mode` subclass, `score()`.
`design` emits **software**: no manifest, no `score()`, no graph. So the validator
and the check differ, and so does where the output lands.
"""

from __future__ import annotations

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

from refactory_dsh import projects, sandbox, target_guard, worktree
from refactory_dsh.create_ops import FILE_MARKER, parse_files
from refactory_dsh.paths import project_for_nodes
from refactory_dsh.worktree import _alive, _json

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
    checked: dict[Path, str] = {}
    for relative, content in files.items():
        path = Path(relative)
        if (path.is_absolute() or PureWindowsPath(relative).drive
                or "\\" in relative or ".." in path.parts or not path.parts
                or relative.endswith("/") or any(ord(char) < 32 for char in relative)):
            raise ValueError(f"{relative!r} is not a file inside the artifact directory")
        if path in checked:
            raise ValueError(f"{relative!r} duplicates an artifact file")
        checked[path] = content
    for path in checked:
        if any(parent in checked for parent in path.parents):
            raise ValueError(f"{str(path)!r} conflicts with an artifact file used as its directory")
    return checked


def _run_id(workdir: Path) -> str:
    """This invocation's id, so the run's branch names the run; random for a walked run."""
    found = str(_json(Path(workdir) / worktree.INVOCATION).get("id") or "")
    return found[:12] if re.fullmatch(r"[0-9a-f]{12,}", found) else uuid.uuid4().hex[:12]


def _open_worktree(ctx: Any, name: str, task: dict[str, Any]) -> dict[str, Any]:
    """Give the run its own worktree of its target, and say what that means (#351).

    The target is the repository ``target`` names, or a new project in the projects
    folder when it names none. The run branches from ``base`` -- the target's HEAD, or
    a new project's ``main`` -- pinned to a commit here, and never works in the target
    itself: what the person has there, committed or not, stays theirs.
    """
    named = str(task.get("target") or ctx.knob("target", "") or "").strip()
    target = worktree.repository_root(Path(named)) if named else projects.new_project(name)
    rev = str(task.get("base") or ctx.knob("base", "") or "").strip() or ("HEAD" if named else "main")
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
        print(f"refactory: {note}", file=sys.stderr)
    found = worktree.create(target, base, run_id, ctx.workdir)
    # The person's checkout is still watched, as a backstop: drift there is reported,
    # and `emit` may change neither it nor the run's own worktree (#319).
    baseline = target_guard.record(ctx.workdir, target, output_only=OUTPUT_ONLY, also=(Path(found.path),))
    if baseline["changes"]:
        target_guard.warn(f"{len(baseline['changes'])} uncommitted change(s) in {target} are not part of "
                          f"this run, which starts from commit {found.base[:12]}: "
                          f"{target_guard._named(sorted(baseline['changes']))}")
    elif baseline["changes"] is None:
        target_guard.warn(f"the target {target} has more than {target_guard.LIMIT} uncommitted paths, "
                          "so this run cannot tell whether it moves")
    projects.record_run(target, run=ctx.workdir, branch=found.branch, base=found.base, status="running")
    return {"target": str(target), "worktree": found.path, "branch": found.branch, "base": found.base,
            "source": found.path,
            "emit_scope": (
                f"Paths are relative to the root of this run's worktree, {found.path}: a git worktree of "
                f"{target} on branch {found.branch}. Files you do not write stay as they are at the "
                "base commit, so write only the files you add or change, each one in full, and read "
                "the current ones there first."
            )}


def prepare_design(ctx: Any, *, worktrees: bool = True) -> dict[str, Any]:
    """Read the brief, and what the brief says counts as done.

    A design run works in its own worktree unless its ``worktree`` knob is off
    (`_open_worktree`). ``worktrees`` is False for design_fast, which keeps its own
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
        located = _open_worktree(ctx, name, task)
    else:
        given = [knob for knob in ("target", "base", "setup")
                 if str(task.get(knob) or ctx.knob(knob, "") or "").strip()]
        if given:
            raise ValueError(f"{', '.join(given)} need the run's worktree, which is off; turn it on, or "
                             "leave them empty to write the artifact under .factory/artifacts")
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
                       "base": NO_WORKTREE, "source": str(project),
                       "emit_scope": "Paths are relative to the artifact's own directory, which holds "
                                     "only the files you write here."}
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

    return {"name": name, "brief": brief, "check": check, "validation": "(no check has run yet)",
            "research_review": "(no research review has run yet)", **UNREFUSED, **located}


def _emitted(ctx: Any) -> dict[Path, str]:
    """The files `emit` wrote into emit.txt, refused when its blocks are malformed."""
    answer = ctx.read_text("emit.txt")
    headers = [line for line in answer.splitlines() if line.startswith("===FILE:")]
    if any(FILE_MARKER.fullmatch(line) is None for line in headers):
        raise ValueError("invalid artifact file header; use ===FILE: <relative path>=== with letters, digits, '_', '.', '/' or '-'")
    paths = [match.group(1) for match in FILE_MARKER.finditer(answer)]
    if len(paths) != len(set(paths)):
        raise ValueError("the artifact emission repeats a file path")
    files = parse_files(answer)
    if not files:
        raise ValueError("the answer contained no '===FILE: <name>===' blocks")
    return _artifact_files(files)


def materialise_artifact(ctx: Any) -> dict[str, Any]:
    """Make the emitted files the run's artifact: a commit in its worktree, when it has one.

    Each attempt is one commit on the run's branch (`worktree.commit_attempt`), and
    ``changes.diff`` holds the branch's diff from its base, which is what the reviewers
    judge. A run without a worktree writes the artifact directory, as it always has.
    """
    found = worktree.recorded(ctx.workdir)
    if found is None:
        return {**materialise_directory(ctx), "commit": NO_WORKTREE,
                "change": "The artifact directory holds only the files this run wrote."}
    checked = _emitted(ctx)
    target_guard.warn_if_moved(ctx.workdir)
    attempt = len(worktree.git(Path(found.path), "rev-list", f"{found.base}..HEAD").split()) + 1
    advanced = worktree.commit_attempt(ctx.workdir, checked, f"{ctx.facts['name']}: attempt {attempt}")
    _warn_if_shared_changed(advanced, "since the last check")
    ctx.write_text("changes.diff", worktree.diff(advanced))
    wrapper = Path(ctx.workdir) / worktree.WRAPPER
    return {"artifact": advanced.path, "files": sorted(str(path) for path in checked),
            "commit": advanced.head,
            "change": (
                f"The artifact is a git worktree of {advanced.target} on branch {advanced.branch}. "
                f"The change under review is `git -C {advanced.path} diff {advanced.base[:12]}`, also "
                f"saved as changes.diff in the run directory. Run commands with `{wrapper} <command>`, "
                "which uses the worktree's own environment, not refactory's."
            )}


def materialise_directory(ctx: Any) -> dict[str, Any]:
    """Write the emitted files into the artifact directory, replacing an earlier attempt's."""
    checked = _emitted(ctx)
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
    """
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
        target_guard.warn_if_moved(ctx.workdir)
        result = _check_in_worktree(ctx, found, command, timeout)
        _warn_if_shared_changed(found, "while preparing for and running the check")
    from refactory_dsh import authoring_evidence as evidence
    from refactory_dsh.create_ops import repair

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
    """Run the check in the run's worktree, after preparing the environment it needs."""
    home = Path(ctx.workdir) / ".refactory" / "check-home"
    home.mkdir(parents=True, exist_ok=True)
    problems = worktree.ensure_environment(found, ctx.workdir)
    worktree.write_wrapper(found, ctx.workdir)
    env = worktree.check_environment(Path(found.path), home)
    setup = str(ctx.knob("setup", "") or "").strip()
    if not problems and setup:
        prepared = sandbox.run_command(setup, workdir=Path(found.path), timeout_s=timeout,
                                       budget=getattr(ctx, "budget", None), env=env, home=home, exact_env=True)
        if not prepared.ok:
            problems.append(f"the setup command `{setup}` failed: "
                            + "\n".join((prepared.problem(), prepared.stdout or "", prepared.stderr or "")).strip())
    if problems:
        return sandbox.SandboxResult(ok=False, returncode=1, elapsed_s=0.0, stderr=(
            "the worktree's environment could not be prepared:\n" + "\n".join(problems)))
    return sandbox.run_command(command, workdir=Path(found.path), timeout_s=timeout,
                               budget=getattr(ctx, "budget", None), env=env, home=home, exact_env=True)


def report_artifact(ctx: Any) -> dict[str, Any]:
    """Say what was produced, so a run's output is discoverable rather than inferred."""
    from refactory_dsh.authoring_evidence import require_completion

    require_completion(ctx)
    found = worktree.recorded(ctx.workdir)
    if found is None:
        _require_owner(str(ctx.facts["name"]), ctx.workdir)
    else:
        found = worktree.require_current(ctx.workdir)
        edited = worktree.tracked_edits(found)
        if edited:
            target_guard.warn(f"tracked files in the run's worktree differ from its last attempt and are not "
                              f"part of its branch {found.branch}: {target_guard._named(edited)}")
        _warn_if_shared_changed(found, "during the reviews")
    target_guard.warn_if_moved(ctx.workdir)
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
