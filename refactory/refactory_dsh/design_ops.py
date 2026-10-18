"""The operations design-v2's graph calls.

`create` emits a **package of modes**: a manifest, a `Mode` subclass, `score()`.
`design` emits **software**: no manifest, no `score()`, no graph. So the validator
and the check differ, and so does where the output lands.

Where the software lands, and how a check runs there, is `repo_change`'s: design uses
the same steps as any mode that changes a repository. What is design's own is here: its
brief, how its work is delivered (`delivery`), its check gate's evidence and repairs, and
its report.
"""

from __future__ import annotations

import json
import re as _re
from pathlib import Path
from typing import Any

from refactory_dsh import delivery, repo_change, sandbox, worktree
from refactory_dsh.nn import Verdict

_FILE_EXT_RE = _re.compile(
    r'\b[\w/.-]+\.(?:py|ts|js|mjs|cjs|jsx|tsx|json|md|yaml|yml|toml|go|rs|rb|java|c|cpp|h|sh|css|html)\b'
)

STAGING = "staging"

#: Planning steps that must not change the target or the worktree (#347).
#: They write only into the run directory; a change they make to the target
#: or the worktree fails the step and names what changed.
PLANNING_STEPS = ('research_director', 'strategy_director', 'synthesize_strategy', 'design_doc')

#: The op design's materialise node called before it moved to `repo_change` (#330): a run
#: lowered then, such as one waiting at its approval gate, still names it.
materialise_artifact = repo_change.materialise


def prepare_design(ctx: Any, *, deliver: bool = True, in_worktree: bool | None = None) -> dict[str, Any]:
    """Read the brief, and what the brief says counts as done.

    Where the run works -- a worktree of its target unless its ``worktree`` knob is off,
    or ``in_worktree`` says otherwise -- is `repo_change.locate`'s. With ``deliver``, how
    its work is delivered is planned here (`delivery.plan_run`), for the approval gate to
    show. design_fast passes ``deliver=False``: it has no approval gate to show one at,
    so it delivers nothing and refuses ``refine``.
    """
    task = dict(getattr(ctx, "task", {}) or {})
    # The walker passes these in the run's task; a run on DSH carries them as knobs.
    name = str(task.get("name") or ctx.knob("name", "") or "").strip()
    brief = str(task.get("brief") or ctx.knob("brief", "") or "").strip()
    check = str(task.get("check") or ctx.knob("check", "") or "").strip()
    from refactory_dsh.brief_guard import require_brief
    require_brief(brief, source="brief")
    if not name and brief:
        from refactory_dsh.create_ops import _slug

        name = _slug(brief)
    if not name:
        raise ValueError(
            "design-mode needs what to build: pass {'name': ..., 'brief': ..., 'check': ...} "
            "in the task (the walker) or the `name`/`brief`/`check` knobs (a run on DSH)"
        )
    if not check:
        raise ValueError(
            "design-mode needs a check: the command that decides whether the artifact "
            "works, e.g. {'check': 'python -m pytest -q'}"
        )
    if in_worktree is None:
        in_worktree = repo_change.wants_worktree(ctx)
    if not deliver and str(task.get("refine") or "").strip():
        raise ValueError("refine follows up a pull request, and design_fast delivers nothing, so it cannot "
                         "update one; follow it up with design instead")
    if deliver and in_worktree:
        # A refinement starts from its pull request's head, in a clone of its repository.
        task = delivery.refine_task(ctx, task)
    located = repo_change.locate(ctx, name, task, in_worktree=in_worktree,
                                 needs_worktree=("refine",) if deliver else (),
                                 output_only=PLANNING_STEPS)

    # Compute research_scope: 'scoped' for --refine or briefs naming files.
    if str(task.get("refine") or "").strip():
        research_scope = "scoped"
    elif _FILE_EXT_RE.search(brief):
        research_scope = "scoped"
    else:
        research_scope = "full"

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
    ctx.write_text("reviews/design-review-feedback.txt",
                   "not run: initial design pass; no prior review\n")
    from refactory_dsh.create_ops import UNREFUSED

    planned = delivery.plan_run(ctx, task=task) if deliver else {}
    return {"name": name, "brief": brief, "check": check, "validation": "(no check has run yet)",
            "research_review": "(no research review has run yet)", "research_scope": research_scope,
            "design_review_feedback": "(no design review has run yet)",
            "design_review_findings": "(no findings yet)",
            **UNREFUSED, **located, **planned}


