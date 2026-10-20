"""Operations for the explicit fast modes; full-mode review is never invoked."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any

from refactory_dsh import create_ops, design_ops, repo_change, sandbox, unbuilt_check, worktree
from refactory_dsh import authoring_evidence as evidence
from refactory_dsh.nn import Verdict

#: A fast mode runs no full review gate or overwatch, so the prompts that show those
#: verdicts say so; "nothing refused yet" would read as a promise of a later review.
FAST_UNREFUSED = {
    "qa_gate": "(fast mode runs no full review gate)",
    "overwatch_review": "(fast mode runs no overwatch audit)",
}

#: Where emit writes when the run copies ``source_project``: the copy is kept as it is,
#: and only the files the change adds or rewrites are written.
COPY_SCOPE = (
    "Paths are relative to the artifact's own directory, which starts as a copy of {source} "
    "(read it there; never write to it). Write only the files this change adds or rewrites, "
    "each as a block introduced by a line of exactly ===FILE: <relative path>=== followed by "
    "its full contents; the rest of the copy is kept as it is. The copy holds: {files}"
)


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


from refactory_dsh.paths import _source_manifest  # moved to paths.py; re-exported for callers  # noqa: E402


def prepare_artifact(ctx: Any) -> dict[str, Any]:
    """design's prepare, without delivery: fast mode has no approval gate to show one at.

    With ``source_project`` the run copies that directory rather than working in a
    worktree (`materialise_artifact`), so it takes none of the knobs that place one.
    """
    task = dict(getattr(ctx, "task", {}) or {})
    source = str(task.get("source_project") or ctx.knob("source_project", "") or "").strip()
    manifest: dict[str, str] = {}
    if source:
        given = [knob for knob in ("target", "base", "setup")
                 if str(task.get(knob) or ctx.knob(knob, "") or "").strip()]
        if given:
            raise ValueError(f"source_project copies a directory into the artifact, and {', '.join(given)} "
                             "place the run in a worktree of a repository instead; pass one or the other")
        root = Path(source).expanduser().resolve()
        if not root.is_dir():
            raise ValueError("source_project must be an existing directory")
        source = str(root)
        manifest = _source_manifest(root)
    # deliver=False tells prepare_design to omit pitfalls facts and writes
    # that this graph does not declare.
    prepared = design_ops.prepare_design(ctx, deliver=False, in_worktree=False if source else None)
    if source:
        prepared.update(source=source,
                        emit_scope=COPY_SCOPE.format(source=source, files=json.dumps(sorted(manifest))))
    ctx.write_text("repair.txt", "(initial implementation; no repair requested)\n")
    ctx.write_json("source-manifest.json", {"source_project": source, "files": manifest})
    return {**prepared, "source_project": source, "source_files": json.dumps(sorted(manifest)),
            **FAST_UNREFUSED, "repair_feedback": "(initial implementation)", "repairs_used": 0}


def materialise_artifact(ctx: Any) -> dict[str, Any]:
    """`repo_change.materialise`; with ``source_project``, onto a copy of it."""
    source = str(ctx.facts.get("source_project") or "")
    if not source:
        return repo_change.materialise(ctx)
    expected = ctx.read_json("source-manifest.json", {}).get("files", {})
    if _source_manifest(Path(source)) != expected:
        raise ValueError("source project changed; restart preparation to invalidate dependent results")
    return repo_change.materialise(ctx, source=Path(source), files=tuple(sorted(expected)))


def _checked_files(root: Path) -> dict[str, str]:
    """What focused QA is bound to: a run worktree's committed attempt, which is what its
    branch delivers, or every file of a package or artifact directory."""
    return evidence.source_files(root) if worktree.is_run_worktree(root) else _source_manifest(root)


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


def _repair(ctx: Any, findings: list[str], *, kind: str = "check") -> str:
    used = int(ctx.facts.get("repairs_used", 0))
    limit = int(ctx.knob("repair_attempts", 1))
    feedback = "\n".join(findings)
    ctx.write_text("repair.txt", feedback + "\n")
    ctx.set_fact("repair_feedback", feedback)

    if kind == "check":
        kind_used = int(ctx.facts.get("check_repairs_used", 0))
        ctx.set_fact("check_repairs_used", kind_used + 1)
    else:
        kind_used = int(ctx.facts.get("review_repairs_used", 0))
        ctx.set_fact("review_repairs_used", kind_used + 1)

    if used >= limit:
        ctx.write_json("fast-report.json", _report(ctx, "unresolved", findings))
        label = "check" if kind == "check" else "review"
        raise ValueError(
            f"focused QA/admission unresolved after {used} repair(s) "
            f"({label} failure): {feedback}"
        )
    ctx.set_fact("repairs_used", used + 1)
    ctx.write_json("fast-report.json", _report(ctx, "repairing", findings))
    return Verdict("reloop", feedback)


def gate_emit(ctx: Any) -> str:
    """Send an emission materialise could not apply back to emit (#330 E5).

    A refused emission is not a code failure, so it does not spend a repair attempt.
    The gate's own max_iterations still bounds retries.
    """
    refused = repo_change.refused(ctx)
    if not refused:
        return "proceed"
    detail = f"materialise could not apply emit.txt, so nothing was checked: {refused}"
    ctx.set_fact("repair_feedback", detail)
    return Verdict("reloop", detail)


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
        if ctx.facts.get("artifact"):
            # Where design checks: the run's worktree, in its own environment, or the
            # artifact directory.
            result = repo_change.run_check(ctx, command, timeout)
        else:
            # An artifact built for refactory: its check imports refactory_dsh.
            result = sandbox.run_command(command, workdir=Path(output), timeout_s=timeout,
                                         budget=budget, env=sandbox.refactory_env(), exact_env=True)
        payload.update(status="pass" if result.ok else "fail", returncode=result.returncode,
                       stdout=result.stdout, stderr=result.stderr, timed_out=result.timed_out,
                       elapsed_s=result.elapsed_s)
        if budget is not None:
            budget.checkpoint()
    # Bind the check and later QA verdict to these exact materialized files: in a worktree,
    # its committed attempt, and every file its branch changes.
    if worktree.recorded(ctx.workdir) is not None:
        review_files = sorted(ctx.facts.get("files") or [])
    else:
        review_files = sorted(create_ops.parse_files(ctx.read_text("emit.txt")))
    ctx.write_json("focused-source.json", {"root": output, "files": _checked_files(Path(output)),
                                          "review_files": review_files})
    ctx.write_json("focused-check.json", payload)

    from refactory_dsh import base_check
    guardedness = None
    if ctx.facts.get("artifact") and worktree.recorded(ctx.workdir) is not None:
        ctx.set_fact("inherited_failures", [])
        try:
            guardedness = base_check.compute_test_guardedness(
                ctx, command, float(ctx.knob("check_timeout_s", 60)),
            )
        except Exception as exc:  # evidence, not a gate: say why it is missing
            guardedness = {"unavailable": f"the new tests could not be run on the base: {exc}"}
        if guardedness is not None:
            ctx.write_json("test-guardedness.json", guardedness)
    if payload["status"] == "fail":
        detail = f"requested runtime check failed (exit {payload.get('returncode')}): {payload.get('stderr') or payload.get('stdout')}"
        kind = evidence.check_failure_kind(result, budget=getattr(ctx, "budget", None))
        if kind != "code_failure":
            evidence.record(ctx, {**payload, "status": kind})
            evidence.stop_unrepairable(kind, detail)

        # A failure the unchanged base has too is not the attempt's.
        found = worktree.recorded(ctx.workdir) if ctx.facts.get("artifact") else None
        if found is not None:
            filtered = base_check.filter_check(
                ctx, result, command,
                float(ctx.knob("check_timeout_s", 60)),
                guardedness=guardedness,
            )
            if filtered.base_environment_failure:
                evidence.stop_unrepairable(
                    "unavailable",
                    f"the base check could not run: {filtered.base_environment_failure}",
                )

            # Diagnose unbuilt packages from the worktree's own manifests.
            wt_path = Path(found.path)
            unbuilt = unbuilt_check.scan_unbuilt_packages(wt_path, found.base, found.head)
            unbuilt_msg = unbuilt_check.format_unbuilt_diagnosis(unbuilt) if unbuilt else ""

            if filtered.all_excused:
                payload["status"] = "pass"
                payload["inherited_failures"] = sorted(filtered.inherited)
                ctx.set_fact("inherited_failures", sorted(filtered.inherited))
                if unbuilt_msg:
                    ctx.write_text("check.txt", f"(the check passed; inherited failures excused)\n\n{unbuilt_msg}\n")
                ctx.write_json("focused-check.json", payload)
            else:
                # New failures exist — wire unbuilt diagnosis the same way
                # as design_ops.check_artifact does.
                if filtered.new_failures and filtered.inherited:
                    detail = filtered.new_failure_detail
                if filtered.inherited_detail:
                    inherited_part = filtered.inherited_detail
                    if unbuilt_msg:
                        inherited_part = inherited_part + "\n\n" + unbuilt_msg
                    ctx.write_text("check.txt", detail + "\n\n" + inherited_part + "\n")
                elif unbuilt_msg:
                    ctx.write_text("check.txt", detail + "\n\n" + unbuilt_msg + "\n")
                # When every new failure names an exact missing entry-point
                # path, stop without spending a builder attempt.
                if unbuilt and unbuilt_check.failures_explained_by_unbuilt(detail, unbuilt):
                    raise ValueError(
                        f"check failed because of unbuilt packages; code repair was not attempted.\n{unbuilt_msg}"
                    )
                # Otherwise store the diagnosis so gate_qa prepends it to
                # the builder's feedback.
                if unbuilt_msg:
                    payload["unbuilt_diagnosis"] = unbuilt_msg
                ctx.write_json("focused-check.json", payload)
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
                     checked_source.get("files") == _checked_files(Path(output)))
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
    check_failed = check.get("status") == "fail"
    if check_failed:
        check_output = check.get("stderr") or check.get("stdout") or ""
        findings.append(f"requested runtime check failed (exit {check.get('returncode')}): {check_output}")
    findings.extend(evidence.current_problems(ctx, ("focused_qa",)))
    findings.extend(evidence.audit_problems(ctx))
    # Prepend unbuilt-package diagnosis so the builder sees it first.
    unbuilt_diag = check.get("unbuilt_diagnosis", "")
    if unbuilt_diag:
        findings.insert(0, unbuilt_diag)
    if findings:
        # Classify only refactory's own text; check stderr/stdout is target text
        # and must not be word-scanned (correction 1: gate_qa classifies target text).
        if check_failed:
            refactory_findings = [
                f for f in findings
                if not f.startswith("requested runtime check failed")
            ]
        else:
            refactory_findings = findings
        if refactory_findings:
            refactory_detail = "\n".join(refactory_findings)
            evidence.stop_unrepairable(evidence.failure_kind(refactory_detail), refactory_detail)
        return _repair(ctx, findings, kind='check' if check_failed else 'review')
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
