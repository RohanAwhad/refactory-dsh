"""The operations design-v2's graph calls.

`create` emits a **package of modes**: a manifest, a `Mode` subclass, `score()`.
`design` emits **software**: no manifest, no `score()`, no graph. So the validator
and the check differ, and so does where the output lands.
"""

from __future__ import annotations

import json
import re
import shutil
import tempfile
from pathlib import Path, PureWindowsPath
from typing import Any

from refactory_dsh import sandbox
from refactory_dsh.create_ops import FILE_MARKER, parse_files
from refactory_dsh.paths import project_for_nodes

STAGING = "staging"

#: Project-owned software artifacts are separate from discoverable mode packages.
ARTIFACTS = Path(".factory") / "artifacts"


def _artifact_name(name: str) -> str:
    if (not name or name in {".", ".."} or name != name.strip()
            or any(char in name for char in "/\\:")
            or any(ord(char) < 32 for char in name)):
        raise ValueError("artifact name must be one directory name, without separators, drives or '..'")
    return name


def _artifact_root(name: str) -> Path:
    """Use the configured project, never the lowered operation's working directory."""
    project = project_for_nodes().expanduser().resolve()
    root = project / ARTIFACTS / _artifact_name(name)
    for path in (project / ".factory", project / ARTIFACTS, root):
        if path.is_symlink() or not path.resolve().is_relative_to(project):
            raise ValueError(f"artifact path must not cross a symlink: {path}")
        if path.exists() and not path.is_dir():
            raise ValueError(f"artifact path is not a directory: {path}")
    return root


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


def prepare_design(ctx: Any) -> dict[str, Any]:
    """Read the brief, and what the brief says counts as done."""
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
            "research_review": "(no research review has run yet)", **UNREFUSED}


def materialise_artifact(ctx: Any) -> dict[str, Any]:
    """Write the emitted files into the artifact directory."""
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
    root = _artifact_root(str(ctx.facts["name"]))
    checked = _artifact_files(files)
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
    """
    root = _artifact_root(str(ctx.facts["name"]))
    if Path(str(ctx.facts["artifact"])) != root:
        raise ValueError("artifact location is stale or ambiguous; materialise again before checking")
    command = str(ctx.facts["check"])
    from refactory_dsh import authoring_evidence as evidence
    from refactory_dsh.create_ops import repair

    result = sandbox.run_command(
        command, workdir=root, timeout_s=int(ctx.knob("check_timeout_s", 180)),
        budget=getattr(ctx, "budget", None),
    )
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


def report_artifact(ctx: Any) -> dict[str, Any]:
    """Say what was produced, so a run's output is discoverable rather than inferred."""
    from refactory_dsh.authoring_evidence import require_completion

    require_completion(ctx)
    payload = {
        "name": ctx.facts.get("name"),
        "artifact": ctx.facts.get("artifact"),
        "files": ctx.facts.get("files", []),
        "check": ctx.facts.get("check"),
        "checked": True,
        "validation_evidence": ctx.read_json("authoring-evidence.json", None),
        "intent_audit": ctx.read_json("intent-audit.json", None),
    }
    ctx.write_json("design.json", payload)
    return {"designed": json.dumps(payload, default=str)}
