"""Operations for the explicit fast modes; full-mode review is never invoked."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any

from refactory_dsh import create_ops, design_ops, sandbox, target_guard, authoring_evidence as evidence
from refactory_dsh.nn import Verdict

#: A fast mode runs no full review gate or overwatch, so the prompts that show those
#: verdicts say so; "nothing refused yet" would read as a promise of a later review.
FAST_UNREFUSED = {
    "qa_gate": "(fast mode runs no full review gate)",
    "overwatch_review": "(fast mode runs no overwatch audit)",
}


def prepare_package(ctx: Any) -> dict[str, Any]:
    prepared = create_ops.prepare(ctx)
    ctx.write_text("repair.txt", "(initial implementation; no repair requested)\n")
    ctx.write_text("validation.txt", "(structural admission not run)\n")
    return {**prepared, **FAST_UNREFUSED, "repair_feedback": "(initial implementation)", "repairs_used": 0}


def survey_package(ctx: Any) -> dict[str, Any]:
    """Fast authoring has a selected source, not a package-discovery stage."""
    selected = str(ctx.facts.get("revision_of") or ctx.facts.get("extends") or "")
    package = selected.split("/", 1)[0] if selected else ""
    packages = [{"package": package, "reference": selected}] if package else []
    ctx.write_json("survey.json", {"packages": packages})
    return {"existing": [package] if package else [],
            "survey": (f"Use the supplied source for {selected}; no other package survey is needed."
                       if selected else "No reference package requested; implement from the supplied API and brief.")}


def _source_manifest(root: Path) -> dict[str, str]:
    ignored = {".git", ".factory", ".venv", "node_modules", "__pycache__", ".pytest_cache"}
    result = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if any(part in ignored or part.startswith(".env") for part in relative.parts):
            continue
        if path.is_symlink():
            raise ValueError(f"source project contains a symlink: {relative}; use an explicit plain-file source")
        if path.is_file():
            result[str(relative)] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def prepare_artifact(ctx: Any) -> dict[str, Any]:
    # Fast design keeps copying `source_project` until it moves onto worktrees (#351).
    prepared = design_ops.prepare_design(ctx, worktrees=False)
    ctx.write_text("repair.txt", "(initial implementation; no repair requested)\n")
    source = str(getattr(ctx, "task", {}).get("source_project") or ctx.knob("source_project", "") or "").strip()
    manifest: dict[str, str] = {}
    if source:
        root = Path(source).expanduser().resolve()
        if not root.is_dir():
            raise ValueError("source_project must be an existing directory")
        source = str(root)
        manifest = _source_manifest(root)
    ctx.write_json("source-manifest.json", {"source_project": source, "files": manifest})
    return {**prepared, "source_project": source, "source_files": json.dumps(sorted(manifest)),
            **FAST_UNREFUSED, "repair_feedback": "(initial implementation)", "repairs_used": 0}


def materialise_artifact(ctx: Any) -> dict[str, Any]:
    source = str(ctx.facts.get("source_project") or "")
    if not source:
        return design_ops.materialise_directory(ctx)
    root = Path(source)
    expected = ctx.read_json("source-manifest.json", {}).get("files", {})
    if _source_manifest(root) != expected:
        raise ValueError("source project changed; restart preparation to invalidate dependent results")
    files = create_ops.parse_files(ctx.read_text("emit.txt"))
    if not files:
        raise ValueError("implementation emitted no file blocks")
    checked = design_ops._artifact_files(files)
    output = design_ops._artifact_root(str(ctx.facts["name"]))
    design_ops._claim(str(ctx.facts["name"]), ctx.workdir)
    target_guard.warn_if_moved(ctx.workdir)
    if output == root or output in root.parents:
        raise ValueError("output artifact may not replace the source project")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".design-fast-", dir=output.parent))
    stage = temporary / "artifact"
    backup = temporary / "previous"
    try:
        stage.mkdir()
        for relative in expected:
            target = stage / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(root / relative, target)
        for relative, content in checked.items():
            target = stage / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
        if output.exists():
            output.rename(backup)
        try:
            stage.rename(output)
        except BaseException:
            if backup.exists():
                backup.rename(output)
            raise
    finally:
        if not backup.exists() or output.exists():
            shutil.rmtree(temporary)
    return {"artifact": str(output), "files": sorted(set(expected) | {str(path) for path in checked})}


def _report(ctx: Any, status: str, findings: list[str]) -> dict[str, Any]:
    return {
        "schema": 1, "status": status, "repair_limit": int(ctx.knob("repair_attempts", 1)),
        "repairs_used": int(ctx.facts.get("repairs_used", 0)), "research": "not requested/run",
        "structural_admission": "passed" if ctx.facts.get("package_path") and ctx.facts.get("validation") == "(structural admission passed)" else "not applicable or pending",
        "focused_qa": ctx.read_json("focused-qa.json", None),
        "runtime_check": ctx.read_json("focused-check.json", None),
        "canonical_smoke": "passed" if ctx.facts.get("smoke_ok") else "not run",
        "findings": findings,
        "resources": ctx.budget.snapshot() if getattr(ctx, "budget", None) is not None else None,
    }


def _repair(ctx: Any, findings: list[str]) -> str:
    used = int(ctx.facts.get("repairs_used", 0))
    limit = int(ctx.knob("repair_attempts", 1))
    feedback = "\n".join(findings)
    ctx.write_text("repair.txt", feedback + "\n")
    ctx.set_fact("repair_feedback", feedback)
    if used >= limit:
        ctx.write_json("fast-report.json", _report(ctx, "unresolved", findings))
        raise ValueError(f"focused QA/admission unresolved after {used} repair(s): {feedback}")
    ctx.set_fact("repairs_used", used + 1)
    ctx.write_json("fast-report.json", _report(ctx, "repairing", findings))
    return Verdict("reloop", feedback)


def validate_package(ctx: Any) -> str:
    brief = ctx.read_json("brief.json", {})
    try:
        problems = create_ops.check_package(
            str(ctx.facts["name"]), requires_data_node=bool(brief.get("data_node")),
            requires_task_refs=tuple(brief.get("task_refs", ())),
            requires_input=create_ops.declared_inputs(ctx.knob("requires_input", "")),
        )
    except Exception as exc:
        problems = [f"{type(exc).__name__}: {exc}"]
    ctx.write_text("validation.txt", "\n".join(problems) + "\n" if problems else "ok\n")
    ctx.set_fact("validation", "\n".join(problems) if problems else "(structural admission passed)")
    return _repair(ctx, problems) if problems else "proceed"


def focused_check(ctx: Any) -> dict[str, Any]:
    output = str(ctx.facts.get("artifact") or ctx.facts.get("package_path") or "")
    if not output:
        raise ValueError("focused QA needs the materialised output path")
    command = str(ctx.facts.get("check") or ctx.knob("check", "") or "").strip()
    payload: dict[str, Any] = {"command": command or None, "status": "not_requested", "stdout": None, "stderr": None}
    if command:
        budget = getattr(ctx, "budget", None)
        timeout = float(ctx.knob("check_timeout_s", 60))
        if budget is not None:
            budget.checkpoint()
            remaining = budget.remaining_s()
            if remaining is not None:
                timeout = min(timeout, remaining)
        result = sandbox.run_command(command, workdir=Path(output), timeout_s=timeout, budget=budget)
        payload.update(status="pass" if result.ok else "fail", returncode=result.returncode,
                       stdout=result.stdout, stderr=result.stderr, timed_out=result.timed_out,
                       elapsed_s=result.elapsed_s)
        if budget is not None:
            budget.checkpoint()
    # Bind the check and later QA verdict to these exact materialized files.
    review_files = sorted(create_ops.parse_files(ctx.read_text("emit.txt")))
    ctx.write_json("focused-source.json", {"root": output, "files": _source_manifest(Path(output)),
                                          "review_files": review_files})
    ctx.write_json("focused-check.json", payload)
    if payload["status"] == "fail":
        detail = f"requested runtime check failed (exit {payload.get('returncode')}): {payload.get('stderr') or payload.get('stdout')}"
        kind = evidence.failure_kind(detail, timed_out=bool(payload.get("timed_out")), returncode=payload.get("returncode"))
        if kind != "code_failure":
            evidence.record(ctx, {**payload, "status": kind})
            evidence.stop_unrepairable(kind, detail)
    if ctx.facts.get("package_path"):
        problems = evidence.current_problems(ctx)
        if problems:
            raise ValueError("; ".join(problems))
    outcome = {"status": "pass" if payload["status"] != "fail" else "code_failure",
               "focused_check": payload}
    if ctx.facts.get("package_path"):
        outcome["smoke"] = ctx.read_json("smoke.json", {})
        outcome["revision"] = ctx.read_json("revision.json", None)
    evidence.record(ctx, outcome)
    return {"focused_check": json.dumps(payload), "output_path": output,
            "review_files": json.dumps(review_files)}


def gate_qa(ctx: Any) -> str:
    qa = ctx.read_json("focused-qa.json", None)
    check = ctx.read_json("focused-check.json", {})
    findings = []
    checked_source = ctx.read_json("focused-source.json", {})
    output = str(ctx.facts.get("artifact") or ctx.facts.get("package_path") or "")
    try:
        unchanged = (checked_source.get("root") == output and
                     checked_source.get("files") == _source_manifest(Path(output)))
    except (OSError, ValueError):
        unchanged = False
    if not unchanged:
        findings.append("focused QA changed the checked output; implementation and runtime checks must repeat within the repair bound")
    if not isinstance(qa, dict):
        findings.append("focused QA did not deliver a JSON report")
    else:
        unavailable = re.search(r"UNAVAILABLE:[^\n\"]*", json.dumps(qa), re.I)
        if unavailable:
            evidence.stop_unrepairable("unavailable", unavailable[0])
        checks = qa.get("checks")
        if not isinstance(checks, list) or not checks:
            findings.append("focused QA recorded no checks")
        else:
            for entry in checks:
                if not isinstance(entry, dict) or not all(entry.get(key) for key in ("name", "command", "status", "evidence")):
                    findings.append("focused QA check lacks its command/status/evidence")
                elif entry["status"] != "pass":
                    findings.append(f"focused QA check did not pass: {entry['name']}")
        if qa.get("status") != "pass":
            findings.append("focused QA did not pass")
        if not isinstance(qa.get("findings"), list) or not isinstance(qa.get("not_run"), list):
            findings.append("focused QA must explicitly list findings and checks not run")
        else:
            findings.extend(str(item) for item in qa["findings"] if str(item).strip())
    if check.get("status") == "fail":
        findings.append(f"requested runtime check failed (exit {check.get('returncode')})")
    # Keep concrete runtime diagnostics ahead of the coverage/freshness findings.
    if check.get("status") == "fail":
        findings[-1] += f": {check.get('stderr') or check.get('stdout')}"
    findings.extend(evidence.current_problems(ctx, ("focused_qa",)))
    findings.extend(evidence.audit_problems(ctx))
    if findings:
        detail = "\n".join(findings)
        evidence.stop_unrepairable(evidence.failure_kind(detail), detail)
        return _repair(ctx, findings)
    ctx.set_fact("qa_passed", True)
    ctx.write_json("fast-report.json", _report(ctx, "qa_passed", []))
    return "proceed"


def smoke(ctx: Any) -> dict[str, Any]:
    budget = getattr(ctx, "budget", None)
    if budget is not None:
        budget.checkpoint_node("final_validation")
    return create_ops.smoke(ctx)


def report_package(ctx: Any) -> dict[str, Any]:
    result = create_ops.write_result(ctx)
    report = _report(ctx, "completed", [])
    ctx.write_json("fast-report.json", report)
    payload = json.loads(result["created"])
    payload["fast"] = report
    ctx.write_json("create.json", payload)
    return {"created": json.dumps(payload)}


def report_artifact(ctx: Any) -> dict[str, Any]:
    if ctx.read_json("focused-check.json", {}).get("status") != "pass":
        raise ValueError("artifact has no passing required runtime check")
    result = design_ops.report_artifact(ctx)
    report = _report(ctx, "completed", [])
    ctx.write_json("fast-report.json", report)
    payload = json.loads(result["designed"])
    payload["fast"] = report
    payload["source_project"] = ctx.facts.get("source_project") or None
    ctx.write_json("design.json", payload)
    return {"designed": json.dumps(payload)}
