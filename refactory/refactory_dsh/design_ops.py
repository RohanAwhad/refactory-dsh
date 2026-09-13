"""The operations design-v2's graph calls.

`create` emits a **package of modes**: a manifest, a `Mode` subclass, `score()`.
`design` emits **software**: no manifest, no `score()`, no graph. So the validator
and the check differ, and so does where the output lands.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

from refactory_dsh import sandbox
from refactory_dsh.create_ops import FILE_MARKER, parse_files

STAGING = "staging"

#: Where a designed artifact lands, beside the workspace rather than in it: it is not
#: a package, and it must not be discoverable as one.
ARTIFACTS = Path(".factory") / "artifacts"


def prepare_design(ctx: Any) -> dict[str, Any]:
    """Read the brief, and what the brief says counts as done."""
    task = dict(getattr(ctx, "task", {}) or {})
    name = str(task.get("name") or "").strip()
    if not name:
        raise ValueError("design-mode needs an artifact name, e.g. {'name': 'my_tool'}")
    brief = str(task.get("brief") or "").strip()
    check = str(task.get("check") or "").strip()
    if not check:
        raise ValueError(
            "design-mode needs a check: the command that decides whether the artifact "
            "works, e.g. {'check': 'python -m pytest -q'}"
        )
    ctx.write_json("brief.json", {"name": name, "brief": brief, "check": check})
    return {"name": name, "brief": brief, "check": check, "validation": "(no check has run yet)"}


def materialise_artifact(ctx: Any) -> dict[str, Any]:
    """Write the emitted files into the artifact directory."""
    files = parse_files(ctx.read_text("emit.txt"))
    if not files:
        raise ValueError("the answer contained no '===FILE: <name>===' blocks")
    name = str(ctx.facts["name"])
    root = ARTIFACTS / name
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    written: list[str] = []
    for relative, content in files.items():
        target = root / relative
        if not target.resolve().is_relative_to(root.resolve()):
            raise ValueError(f"{relative!r} escapes the artifact directory")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        written.append(relative)
    return {"artifact": str(root), "files": sorted(written)}


def check_artifact(ctx: Any) -> str:
    """Gate: run the brief's check, in a sandbox, and refuse what does not pass.

    This is the design-time equivalent of the package gate: a package must import and
    score, an artifact must build and run. Both refuse rather than warn, because an
    artifact nobody checked is not a deliverable.
    """
    root = Path(str(ctx.facts["artifact"]))
    command = str(ctx.facts["check"])
    attempts = int(ctx.knob("check_attempts", 2))
    result = sandbox.run_command(
        command,
        workdir=root,
        timeout_s=int(ctx.knob("check_timeout_s", 180)),
    )
    tail = (result.stderr or result.stdout).strip().splitlines()
    detail = tail[-1] if tail else ""
    if result.ok:
        ctx.write_text("check.txt", "ok\n")
        ctx.set_fact("validation", "(the check passed)")
        return "proceed"
    report = result.problem() + (f": {detail}" if detail else "")
    ctx.write_text("check.txt", report + "\n")
    ctx.set_fact("validation", report)
    used = int(ctx.facts.get("check_attempts_used", 0)) + 1
    if used >= attempts:
        raise ValueError(f"the artifact still does not pass its check: {report}")
    ctx.set_fact("check_attempts_used", used)
    return "reloop"


def report_artifact(ctx: Any) -> dict[str, Any]:
    """Say what was produced, so a run's output is discoverable rather than inferred."""
    payload = {
        "name": ctx.facts.get("name"),
        "artifact": ctx.facts.get("artifact"),
        "files": ctx.facts.get("files", []),
        "check": ctx.facts.get("check"),
        "checked": True,
    }
    ctx.write_json("design.json", payload)
    return {"designed": json.dumps(payload, default=str)}