def gate_design_review(ctx: Any) -> str:
    """Proceed when the design review passes and the mechanical checks hold.

    Never raises ValueError: when attempts are exhausted, it proceeds to
    gate_strategy with the remaining findings in the design_review_findings
    fact, so the person decides.
    """
    from refactory_dsh.create_ops import PASS_MARK, blocking_findings
    from refactory_dsh.nn import Verdict

    # ── read the agent's review ──
    review_text = ctx.read_text("reviews/design-review.md") if ctx.exists(
        "reviews/design-review.md") else ""
    problems: list[str] = []

    # Verdict and blocking findings, same parsing as gate_overwatch.
    if review_text.strip():
        verdict_line = review_text.strip().splitlines()[-1].strip()
        problems.extend(blocking_findings(review_text))
        if verdict_line != PASS_MARK:
            problems.append(f"the review did not state {PASS_MARK!r}; "
                            f"its last line is: {verdict_line!r}")
    else:
        problems.append("reviews/design-review.md is empty or missing")

    # ── mechanical check 1: cited file:line references exist ──
    design_text = ctx.read_text("design.md") if ctx.exists("design.md") else ""
    source = str(ctx.facts.get("source") or ctx.facts.get("artifact") or "")

    # Build an index of tracked files for bare-name resolution.
    tracked_files: list[str] = []
    if source:
        try:
            output = worktree.git(Path(source), "ls-files")
            tracked_files = [f for f in output.splitlines() if f.strip()]
        except ValueError:
            pass

    def _resolve_citation(file_path: str) -> Path | None:
        """Resolve a citation path: try direct, then match as a suffix of tracked files."""
        full = Path(source) / file_path
        if full.is_file():
            return full
        # Bare or partial path: find a tracked file whose path ends with it.
        matches = []
        for tracked in tracked_files:
            if tracked == file_path or tracked.endswith("/" + file_path):
                candidate = Path(source) / tracked
                if candidate.is_file():
                    matches.append(candidate)
        return matches[0] if len(matches) == 1 else None

    if design_text and source:
        # Match path.ext:N or path.ext:N-M  (the format design_doc is told to use)
        for match in _re.finditer(
            r'(?<!\w)([A-Za-z_][\w/._-]*\.\w+):(\d+)(?:-(\d+))?(?!\w)',
            design_text
        ):
            # Skip URLs: a "://" in the 10 chars before the match means this is
            # a hostname:port, not a file:line citation.
            prefix = design_text[max(0, match.start() - 10):match.start()]
            if '://' in prefix:
                continue
            file_path = match.group(1)
            start_s, end_s = match.group(2), match.group(3)
            line_num = int(end_s) if end_s else int(start_s)
            resolved = _resolve_citation(file_path)
            if resolved is None:
                continue
            else:
                actual_lines = resolved.read_text().splitlines()
                if line_num > len(actual_lines):
                    problems.append(
                        f"citation {match.group(0)!r}: {file_path} has "
                        f"{len(actual_lines)} lines, not {line_num}"
                    )

    # ── mechanical check 2: brief quotations are verbatim ──
    if design_text:
        brief_text = ""
        if ctx.exists("brief.json"):
            try:
                brief_data = json.loads(ctx.read_text("brief.json"))
                brief_text = str(brief_data.get("brief", ""))
            except (json.JSONDecodeError, AttributeError):
                pass
        if brief_text:
            # Collapse whitespace for comparison so wrapped quotes still match.
            brief_collapsed = _re.sub(r'\s+', ' ', brief_text).strip()
            for match in _re.finditer(
                r'(?i)(?:\bthe brief\b\s+(?:says|asks|requires|reads)[,:]?\s*'
                r'|\bthe brief:\s*)'
                r'"([^"]{4,})"',
                design_text,
            ):
                quoted = match.group(1)
                quoted_collapsed = _re.sub(r'\s+', ' ', quoted).strip()
                if quoted_collapsed not in brief_collapsed:
                    problems.append(
                        f"the design attributes {quoted!r} to the brief, but "
                        f"that text does not appear verbatim in the brief"
                    )

    # ── mechanical check 3: design-document contract ──
    from refactory_dsh import design_contract

    if design_text:
        run_name = str(ctx.facts.get("name") or "")
        parsed = design_contract.parse(design_text, run_name)
        missing = design_contract.missing_parts(parsed)
        if missing:
            problems.append(
                f"the design is missing required section(s): {', '.join(missing)}"
            )
        left_no_reason = design_contract.left_without_reason(parsed)
        if left_no_reason:
            problems.append(
                "rule-site(s) marked [left] without a reason: "
                + ", ".join(left_no_reason)
            )
        unmarked = design_contract.unmarked_sites(parsed)
        if unmarked:
            problems.append(
                "rule-site(s) with no [changed] or [left: <reason>] marker: "
                + ", ".join(unmarked)
            )
        if design_contract.title_is_run_name(design_text, run_name):
            problems.append(
                "the design's title is the run name; it must say what the change does"
            )

    # ── decide: proceed, reloop, or exhaust ──
    allowed = int(ctx.knob("design_review_attempts", 2))
    used = int(ctx.facts.get("design_review_attempts_used", 0)) + 1
    ctx.set_fact("design_review_attempts_used", used)

    if not problems:
        ctx.write_text("reviews/design-review-feedback.txt",
                       "the design review passed\n")
        ctx.set_fact("design_review_feedback", "(the design review passed)")
        ctx.set_fact("design_review_findings",
                     "(none: the design review passed)")
        return "proceed"

    report = "\n".join(f"- {p}" for p in problems)

    if used >= allowed:
        # Exhausted: proceed to gate_strategy with findings shown.
        ctx.set_fact("design_review_feedback", report)
        ctx.set_fact("design_review_findings", report)
        ctx.write_text("reviews/design-review-feedback.txt", report + "\n")
        return "proceed"

    # Reloop to design_doc with the findings.
    ctx.write_text("reviews/design-review-feedback.txt", report + "\n")
    ctx.set_fact("design_review_feedback", report)
    ctx.set_fact("design_review_findings",
                 "(no findings yet)")
    return Verdict("reloop", report)


def check_artifact(ctx: Any) -> str:
    """Gate: run the brief's check, in a sandbox, and refuse what does not pass.

    This is the design-time equivalent of the package gate: a package must import and
    score, an artifact must build and run. Both refuse rather than warn, because an
    artifact nobody checked is not a deliverable.

    The check runs where the run works (`repo_change.run_check`). An emission materialise
    could not apply is sent back to emit with the reason, as a failed check is, rather
    than ending the run (#330 E5).
    """
    from refactory_dsh import authoring_evidence as evidence
    from refactory_dsh.create_ops import repair

    refused = repo_change.refused(ctx)
    if refused:
        # Nothing was materialised, so there is nothing to record runtime evidence about;
        # a refused emission is not a code failure, so it does not spend an attempt.
        detail = f"materialise could not apply emit.txt, so nothing was checked: {refused}"
        ctx.write_text("check.txt", detail + "\n")
        ctx.set_fact("validation", detail)
        return Verdict("reloop", detail)
    command = str(ctx.facts["check"])
    result = repo_change.run_check(ctx, command, int(ctx.knob("check_timeout_s", 180)))
    detail = "\n".join((result.problem(), result.stdout or "", result.stderr or "")).strip()
    outcome = {"command": command, "status": "pass" if result.ok else evidence.failure_kind(
        detail, timed_out=result.timed_out, returncode=result.returncode),
        "stdout": result.stdout, "stderr": result.stderr, "returncode": result.returncode,
        "timed_out": result.timed_out, "elapsed_s": result.elapsed_s}
    ctx.write_text("check.txt", detail or "ok\n")

    # Whether the new tests fail without the change is evidence for the reviewers, passing
    # or not; recorded before the receipt, which hashes it.
    from refactory_dsh import base_check
    guardedness = None
    if worktree.recorded(ctx.workdir) is not None:
        ctx.set_fact("inherited_failures", [])
        try:
            guardedness = base_check.compute_test_guardedness(
                ctx, command, int(ctx.knob("check_timeout_s", 180)),
            )
        except Exception as exc:  # evidence, not a gate: say why it is missing
            guardedness = {"unavailable": f"the new tests could not be run on the base: {exc}"}
        if guardedness is not None:
            ctx.write_json("test-guardedness.json", guardedness)
    evidence.record(ctx, outcome)

    if result.ok:
        ctx.set_fact("validation", "(the check passed)")
        return "proceed"
    evidence.stop_unrepairable(outcome["status"], detail)

    # A failure the unchanged base has too is not the attempt's (`base_check.compare_failures`).
    if worktree.recorded(ctx.workdir) is not None:
        filtered = base_check.filter_check(
            ctx, result, command, int(ctx.knob("check_timeout_s", 180)),
            guardedness=guardedness,
        )
        if filtered.base_environment_failure:
            evidence.stop_unrepairable(
                "unavailable",
                f"the base check could not run: {filtered.base_environment_failure}",
            )
        if filtered.all_excused:
            validation_msg = f"(the check passed; {filtered.inherited_detail})"
            ctx.set_fact("validation", validation_msg)
            ctx.write_text("check.txt", validation_msg + "\n")
            # The receipt the reviews and the audit check is this one, which passed.
            outcome["status"] = "pass"
            outcome["inherited_failures"] = sorted(filtered.inherited)
            ctx.set_fact("inherited_failures", sorted(filtered.inherited))
            evidence.record(ctx, outcome)
            return "proceed"
        if filtered.new_failures and filtered.inherited:
            detail = filtered.new_failure_detail
        if filtered.inherited_detail:
            ctx.write_text("check.txt", detail + "\n\n" + filtered.inherited_detail + "\n")

    return repair(ctx, [detail])


def report_artifact(ctx: Any) -> dict[str, Any]:
    """Say what was produced, so a run's output is discoverable rather than inferred."""
    from refactory_dsh.authoring_evidence import require_completion

    if worktree.recorded(ctx.workdir) is not None:
        # Where the worktree stands first, so a moved HEAD is named as such and not as a
        # stale sign-off.
        worktree.require_current(ctx.workdir)
    require_completion(ctx)
    payload = repo_change.report(ctx, "design.json", {
        "name": ctx.facts.get("name"),
        "artifact": ctx.facts.get("artifact"),
        "files": ctx.facts.get("files", []),
        "check": ctx.facts.get("check"),
        "checked": True,
        "validation_evidence": ctx.read_json("authoring-evidence.json", None),
        "intent_audit": ctx.read_json("intent-audit.json", None),
    })
    return {"designed": json.dumps(payload, default=str)}
