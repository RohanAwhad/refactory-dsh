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

from refactory_dsh import delivery, repo_change, sandbox, unbuilt_check, worktree
from refactory_dsh.invocations import CHECK_PREP, CHECK_VERDICT, SELF_CHECK_RECORD
from refactory_dsh.nn import Verdict

#: The reason prepare_design writes when the run is a refinement.  classify_scale
#: recognises a refine by this constant rather than reading research_scope (which
#: classify_scale itself writes), so a rerun reclassifies from the footprint.
REFINE_SCOPE_REASON = "refinement of an open pull request"

STAGING = "staging"

#: Planning steps that must not change the target or the worktree (#347).
#: They write only into the run directory; a change they make to the target
#: or the worktree fails the step and names what changed.
PLANNING_STEPS = ('research_director', 'pitfalls_researcher', 'strategy_director', 'synthesize_strategy', 'design_doc')

#: The op design's materialise node called before it moved to `repo_change` (#330): a run
#: lowered then, such as one waiting at its approval gate, still names it.
materialise_artifact = repo_change.materialise


def noop(ctx: Any) -> dict[str, Any]:
    """Terminal node: does nothing, returns an empty dict."""
    return {}


def pick_gate_corrections(ctx: Any) -> str:
    """Gate evaluator: extract gate_strategy corrections from the intent ledger.

    Returns ``"proceed"`` when corrections exist (route to apply_corrections),
    or ``"skip"`` when there are none (route directly to emit, skipping the
    agent entirely).

    Reads the ledger only when it exists; a missing ledger means "no corrections".
    Only correction or constraint entries with product scope and user-relayed
    source, between a gate_strategy proceed verdict and the next gate-visit or
    verdict-use, are gate corrections.
    """
    from refactory_dsh.steering import LEDGER, parse_entries

    ledger_path = Path(ctx.workdir) / LEDGER
    if not ledger_path.exists():
        ctx.set_fact("gate_corrections", "")
        ctx.set_fact("design_corrections_applied", "0 corrections (no ledger)")
        return "skip"

    text = ledger_path.read_text()
    entries = parse_entries(text)
    corrections: list[str] = []

    collecting = False
    for entry in entries:
        kind = entry.get("kind", "")

        # A verdict for gate_strategy=proceed starts collecting;
        # any other verdict resets.
        if kind == "verdict":
            gate_match = _re.search(r'^gate:\s*(.+)$', entry["text"], _re.MULTILINE)
            verdict_match = _re.search(r'^verdict:\s*(.+)$', entry["text"], _re.MULTILINE)
            if gate_match and verdict_match:
                gate = gate_match.group(1).strip()
                verdict = verdict_match.group(1).strip().lower()
                collecting = (gate == "gate_strategy" and verdict == "proceed")
            continue

        # A gate-visit or verdict-use stops collecting.
        if kind in ("gate-visit", "verdict-use"):
            collecting = False
            continue

        # While collecting, pick up corrections/constraints with product scope
        # and user-relayed source.
        if collecting and kind in ("correction", "constraint"):
            if entry.get("scope") == "product" and entry.get("source") == "user-relayed":
                corrections.append(entry["text"])

    if not corrections:
        ctx.set_fact("gate_corrections", "")
        ctx.set_fact("design_corrections_applied", "0 corrections")
        return "skip"

    joined = "\n\n".join(f"- {c}" for c in corrections)
    ctx.set_fact("gate_corrections", joined)
    ctx.set_fact("design_corrections_applied", f"{len(corrections)} correction(s)")

    # Back up design.md before corrections are applied (gate 5c).
    design_path = Path(ctx.workdir) / "design.md"
    if design_path.exists():
        (Path(ctx.workdir) / "design.md.pre_corrections").write_text(design_path.read_text())

    return "proceed"


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

    # Compute research_scope.  A refine is always scoped; everything else starts
    # as full and is reclassified by classify_scale after research.
    if str(task.get("refine") or "").strip():
        research_scope = "scoped"
        research_scope_reason = REFINE_SCOPE_REASON
    else:
        research_scope = "full"
        research_scope_reason = ""

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
    # Write initial check-record.json so emit finds its input
    ctx.write_json("check-record.json", {"status": "not_run", "validation": "(no check has run yet)"})
    for feedback in ("research-review.txt", "check.txt"):
        ctx.write_text(feedback, "not run: initial authoring pass; no prior feedback\n")
    ctx.write_text("reviews/design-review-feedback.txt",
                   "not run: initial design pass; no prior review\n")
    from refactory_dsh.create_ops import UNREFUSED

    # Pitfalls facts and file are written only by full design mode (whose
    # prepare node declares them); design_fast calls with deliver=False and
    # filters these keys out before returning to the executor.
    if deliver:
        ctx.write_text("pitfalls-review.txt",
                       "not run: initial pitfalls analysis; no prior review\n")
    planned = delivery.plan_run(ctx, task=task) if deliver else {}
    pitfalls_facts = {
        "has_pitfalls_step": True,
        "pitfalls": "(no pitfalls analysis yet)",
        "pitfalls_review": "(no pitfalls review has run yet)",
    } if deliver else {}
    # Write feedback-initial.json so a redo before QA re-fills
    feedback_initial = {
        "validation": "(no check has run yet)",
        **UNREFUSED,
    }
    ctx.write_json("feedback-initial.json", feedback_initial)
    # ── deliverable_kind: repository change vs artifact ──
    has_target = bool(str(task.get("target") or ctx.knob("target", "") or "").strip())
    has_refine = bool(str(task.get("refine") or ctx.knob("refine", "") or "").strip())
    has_source_project = bool(str(task.get("source_project") or ctx.knob("source_project", "") or "").strip())
    deliverable_kind = "repository_change" if (has_target or has_refine or has_source_project) else "artifact"
    from refactory_dsh.authoring_evidence import (
        REPO_CHANGE_REVIEW_INSTRUCTIONS,
        ARTIFACT_REVIEW_INSTRUCTIONS,
    )
    review_instructions = (
        REPO_CHANGE_REVIEW_INSTRUCTIONS if deliverable_kind == "repository_change"
        else ARTIFACT_REVIEW_INSTRUCTIONS
    )
    from refactory_dsh.verification import verification_methods

    claims_facts = {
        "claims_check_feedback": "",
        "design_review_status": "",
    } if deliver else {}
    return {"name": name, "brief": brief, "check": check, "validation": "(no check has run yet)",
            "research_review": "(no research review has run yet)",
            "research_scope": research_scope, "research_scope_reason": research_scope_reason,
            "design_review_feedback": "(no design review has run yet)",
            "design_review_findings": "(no findings yet)",
            "deliverable_kind": deliverable_kind,
            "review_instructions": review_instructions,
            "verification_methods": verification_methods(deliverable_kind),
            **pitfalls_facts,
            **claims_facts,
            **UNREFUSED, **located, **planned}


#: What a scoped change's plan is compiled from, in place of the perspectives step.
SCOPED_STRATEGY_PLAN = (
    "(no perspectives: this is a scoped change, so plan directly from the research and "
    "the pitfalls — the simplest thing that could pass the check — and say what the "
    "check will not catch)"
)


#: What a light refine's plan tells design_doc.
LIGHT_REFINE_STRATEGY_PLAN = (
    "This is a light refine — a follow-up to an existing pull request. The brief "
    "describes exactly what to fix. Below are the PR description, the diff stat and "
    "the changed-file list against the PR's base so you know what the change already "
    "does. Plan exactly the requested fixes and nothing else. Keep every part of the "
    "existing change that the brief does not ask to modify. Do not redesign what "
    "already works."
)


def route_refine_design(ctx: Any) -> Any:
    """Skip research and strategy on a light refine; a full refine or a non-refine walks them."""
    from refactory_dsh.nn import Verdict

    # A non-refine run always takes the full path, regardless of the knob.
    if ctx.facts.get("research_scope_reason") != REFINE_SCOPE_REASON:
        return Verdict("proceed", "not a refine: walking the full design phase")

    # A refine with the full knob walks the full path too.
    if ctx.knob("refine_design", "light") == "full":
        return Verdict("proceed", "refine_design=full: walking the full design phase")

    # ── light path: write sentinel facts and files ──
    # Retrieve the PR body from delivery_plan.
    plan = ctx.facts.get("delivery_plan") or {}
    refining = plan.get("refine") or plan.get("inputs", {}).get("refine") or {}
    if isinstance(plan, dict):
        # The refining dict may be nested under 'inputs' or directly on the plan.
        for key in ("refine", "inputs"):
            candidate = plan.get(key)
            if isinstance(candidate, dict) and "body" in candidate:
                refining = candidate
                break
    pr_body = str(refining.get("body") or "(no pull request description)")
    pr_into = str(refining.get("into") or "main")

    # Get diff stat and changed files from the worktree.
    diff_stat = "(diff stat unavailable)"
    changed_files = "(changed files unavailable)"
    try:
        wt = worktree.recorded(ctx.workdir)
        if wt is not None:
            wt_path = Path(wt.path)
            # Use merge-base to find the right base for the diff.
            try:
                merge_base = worktree.git(wt_path, "merge-base", f"origin/{pr_into}", "HEAD").strip()
            except (ValueError, OSError):
                merge_base = wt.base
            diff_stat = worktree.git(wt_path, "diff", "--stat", merge_base).strip() or "(empty diff)"
            changed_files = worktree.git(wt_path, "diff", "--name-only", merge_base).strip() or "(no files changed)"
    except Exception:
        pass

    # Build the strategy context block.
    strategy_block = (
        f"{LIGHT_REFINE_STRATEGY_PLAN}\n\n"
        f"## Pull request description\n{pr_body}\n\n"
        f"## Diff stat against the PR's base\n```\n{diff_stat}\n```\n\n"
        f"## Changed files\n```\n{changed_files}\n```"
    )

    # Write sentinel facts.
    ctx.set_fact("research", "(light refine: research was skipped)")
    ctx.set_fact("findings_recorded", "")
    ctx.set_fact("research_review", "(light refine: research review was skipped)")
    ctx.set_fact("research_attempts_used", 0)
    ctx.set_fact("research_advisory", "")
    ctx.set_fact("pitfalls",
                 "(Light refine: the pitfalls analysis was skipped. Check the change "
                 "against the brief's requirements and general code quality instead.)")
    ctx.set_fact("pitfalls_review", "(light refine: pitfalls review was skipped)")
    ctx.set_fact("pitfalls_attempts_used", 0)
    ctx.set_fact("pitfalls_advisory", "")
    ctx.set_fact("strategy_plan", LIGHT_REFINE_STRATEGY_PLAN)
    ctx.set_fact("strategy", strategy_block)

    # Write sentinel files.
    ctx.write_text("research-plan.json", "[]\n")
    ctx.write_text("pitfalls.json", "[]\n")
    ctx.write_text("pitfalls.md", "")
    ctx.write_text("strategy-plan.json", "[]\n")
    ctx.write_text("strategy.json", json.dumps({"strategy": strategy_block}, indent=2) + "\n")
    ctx.write_text("research-review.txt", "(light refine: research was skipped)\n")
    ctx.write_text("pitfalls-review.txt", "(light refine: pitfalls review was skipped)\n")
    ctx.path("research").mkdir(parents=True, exist_ok=True)
    ctx.path("strategy").mkdir(parents=True, exist_ok=True)

    return Verdict("light", "light refine: skipping research and strategy")


def route_strategy(ctx: Any) -> Any:
    """Send a scoped change straight to the plan; a full design weighs perspectives first.

    The perspectives step chooses between ways to build the artifact. A scoped change
    has one sensible way, and one perspective summarised by the plan step is the same
    work done twice.
    """
    from refactory_dsh.nn import Verdict

    if ctx.facts.get("research_scope") == "scoped":
        ctx.set_fact("strategy_plan", SCOPED_STRATEGY_PLAN)
        # What the perspectives step would leave, empty: the plan and design steps
        # declare them as inputs, and the spine refuses a declared input that is not
        # on disk.
        ctx.write_text("strategy-plan.json", "[]\n")
        ctx.path("strategy").mkdir(parents=True, exist_ok=True)
        return Verdict("scoped", "a scoped change is planned straight from the research")
    # `proceed` rather than a label of its own: a person's skip of this gate sends
    # proceed on the spine, and the full path is the one that loses nothing.
    return Verdict("proceed", "a full design weighs perspectives before the plan")


def gate_design_review(ctx: Any) -> str:
    """Send a design back only when the build cannot proceed without a rewrite.

    One design pass, with no reviewer between the design and the person's gate: a
    design goes back to design_doc only when its ``## Changes`` section is missing or
    empty, at most ``design_review_attempts`` times (default 1). Everything else is
    advisory and is shown on the approval gate's card (`design_review_status`): the
    claims check's unverified claims and notes (`design_claims.check_design_claims`), and
    this gate's own checks -- the light template's other required sections, citations,
    quotations of the brief, and the optional sections a design has (rule-site markers,
    pitfall answers). An optional section a design leaves out is not checked; for a
    design without ``## Anti-patterns to avoid`` the card names the high-risk pitfalls
    that leaves unchecked. Never reads a review, and never fails the run.
    """
    from refactory_dsh import design_contract
    from refactory_dsh.nn import Verdict

    # Only what the build cannot proceed without; it is the one thing that costs a round.
    problems: list[str] = []
    # What the gate notices that the person should see. It never costs a round.
    advisories: list[str] = []

    design_text = ctx.read_text("design.md") if ctx.exists("design.md") else ""
    source = str(ctx.facts.get("source") or ctx.facts.get("artifact") or "")

    # ── what blocks: the section the build needs ──
    missing_build = design_contract.missing_sections(design_text)
    if missing_build:
        problems.append(
            "the design is missing the section the build needs, or it is empty: "
            + ", ".join(f"## {section}" for section in missing_build))

    # ── advisory 1: cited file:line references exist ──
    if design_text and source:
        tracked = tracked_files(source)
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
            resolved = resolve_cited(source, file_path, tracked)
            if resolved is None:
                continue
            try:
                actual_lines = (Path(source) / resolved).read_text().splitlines()
            except (OSError, UnicodeDecodeError):
                continue
            if line_num > len(actual_lines):
                advisories.append(
                    f"citation {match.group(0)!r}: {file_path} has "
                    f"{len(actual_lines)} lines, not {line_num}"
                )

    # ── advisory 2: brief quotations are verbatim ──
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
                    # Differing only in punctuation, case, spacing or markdown is a
                    # quoting slip, not a claim the brief does not make.
                    quoted_words = _quote_words(quoted)
                    if quoted_words and f" {quoted_words} " in f" {_quote_words(brief_text)} ":
                        advisories.append(
                            f"the design's quote {quoted!r} differs from the brief only "
                            f"in punctuation or formatting"
                        )
                    else:
                        advisories.append(
                            f"the design attributes {quoted!r} to the brief, but "
                            f"that text does not appear verbatim in the brief"
                        )

    # ── advisory 3: the rest of the light template, and the optional sections present ──
    if design_text:
        run_name = str(ctx.facts.get("name") or "")
        parsed = design_contract.parse(design_text, run_name)
        missing = [section for section in design_contract.missing_sections(
            design_text, design_contract.REQUIRED_SECTIONS)
            if section not in design_contract.BUILD_SECTIONS]
        if missing:
            advisories.append(
                f"the design is missing section(s): {', '.join(missing)}"
            )
        left_no_reason = design_contract.left_without_reason(parsed)
        if left_no_reason:
            advisories.append(
                "rule-site(s) marked [left] without a reason: "
                + ", ".join(left_no_reason)
            )
        unmarked = design_contract.unmarked_sites(parsed)
        if unmarked:
            # Check for near-miss markers on each unmarked site.
            parts: list[str] = []
            for site in parsed.rule_sites:
                if site["status"] != "unmarked":
                    continue
                near = design_contract.find_near_miss_markers(site["path"])
                if near:
                    parts.append(f"{site['path']} ({'; '.join(near)})")
                else:
                    parts.append(site["path"])
            advisories.append(
                "rule-site(s) with no [changed] or [left: <reason>] marker: "
                + ", ".join(parts)
            )
        if design_contract.title_is_run_name(design_text, run_name):
            advisories.append(
                "the design's title is the run name; it must say what the change does"
            )

    # ── advisory 4: pitfall coverage, when the design answers pitfalls ──
    has_pitfalls = ctx.facts.get("has_pitfalls_step", False)
    answers_pitfalls = not design_contract.missing_sections(
        design_text, (design_contract.ANTI_PATTERNS,))
    if has_pitfalls and not answers_pitfalls:
        # An optional section: a design without it is not checked against the pitfalls,
        # and the card says which high-risk pitfalls that leaves unchecked.
        ctx.set_fact("pitfall_coverage",
                     "pitfall coverage not checked: the design has no "
                     "## Anti-patterns to avoid section (optional)")
        unchecked = _high_risk_pitfalls(ctx)
        if unchecked:
            advisories.append(
                "high-risk pitfall(s) not checked: the design has no "
                f"## Anti-patterns to avoid section: {', '.join(unchecked)}")
    elif has_pitfalls:
        from refactory_dsh import pitfalls as pitfalls_mod

        if ctx.exists("pitfalls.json"):
            try:
                pitfalls_raw = ctx.read_text("pitfalls.json")
                pitfalls_data = json.loads(pitfalls_raw)
                if isinstance(pitfalls_data, list):
                    # gate_pitfalls lets a malformed entry through; only objects count.
                    pitfalls_data = [p for p in pitfalls_data if isinstance(p, dict)]
                    addressed, aside, pit_missing = pitfalls_mod.pitfall_coverage(
                        design_text, pitfalls_data, source=source)
                    missing_high = [pid for pid in pit_missing
                                   if any(p.get("id") == pid and p.get("risk") == "high"
                                          for p in pitfalls_data)]
                    if missing_high:
                        advisories.append(
                            f"high-risk pitfall(s) not addressed in the design: "
                            f"{', '.join(missing_high)}"
                        )
                    coverage_lines = []
                    for p in pitfalls_data:
                        pid = p.get("id", "?")
                        risk = p.get("risk", "?")
                        if pid in addressed:
                            coverage_lines.append(f"  {pid} ({risk}): addressed")
                        elif pid in aside:
                            coverage_lines.append(f"  {pid} ({risk}): set aside")
                        else:
                            # Only a high-risk pitfall must be answered; the others
                            # appear in a design only where they change a decision.
                            coverage_lines.append(f"  {pid} ({risk}): "
                                                  + ("MISSING" if risk == "high"
                                                     else "not mentioned"))
                    if coverage_lines:
                        ctx.set_fact("pitfall_coverage",
                                     "Pitfall coverage:\n" + "\n".join(coverage_lines))
            except (json.JSONDecodeError, UnicodeDecodeError):
                advisories.append("pitfalls.json exists but is not valid JSON")
        else:
            advisories.append("pitfalls.json is missing")
    else:
        ctx.set_fact("pitfall_coverage",
                     "pitfalls check skipped: no pitfalls_researcher in this graph")

    # ── advisory 5: finding coverage ──
    # Research and perspective findings are read by the design writer. A design cites
    # only the ones that change a decision, so an uncited finding is recorded in the
    # finding_coverage fact, never listed at the gate or sent back.
    from refactory_dsh import findings_coverage
    findings_coverage.gate_problems(ctx)

    # ── decide: proceed, or one rewrite for the section the build needs ──
    # Rewrites are counted, not visits: a design a person sends back at gate_strategy
    # that then lacks ## Changes still gets the rewrite it has not had.
    allowed = int(ctx.knob("design_review_attempts", 1))
    rewrites = int(ctx.facts.get("design_review_rewrites", 0) or 0)
    used = int(ctx.facts.get("design_review_attempts_used", 0)) + 1
    ctx.set_fact("design_review_attempts_used", used)
    ctx.set_fact("design_review_advisory", "\n".join(f"- {a}" for a in advisories))
    advisory_block = _advisory_block(advisories)
    coverage = ctx.facts.get("pitfall_coverage", "")
    coverage_block = f"\n\n{coverage}" if coverage else ""

    if not problems:
        ctx.write_text("reviews/design-review-feedback.txt",
                       "nothing blocks the build\n" + advisory_block)
        ctx.set_fact("design_review_feedback", "(nothing blocks the build)")
        ctx.set_fact("design_review_blocking", "")
        ctx.set_fact("design_review_findings", "(none: nothing blocks the build)" + coverage_block)
        ctx.set_fact("design_review_status", design_review_status(ctx.facts))
        return "proceed"

    report = "\n".join(f"- {p}" for p in problems)
    ctx.set_fact("design_review_blocking", report)

    if rewrites < allowed:
        # The one rewrite: the writer also sees what is advisory, to fix while it is there.
        ctx.set_fact("design_review_rewrites", rewrites + 1)
        feedback = report + _advisory_block(advisories)
        ctx.write_text("reviews/design-review-feedback.txt", feedback + "\n")
        ctx.set_fact("design_review_feedback", feedback)
        ctx.set_fact("design_review_findings", "(no findings yet)")
        return Verdict("reloop", report)

    # Out of rewrites: the person decides, with the gap shown.
    ctx.write_text("reviews/design-review-feedback.txt", report + "\n" + advisory_block)
    ctx.set_fact("design_review_feedback", report)
    ctx.set_fact("design_review_findings", report + coverage_block)
    ctx.set_fact("design_review_status", design_review_status(ctx.facts))
    return "proceed"


def _high_risk_pitfalls(ctx: Any) -> list[str]:
    """The ids of pitfalls.json's high-risk pitfalls; none when it is missing or unreadable
    (gate_pitfalls has already put that on the card)."""
    try:
        data = json.loads(ctx.read_text("pitfalls.json")) if ctx.exists("pitfalls.json") else []
    except (json.JSONDecodeError, UnicodeDecodeError):
        return []
    if not isinstance(data, list):
        return []
    return [str(entry.get("id") or "?") for entry in data
            if isinstance(entry, dict) and entry.get("risk") == "high"]


#: A research direction's footprint has exactly these keys, each a list (dm-200).
FOOTPRINT_KEYS = ("existing_functions", "new_items", "shared_engine")

#: One existing function a change touches: "path:line name" (or "path:start-end
#: name", counted by its start line), with a real line number and a single name.
_FUNCTION_CITATION = _re.compile(
    r"(?P<path>[^\s:]+):(?P<line>[1-9]\d*)(?:-[1-9]\d*)?\s+(?P<name>[A-Za-z_][\w.]*(?:\(\))?)")


def tracked_files(source: str) -> set[str] | None:
    """The checkout's tracked files, or None when *source* is not a git checkout."""
    try:
        listing = worktree.git(Path(source), "ls-files", "-z")
    except Exception:  # not a git checkout: the caller checks the file system instead
        return None
    return {name for name in listing.split("\0") if name}


def cited_candidates(source: str, cited: str, tracked: set[str] | None) -> list[str]:
    """Every checkout file a cited path could name, as a path from the checkout's root.

    A citation names a path from the repository root, from a package, or a bare file
    name; *tracked* (`tracked_files`) is what counts: the path itself when it is
    tracked, otherwise every tracked file ending with ``/<cited>``. Without a git
    checkout the file system decides. A path outside the checkout names nothing: an
    absolute path, one with ``..``, or a tracked symlink whose target is outside it.
    """
    if cited.startswith(("/", "~")) or ".." in Path(cited).parts:
        return []
    if tracked is not None:
        if cited in tracked:
            names = [cited]
        else:
            names = sorted(name for name in tracked if name.endswith("/" + cited))
        return [name for name in names if inside_checkout(source, name)]
    try:
        named = inside_checkout(source, cited) and (Path(source) / cited).is_file()
        return [cited] if named else []
    except OSError:  # a path too long for the file system names no file
        return []


def inside_checkout(source: str, name: str) -> bool:
    """Whether checkout path *name*, every symlink followed, is still inside *source*."""
    try:
        root = Path(source).resolve()
        return (root / name).resolve().is_relative_to(root)
    except (OSError, RuntimeError):  # too long a path, or a symlink loop
        return False


def resolve_cited(source: str, cited: str, tracked: set[str] | None) -> str | None:
    """The checkout file a citation names: its only candidate, or None (#498).

    With no checkout to verify against (*source* empty) the citation stands as written.
    """
    if not source:
        return cited
    candidates = cited_candidates(source, cited, tracked)
    return candidates[0] if len(candidates) == 1 else None


def classify_scale(ctx: Any) -> dict[str, Any]:
    """Deterministic op: classify the change's scale from the research footprint.

    Reads research-plan.json as raw JSON (not via ``_plan()``, which strips
    entries to only focus/slug/finding/ids).  Every direction must carry a
    ``footprint`` object.  The three-part rule decides scoped vs full; the
    brief is never read.
    """
    # ── refine detection ───────────────────────────────────────────────
    # prepare writes REFINE_SCOPE_REASON for a refine run.  Detect that
    # constant rather than reading research_scope (which we write ourselves).
    if ctx.facts.get("research_scope_reason") == REFINE_SCOPE_REASON:
        return {
            "research_scope": "scoped",
            "research_scope_reason": REFINE_SCOPE_REASON,
        }

    # ── read directions ────────────────────────────────────────────────
    from refactory_dsh.create_ops import _extract_json_array

    raw = ctx.read_text("research-plan.json") if ctx.exists("research-plan.json") else ""
    directions = _extract_json_array(raw)

    def _full(reason: str) -> dict[str, Any]:
        return {"research_scope": "full", "research_scope_reason": reason}

    if not directions:
        return _full("no usable footprint in the research")

    # ── where cited code must exist ───────────────────────────────────
    # The target's own checkout (the run's worktree), never the run directory.
    source = str(ctx.facts.get("source") or ctx.facts.get("artifact") or "")
    tracked = tracked_files(source) if source else None

    # ── aggregate footprint ────────────────────────────────────────────
    touched: dict[tuple[str, str, str], str] = {}   # (file, name, line) -> citation
    new_items: list[str] = []
    engine: list[str] = []
    for direction in directions:
        if not isinstance(direction, dict):
            return _full(f"no usable footprint: a direction is not an object: {direction!r}"[:300])
        footprint = direction.get("footprint")
        if not isinstance(footprint, dict):
            return _full("no usable footprint in the research")
        missing = [key for key in FOOTPRINT_KEYS if key not in footprint]
        unknown = sorted(key for key in footprint if key not in FOOTPRINT_KEYS)
        if missing or unknown:
            return _full("no usable footprint: "
                         + "; ".join(([f"missing {', '.join(missing)}"] if missing else [])
                                     + ([f"unknown {', '.join(unknown)}"] if unknown else [])))
        for key in FOOTPRINT_KEYS:
            if not isinstance(footprint[key], list):
                return _full(f"no usable footprint: {key} is not a list: {footprint[key]!r}"[:300])
        for entry in footprint["existing_functions"]:
            match = _FUNCTION_CITATION.fullmatch(entry.strip()) if isinstance(entry, str) else None
            resolved = resolve_cited(source, match["path"], tracked) if match else None
            if resolved is None:
                return _full(f"footprint without evidence: {entry!r}"[:300])
            # By file, name and line: same-named methods in one file are different
            # functions, and over-counting only ever errs toward full.
            touched[(resolved, match["name"], match["line"])] = f"{resolved}:{match['line']}"
        new_items.extend(str(item) for item in footprint["new_items"])
        engine.extend(str(ref) for ref in footprint["shared_engine"])

    # ── apply the three-part rule ──────────────────────────────────────
    if len(touched) > 2:
        names = ", ".join(key[1] for key in sorted(touched)[:3])
        return _full(f"touches {len(touched)} existing functions (max 2 for scoped): {names}")
    if new_items:
        return _full(f"adds new items: {', '.join(sorted(new_items)[:3])}")
    if engine:
        return _full(f"changes a shared engine: {', '.join(engine[:3])}")
    detail = ", ".join(f"{key[1]} at {touched[key]}" for key in sorted(touched))
    reason = (
        f"touches {len(touched)} existing function(s)"
        + (f" ({detail})" if detail else "")
        + ", adds nothing new, no shared engine"
    )
    return {"research_scope": "scoped", "research_scope_reason": reason}

def design_review_status(facts: dict) -> str:
    """The approval gate's card, from the run's facts.

    '' until the design-review gate has decided (and while it sends a design back).
    Then, in order:

    - ``Design review: PASS``, or ``Design review: FAIL, N unresolved`` and the line
      naming the section the build needs that is still missing;
    - ``Scale: <scoped|full> — <reason>``;
    - ``Unverified claims (high, advisory): N``, one ``- unverified claim: …`` line each:
      what the claims check could not confirm;
    - ``Claims check (advisory): N notes``: where a drifted quote was found;
    - ``Other advisories: N``: the gate's other items, and what the research and
      pitfalls gates let through.

    Only the headline's unresolved lines sent the design back; nothing below them did.
    """
    used = int(facts.get("design_review_attempts_used", 0) or 0)
    if used == 0:
        return ""
    findings = str(facts.get("design_review_findings", ""))
    if findings == "(no findings yet)":
        return ""
    blocking = facts.get("design_review_blocking")
    unresolved = [ln for ln in (str(blocking) if blocking is not None else findings).splitlines()
                  if ln.startswith("- ")]
    lines = ["Design review: " + (f"FAIL, {len(unresolved)} unresolved" if unresolved else "PASS"),
             *unresolved]
    reason = facts.get("research_scope_reason")
    if reason:
        lines.append(f"Scale: {facts.get('research_scope') or 'full'} — {reason}")
    claims = facts.get("claims_check")
    claims = claims if isinstance(claims, dict) else {}
    unverified = [str(item) for item in claims.get("unverified") or ()]
    if unverified:
        lines.append(f"Unverified claims (high, advisory): {len(unverified)}")
        lines.extend(f"- unverified claim: {item}" for item in unverified)
    notes = [str(note) for note in claims.get("notes") or ()]
    if notes:
        lines.append(f"Claims check (advisory): {len(notes)} note{'s' if len(notes) != 1 else ''}")
        lines.extend(f"- {note}" for note in notes)
    advisories = [ln for ln in str(facts.get("design_review_advisory") or "").splitlines()
                  if ln.startswith("- ")]
    for step in ("research", "pitfalls"):
        advisories.extend(f"- {step}: {ln[2:]}"
                          for ln in str(facts.get(f"{step}_advisory") or "").splitlines()
                          if ln.startswith("- "))
    if advisories:
        lines.append(f"Other advisories: {len(advisories)}")
        lines.extend(advisories)
    return "\n".join(lines)


def _quote_words(text: str) -> str:
    """Text reduced to its words: case, punctuation, markdown and spacing dropped."""
    return " ".join(_re.findall(r"[^\W_]+", text.lower()))


def _advisory_block(advisories: list[str]) -> str:
    """The gate's advisory items as a section, or '' when there are none."""
    if not advisories:
        return ""
    return ("\n\n## Advisory (does not change the build; no round needed)\n"
            + "\n".join(f"  · {a}" for a in advisories))


#: How many times design mode's research and pitfalls gates send their step back: once,
#: and only when it produced nothing parseable. They never fail the run.
DESIGN_STEP_RELOOPS = 1


def _resend_once(ctx: Any, used_fact: str, review_file: str, review_fact: str,
                 why: str) -> Verdict | None:
    """Send the step back once for *why*; None once its one resend is spent."""
    used = int(ctx.facts.get(used_fact, 0) or 0)
    if used >= DESIGN_STEP_RELOOPS:
        return None
    ctx.set_fact(used_fact, used + 1)
    ctx.write_text(review_file, f"- {why}\n")
    ctx.set_fact(review_fact, f"- {why}")
    return Verdict("reloop", why)


def gate_design_research(ctx: Any) -> Any:
    """Design mode's research gate: research guides the design and never fails the run.

    Create mode keeps `create_ops.gate_research`, which refuses a plan it cannot use. Here
    the plan is sent back once only when research-plan.json holds no parseable plan at
    all; then the run proceeds with whatever exists. Everything else that gate checks --
    three to five directions (one to three for a scoped change), empty fields, slugs, the
    findings files -- is guidance: it is listed as advisory (``research_advisory``) on
    the approval gate's card. Never raises.
    """
    from refactory_dsh.create_ops import _plan, research_problems

    raw = ctx.read_text("research-plan.json") if ctx.exists("research-plan.json") else ""
    scope = str(ctx.facts.get("research_scope") or "full")
    # Every step after this one declares research/ as an input: it exists, if empty.
    ctx.path("research").mkdir(parents=True, exist_ok=True)
    if not _plan(raw):
        why = research_problems(ctx, raw, scope)[0]
        resent = _resend_once(ctx, "research_attempts_used", "research-review.txt",
                              "research_review", why)
        if resent is not None:
            ctx.set_fact("research_advisory", "")
            return resent
        ctx.set_fact("research_advisory",
                     f"- {why}; the design proceeded without a research plan")
        return "proceed"
    # The plan parses: create's checks, as advice.
    notes = [f"- {problem}" for problem in research_problems(ctx, raw, scope, count="suggested")]
    ctx.write_text("research-review.txt", "ok\n" if not notes else "\n".join(notes) + "\n")
    ctx.set_fact("research_review", "(the research plan is used as it is)")
    ctx.set_fact("research_advisory", "\n".join(notes))
    return "proceed"


def gate_pitfalls(ctx: Any) -> Any:
    """Design mode's pitfalls gate: pitfalls guide the design and never fail the run.

    pitfalls.json is sent back once only when it holds no parseable pitfalls: it is not
    JSON, not an array, or an array with no object in it -- an empty array ``[]``
    included. Then the run proceeds with whatever exists.
    Anything else `pitfalls.validate_pitfalls` finds is listed as advisory
    (``pitfalls_advisory``) on the approval gate's card. Never raises.
    """
    from refactory_dsh import pitfalls as pitfalls_mod

    raw = ctx.read_text("pitfalls.json") if ctx.exists("pitfalls.json") else ""
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        data = None
    if not (isinstance(data, list) and any(isinstance(entry, dict) for entry in data)):
        why = f"pitfalls.json holds no parseable pitfalls: {raw.strip()[:200]!r}"
        resent = _resend_once(ctx, "pitfalls_attempts_used", "pitfalls-review.txt",
                              "pitfalls_review", why)
        if resent is not None:
            ctx.set_fact("pitfalls_advisory", "")
            return resent
        ctx.set_fact("pitfalls_advisory", f"- {why}; the design proceeded without pitfalls")
        return "proceed"
    source = str(ctx.facts.get("source") or ctx.facts.get("artifact") or "")
    problems = pitfalls_mod.validate_pitfalls(raw, source, run_dir=str(ctx.workdir))
    report = "\n".join(f"- {p}" for p in problems)
    ctx.write_text("pitfalls-review.txt", (report or "ok") + "\n")
    ctx.set_fact("pitfalls_review", "(pitfalls validated)" if not problems
                 else "(the pitfalls are used as they are)")
    ctx.set_fact("pitfalls_advisory", report)
    return "proceed"


def apply_cwd_escaped(result: Any, outcome: dict[str, Any]) -> None:
    """Record ``cwd_escaped`` into *outcome* when *result* shows a directory escape.

    Both ``check_artifact`` and ``fast_ops.focused_check`` apply this logic:
    when ``sandbox.run_command`` was called with a ``worktree_root`` and the
    command left that directory, ``result.cwd_outside`` is set.  This helper
    centralises the recording so the logic is not replicated in each call site.
    """
    if getattr(result, "cwd_outside", None):
        outcome["cwd_escaped"] = result.cwd_outside


def _refused_verdict(ctx: Any) -> Verdict | None:
    """The reloop for an emission materialise could not apply, or None when it applied."""
    refused = repo_change.refused(ctx)
    if not refused:
        return None
    # Nothing was materialised, so there is nothing to record runtime evidence about;
    # a refused emission is not a code failure, so it does not spend an attempt.
    detail = f"materialise could not apply emit.txt, so nothing was checked: {refused}"
    ctx.write_text("check.txt", detail + "\n")
    ctx.set_fact("validation", detail)
    ctx.write_json("check-record.json", {
        "status": "refused", "validation": detail,
        "attempt": int(ctx.facts.get("attempts_used", 0)),
        "check_mode": "full", "targeted_command": None,
    })
    return Verdict("reloop", detail)


def _design_constraints(ctx: Any) -> list[str]:
    """R5: the design's ``[left: …]`` sites, as lines that tell the builder not to undo them."""
    from refactory_dsh import design_contract
    run_name = str(ctx.facts.get("name") or "")
    parsed = design_contract.parse(
        ctx.read_text("design.md") if ctx.exists("design.md") else "", run_name
    )
    return [
        f"Design constraint: {site['path']} [left: {site['reason']}] "
        "— do not change this; fix the tests or callers instead."
        for site in parsed.rule_sites
        if site.get("status") == "left" and site.get("reason")
    ]


def _early_stop(ctx: Any, prepass: Any, prev_record: dict[str, Any],
                _saved_base_unavailable: str) -> Any:
    """Send the attempt back because the tests that failed last time still fail."""
    from refactory_dsh import authoring_evidence as evidence
    from refactory_dsh.create_ops import repair
    from refactory_dsh.failfast import detect_stuck

    # Stop early: the targeted test still fails.
    detail = prepass.digest_text or "(targeted check failed)"
    ctx.write_text("check.txt", detail + "\n")
    ctx.set_fact("validation", detail)

    # R5: keep design-constraint lines in early-stop feedback.
    left_lines = _design_constraints(ctx)
    if left_lines:
        detail = "\n".join(left_lines) + "\n\n" + detail

    # base_unavailable note
    base_note = _saved_base_unavailable
    if base_note:
        detail = detail + "\n\n" + base_note

    # Stuck detection on early-stop path too (correction item 4)
    found_wt = worktree.recorded(ctx.workdir)
    stuck_notice = detect_stuck(
        prev_record, prepass.runners,
        "\n".join(filter(None, [
            getattr(prepass.result, "stdout", None) or "",
            getattr(prepass.result, "stderr", None) or "",
        ])).strip(),
        found_wt.path if found_wt else None,
        prev_record.get("commit"),
        str(ctx.facts.get("commit", "")),
    )
    if stuck_notice:
        detail = stuck_notice + "\n\n" + detail

    outcome = {
        "command": prepass.targeted_command, "status": "code_failure",
        "check_mode": "targeted", "targeted_command": prepass.targeted_command,
    }
    evidence.record(ctx, outcome)
    ctx.write_json("check-record.json", {
        "status": "fail",
        "validation": detail[:4000],
        "digest": prepass.digest_text,
        "attempt": int(ctx.facts.get("attempts_used", 0)),
        "command": prepass.targeted_command,
        "runners": prepass.runners,
        "raw_tail": prepass.raw_tail,
        "check_mode": "targeted",
        "targeted_command": prepass.targeted_command,
        "inherited": list(prev_record.get("inherited", [])),
        "commit": str(ctx.facts.get("commit", "")),
    })
    # Do NOT append to convergence history — targeted runs excluded.
    return repair(ctx, [detail], early_stop=True)


def check_artifact(ctx: Any, *, check_attempt: str | None = None) -> str:
    """Gate: run the brief's check, in a sandbox, and refuse what does not pass.

    This is the design-time equivalent of the package gate: a package must import and
    score, an artifact must build and run. Both refuse rather than warn, because an
    artifact nobody checked is not a deliverable.

    The check runs where the run works (`repo_change.run_check`). An emission materialise
    could not apply is sent back to emit with the reason, as a failed check is, rather
    than ending the run (#330 E5). ``check_attempt`` names the prepared attempt the
    evidence belongs to, when `prepare_check` ran this whole check itself.
    """
    refused = _refused_verdict(ctx)
    if refused is not None:
        return refused
    command = str(ctx.facts["check"])
    check_timeout = int(ctx.knob("check_timeout_s", 180))

    # ── Targeted pre-pass ──────────────────────────────────────────────
    from refactory_dsh.failfast import targeted_prepass

    prev_record = ctx.read_json("check-record.json", {})
    # Read base_unavailable before it is cleared below.
    _saved_base_unavailable = ctx.facts.get("base_unavailable") or ""

    prepass = targeted_prepass(
        prev_record, command,
        lambda cmd, timeout: repo_change.run_check(ctx, cmd, timeout, triage_prep=False),
        check_timeout,
        root=(worktree.recorded(ctx.workdir).path
              if worktree.recorded(ctx.workdir) is not None else None),
    )
    if prepass.early_stop:
        return _early_stop(ctx, prepass, prev_record, _saved_base_unavailable)
    return _full_check(ctx, command, check_timeout, prev_record,
                       prepass.targeted_command if prepass.ran else None,
                       beside_reviews=False, check_attempt=check_attempt)


def _full_check(ctx: Any, command: str, check_timeout: int, prev_record: dict[str, Any],
                targeted_command: str | None, *, beside_reviews: bool,
                check_attempt: str | None = None) -> Any:
    """Run the whole check on the attempt, record its evidence, and judge it."""
    from refactory_dsh import authoring_evidence as evidence
    from refactory_dsh.create_ops import repair
    from refactory_dsh.failfast import detect_stuck, record_check_history

    result = (repo_change.run_check(ctx, command, check_timeout, beside_reviews=True)
              if beside_reviews else repo_change.run_check(ctx, command, check_timeout))
    detail = "\n".join((result.problem(), result.stdout or "", result.stderr or "")).strip()
    outcome = {"command": command, "status": "pass" if result.ok else evidence.check_failure_kind(
        result, budget=getattr(ctx, "budget", None)),
        "stdout": result.stdout, "stderr": result.stderr, "returncode": result.returncode,
        "timed_out": result.timed_out, "elapsed_s": result.elapsed_s,
        "check_mode": "full",
        "targeted_command": targeted_command}
    if check_attempt:
        # The prepared attempt this evidence belongs to: a review taken beside this
        # check binds to it (`authoring_evidence.current_problems`).
        outcome["check_attempt"] = check_attempt
    apply_cwd_escaped(result, outcome)
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

    # Clear base_unavailable at the start of each check attempt (R2).
    ctx.set_fact("base_unavailable", "")

    if result.ok:
        # ── enforce test guardedness (R3, #384 item 1) ──
        unexempted = enforce_guardedness(ctx)
        if unexempted:
            detail = (
                "BLOCKING: these new tests pass without the change: "
                + ", ".join(unexempted)
                + ". Make them fail without the change, or explain in the "
                "PR why each test pins existing behaviour."
            )
            ctx.write_text("check.txt", detail + "\n")
            ctx.write_json("check-record.json", {
                "status": "guarded", "validation": detail,
                "attempt": int(ctx.facts.get("attempts_used", 0)),
                "check_mode": "full",
                "targeted_command": targeted_command,
            })
            return repair(ctx, [detail])
        ctx.set_fact("validation", "(the check passed)")
        ctx.write_json("check-record.json", {
            "status": "pass", "validation": "(the check passed)",
            "attempt": int(ctx.facts.get("attempts_used", 0)),
            "check_mode": "full",
            "targeted_command": targeted_command,
        })
        return "proceed"

    # Name the specific resource limit when a file-size stop occurs.
    rc = result.returncode
    if rc == 153 or (rc is not None and rc < 0 and -rc == 25):
        detail = (
            f"the check exceeded the file-size limit ({sandbox.DEFAULT_FILE_MB} MB, "
            "set as DEFAULT_FILE_MB in refactory_dsh/sandbox.py). " + detail
        )
    evidence.stop_unrepairable(outcome["status"], detail)

    # Route change-caused preparation failures to the builder for repair.
    _ccp = getattr(result, "change_caused_prep", None)
    if _ccp:
        from refactory_dsh.check_digest import _clean as _strip_noise
        _error_text = _strip_noise(_ccp["error_text"])
        _prep_detail = (
            f"the {_ccp['step_label']} command `{_ccp['command']}` "
            f"failed (exit {_ccp['returncode']}):\n{_error_text}"
        )
        ctx.write_text("check.txt", _prep_detail + "\n")
        record_check_history(ctx, "full", _prep_detail, command)
        ctx.write_json("check-record.json", {
            "status": "fail",
            "validation": _prep_detail[:4000],
            "attempt": int(ctx.facts.get("attempts_used", 0)),
            "check_mode": "prep_triage",
            "command": _ccp.get("command"),
            "commit": str(ctx.facts.get("commit", "")),
        })
        return repair(ctx, [_prep_detail])

    # A failure the unchanged base has too is not the attempt's (`base_check.compare_failures`).
    found = worktree.recorded(ctx.workdir)
    if found is not None:
        filtered = base_check.filter_check(
            ctx, result, command, int(ctx.knob("check_timeout_s", 180)),
            guardedness=guardedness,
        )
        if filtered.base_environment_failure:
            if filtered.base_crashed:
                # R2: a crash — continue without excusals, with the note.
                note = (f"base comparison unavailable: {filtered.base_environment_failure}; "
                        "the attempt's results stand without excusals")
                ctx.set_fact("base_unavailable", note)
            else:
                # A deterministic failure — stop as today's base_environment_failure.
                evidence.stop_unrepairable(
                    'unavailable',
                    f"the base check could not run: {filtered.base_environment_failure}",
                )

        # Diagnose unbuilt packages from the worktree's own manifests.
        wt_path = Path(found.path)
        unbuilt = unbuilt_check.scan_unbuilt_packages(wt_path, found.base, found.head)
        unbuilt_msg = unbuilt_check.format_unbuilt_diagnosis(unbuilt) if unbuilt else ""

        if filtered.all_excused:
            inherited_detail = filtered.inherited_detail
            if unbuilt_msg and inherited_detail:
                inherited_detail = inherited_detail + "\n\n" + unbuilt_msg
            elif unbuilt_msg:
                inherited_detail = unbuilt_msg
            validation_msg = f"(the check passed; {inherited_detail})"
            ctx.set_fact("validation", validation_msg)
            ctx.write_text("check.txt", validation_msg + "\n")
            # The receipt the reviews and the audit check is this one, which passed.
            outcome["status"] = "pass"
            outcome["inherited_failures"] = sorted(filtered.inherited)
            ctx.set_fact("inherited_failures", sorted(filtered.inherited))
            evidence.record(ctx, outcome)
            ctx.write_json("check-record.json", {
                "status": "pass", "validation": validation_msg,
                "attempt": int(ctx.facts.get("attempts_used", 0)),
                "check_mode": "full",
                "targeted_command": targeted_command,
            })
            # ── enforce test guardedness on excused pass too (R3) ──
            unexempted = enforce_guardedness(ctx)
            if unexempted:
                detail = (
                    "BLOCKING: these new tests pass without the change: "
                    + ", ".join(unexempted)
                    + ". Make them fail without the change, or explain in the "
                    "PR why each test pins existing behaviour."
                )
                ctx.write_text("check.txt", detail + "\n")
                ctx.write_json("check-record.json", {
                    "status": "guarded", "validation": detail,
                    "attempt": int(ctx.facts.get("attempts_used", 0)),
                    "check_mode": "full",
                    "targeted_command": targeted_command,
                })
                return repair(ctx, [detail])
            return "proceed"
        if filtered.new_failures and filtered.inherited:
            detail = filtered.new_failure_detail
        if filtered.inherited_detail:
            inherited_part = filtered.inherited_detail
            if unbuilt_msg:
                inherited_part = inherited_part + "\n\n" + unbuilt_msg
            ctx.write_text("check.txt", detail + "\n\n" + inherited_part + "\n")
        elif unbuilt_msg:
            ctx.write_text("check.txt", detail + "\n\n" + unbuilt_msg + "\n")

        # When every new failure names an exact missing entry-point path,
        # stop without spending a builder attempt.
        if unbuilt and unbuilt_check.failures_explained_by_unbuilt(detail, unbuilt):
            raise ValueError(
                f"check failed because of unbuilt packages; code repair was not attempted.\n{unbuilt_msg}"
            )
        # Otherwise prepend the diagnosis so the builder sees it.
        if unbuilt_msg:
            detail = unbuilt_msg + "\n\n" + detail

    # Build digest of new failures
    from refactory_dsh.check_digest import digest_check_output, format_digest
    check_output = detail
    digest = digest_check_output(check_output)
    digest_text = format_digest(digest)
    if digest_text.strip():
        detail = digest_text + "\n\n" + detail[-2000:]

    # R5: augment feedback with [left: ...] design constraints and broken guarantees.
    from refactory_dsh import design_contract
    run_name = str(ctx.facts.get("name") or "")
    parsed = design_contract.parse(
        ctx.read_text("design.md") if ctx.exists("design.md") else "", run_name
    )
    left_lines: list[str] = []
    for site in parsed.rule_sites:
        if site.get("status") == "left" and site.get("reason"):
            left_lines.append(
                f"Design constraint: {site['path']} [left: {site['reason']}] "
                "— do not change this; fix the tests or callers instead."
            )

    guarantee_lines: list[str] = []
    if (found is not None and filtered is not None
            and not filtered.base_environment_failure
            and filtered.base_complete
            and filtered.base_failures is not None):
        attempt_output = "\n".join((result.stdout or "", result.stderr or "")).strip()
        attempt_failures = base_check.parse_test_failures(attempt_output)
        changed_files = set(base_check._all_changed_files(Path(found.path), found.base))
        for failure in sorted(attempt_failures):
            if failure not in filtered.base_failures:
                test_file = base_check._test_file(failure)
                if test_file and test_file not in changed_files:
                    guarantee_lines.append(
                        f"Broken guarantee: {failure} passes on the base but "
                        "fails on this attempt — the attempt must not break it."
                    )

    if left_lines or guarantee_lines:
        detail = "\n".join(left_lines + guarantee_lines) + "\n\n" + detail

    # Stuck detection on the full-check path.
    found_wt_stuck = worktree.recorded(ctx.workdir)
    stuck_notice = detect_stuck(
        prev_record, digest.to_dict()["runners"],
        "\n".join((result.stdout or "", result.stderr or "")).strip(),
        found_wt_stuck.path if found_wt_stuck else None,
        prev_record.get("commit"),
        str(ctx.facts.get("commit", "")),
    )
    if stuck_notice:
        detail = stuck_notice + "\n\n" + detail

    # R6: store failure history and blocker result for convergence detection.
    attempt_output_r6 = "\n".join((result.stdout or "", result.stderr or "")).strip()
    record_check_history(ctx, "full", attempt_output_r6, command)

    # Write base_unavailable note into check.txt as well (R2 correction 5).
    base_note = ctx.facts.get("base_unavailable")
    if base_note:
        check_txt = ctx.read_text("check.txt") if ctx.exists("check.txt") else ""
        if base_note not in check_txt:
            ctx.write_text("check.txt", check_txt.rstrip() + "\n\n" + base_note + "\n")

    # Record exactly what the builder will be given (feedback re-fill reads it).
    _inherited_list = sorted(filtered.inherited) if found is not None else []
    _base_reset_list = getattr(ctx, '_base_reset', [])
    _cr_fail: dict[str, Any] = {
        "status": "fail",
        "validation": detail[:4000],
        "digest": digest_text,
        "attempt": int(ctx.facts.get("attempts_used", 0)),
        "command": command,
        "runners": digest.to_dict().get("runners", []),
        "raw_tail": digest.raw_tail[-500:],
        "check_mode": "full",
        "targeted_command": targeted_command,
        "inherited": _inherited_list,
        "commit": str(ctx.facts.get("commit", "")),
    }
    if _base_reset_list:
        _cr_fail["base_reset"] = _base_reset_list
    ctx.write_json("check-record.json", _cr_fail)

    return repair(ctx, [detail])


def prepare_check(ctx: Any) -> Any:
    """Gate: bring the attempt to the commit its check and its reviewers both see.

    The check runs beside the reviews (`check_beside_reviews`), so everything that can
    still move the commit happens here first: the environment install, the ``generate``
    command and the ``setup`` build (`repo_change.prepare_attempt`). Then the self-check
    (`_self_check`) runs the tests the change can break, the last check's failures among
    them, and a failure goes back to the builder before any review starts. A
    preparation that fails is judged here by the whole check, as it always was, which
    reports it with its triage.
    """
    import uuid

    from refactory_dsh import authoring_evidence as evidence

    # Nothing a last attempt prepared or decided may stand for this one.
    for name in (CHECK_PREP, CHECK_VERDICT):
        ctx.path(name).unlink(missing_ok=True)
    refused = _refused_verdict(ctx)
    if refused is not None:
        return refused
    command = str(ctx.facts["check"])
    check_timeout = int(ctx.knob("check_timeout_s", 180))
    attempt = uuid.uuid4().hex
    # What sent the build back, for the reviewers: the check beside them rewrites
    # `validation` while they run. A self-check fix-up is not what they judge the
    # attempt against, so it keeps what sent the attempt back before it.
    if not _fixups_used(ctx):
        ctx.set_fact("review_feedback", str(ctx.facts.get("validation") or ""))
    ctx.set_fact("review_baseline", json.dumps(evidence.requirements(ctx), indent=1))
    prev_record = ctx.read_json("check-record.json", {})
    saved_base_unavailable = ctx.facts.get("base_unavailable") or ""
    if repo_change.prepare_attempt(ctx, check_timeout):
        _end_fixups(ctx)
        verdict = check_artifact(ctx, check_attempt=attempt)
        _record_prepared(ctx, attempt, None)
        _record_check_verdict(ctx, attempt, verdict)
        return verdict
    return _self_check(ctx, command, check_timeout, prev_record, saved_base_unavailable, attempt)


def _fixups_used(ctx: Any) -> int:
    """The self-check fix-ups this attempt has had; while it is above 0, emit amends the attempt."""
    return int(ctx.facts.get("self_check_fixup_count") or 0)


def _end_fixups(ctx: Any) -> None:
    """The attempt goes on to the fork or is spent: the next emission is a new attempt."""
    if _fixups_used(ctx):
        ctx.set_fact("self_check_fixup_count", 0)


def _self_check(ctx: Any, command: str, check_timeout: int, prev_record: dict[str, Any],
                saved_base_unavailable: str, attempt: str) -> Any:
    """Run the tests this change can break, and send the change's failures back (b41a).

    The selection (`test_selection.select`) runs in chunks within its time limit
    (`failfast.targeted_prepass`), in the attempt's prepared worktree. A failure the
    base has too is not the change's (`_the_changes_failures`). The change's failures
    go back to emit as a fix-up, which spends no attempt and runs no review, at most
    ``self_check_fixups`` times an attempt and that many times ``check_attempts`` a run.
    Past the bound, a still-failing self-check is the early stop, which spends the
    attempt. Anything else, a pass or a result that says nothing (a timeout, a runner
    it cannot target, nothing collected), goes on to the reviews and the whole check.
    """
    from refactory_dsh import base_check, test_selection
    from refactory_dsh.failfast import targeted_prepass, without_failures

    found = worktree.recorded(ctx.workdir)
    if found is not None:
        root = Path(found.path)
        touched = base_check._all_changed_files(root, found.base)
    else:
        root = Path(str(ctx.facts.get("artifact") or ctx.workdir))
        touched = []
    earlier = _self_checks(ctx)
    # On a fix-up, what the last self-check found failing runs first.
    first = list(earlier[-1].get("failing_ids", [])) if _fixups_used(ctx) and earlier else []
    selection = test_selection.select(touched, root, prev_record, check_command=command,
                                      first=first,
                                      costs=test_selection.costs_from_record(earlier, root))
    prepass = targeted_prepass(
        prev_record, command,
        lambda cmd, timeout: repo_change.run_check(ctx, cmd, timeout, triage_prep=False,
                                                   prepared=True),
        check_timeout, root=found.path if found is not None else None,
        selection=selection.tests,
        self_check_timeout_s=int(ctx.knob("self_check_timeout_s", 300)),
    )
    failing, inherited, base = (list(prepass.failing_ids), [], "")
    if failing:
        failing, inherited, base = _the_changes_failures(
            ctx, command, failing, check_timeout, prepass.limit_s or check_timeout // 3)
    used = _fixups_used(ctx)
    allowed = int(ctx.knob("self_check_fixups", 2))
    run_used = int(ctx.facts.get("total_self_check_fixups_used") or 0)
    run_cap = allowed * int(ctx.knob("check_attempts", 3))
    entry = {
        "attempt": int(ctx.facts.get("attempts_used", 0)) + 1,
        "commit": str(ctx.facts.get("commit") or ""),
        "fixups_before": used,
        "selection": prepass.selection, "how_selected": selection.record(),
        "ran": prepass.ran_ids, "cut": prepass.cut_ids, "commands": prepass.commands,
        "notes": prepass.notes, "limit_s": prepass.limit_s, "duration_s": prepass.elapsed_s,
        "timings": prepass.timings,
        "failing_ids": failing, "inherited_filtered": inherited, "base": base,
    }
    if failing:
        shown = without_failures(prepass, inherited)
        if used < allowed and run_used < run_cap:
            return _self_check_fixup(ctx, shown, entry, used + 1, allowed, run_used + 1, base)
        why = (f"{used} of {allowed} fix-ups used on this attempt" if used >= allowed
               else f"{run_used} of {run_cap} fix-ups used on this run")
        _record_self_check(ctx, {**entry, "outcome": "exhausted", "why": why})
        _end_fixups(ctx)
        # The base's failures stay out of what the next check counts as this change's.
        prior = {**prev_record, "inherited": sorted({*prev_record.get("inherited", []), *inherited})}
        return _early_stop(ctx, shown, prior, saved_base_unavailable)
    outcome = ("nothing selected" if not prepass.selection else
               "inherited" if inherited else
               "pass" if prepass.ran and not prepass.cut_ids and not prepass.notes else
               "inconclusive")
    _record_self_check(ctx, {**entry, "outcome": outcome})
    _end_fixups(ctx)
    _record_prepared(ctx, attempt, prepass.targeted_command if prepass.ran else None)
    return "proceed"


def _the_changes_failures(ctx: Any, command: str, failing: list[str], check_timeout: int,
                          limit_s: float) -> tuple[list[str], list[str], str]:
    """Split the self-check's failures into the change's and the base's.

    The rule is the check's own (`base_check.compare_failures`): a failure is inherited
    only when the base fails the same test, the change did not touch its file, and
    neither the brief nor the design names it. Only failures that could be inherited
    are run on the base (`base_check.targeted_base_failures`). A base that is
    unavailable lets none be inherited: they all go to the builder.

    Returns the change's failures, the inherited ones, and what the base said.
    """
    from refactory_dsh import base_check

    found = worktree.recorded(ctx.workdir)
    if found is None:
        return failing, [], "no base: the run has no worktree"
    changed = base_check._all_changed_files(Path(found.path), found.base)
    brief = str(ctx.facts.get("brief") or "")
    design = ctx.read_text("design.md") if ctx.exists("design.md") else ""
    # Were the base to fail them all, these would be inherited; only they need the base.
    could_be = base_check.compare_failures(set(failing), set(failing), changed, brief, design).inherited
    if not could_be:
        return failing, [], ("not needed: each failure is in a file the change touched, "
                             "or one the brief or the design names")
    result, source = base_check.targeted_base_failures(
        ctx, command, sorted(could_be), prepare_timeout=check_timeout, timeout=limit_s)
    if result.environment_failure:
        return failing, [], f"unavailable, so nothing is inherited: {result.environment_failure}"
    inherited = base_check.compare_failures(result.failures, set(failing), changed, brief,
                                            design).inherited
    return ([test_id for test_id in failing if test_id not in inherited], sorted(inherited),
            f"compared with {source}")


def _self_checks(ctx: Any) -> list[dict[str, Any]]:
    """The self-checks this run recorded, oldest first."""
    if not ctx.exists(SELF_CHECK_RECORD):
        return []
    entries = []
    for line in ctx.read_text(SELF_CHECK_RECORD).splitlines():
        try:
            entries.append(json.loads(line))
        except ValueError:
            continue
    return entries


def _record_self_check(ctx: Any, entry: dict[str, Any]) -> None:
    """Append the self-check to its record, and say what it did on stderr, as a gate logs."""
    import sys

    ctx.append_text(SELF_CHECK_RECORD, json.dumps(entry, default=str) + "\n")
    ran, cut = len(entry["ran"]), len(entry["cut"])
    print(f"refactory: self-check on attempt {entry['attempt']}: {entry['outcome']}; "
          f"{ran} of {len(entry['selection'])} selected test(s) ran in {entry['duration_s']} s"
          + (f", {cut} cut at the limit" if cut else "")
          + (f"; {len(entry['failing_ids'])} failing" if entry["failing_ids"] else ""),
          file=sys.stderr)


def _self_check_fixup(ctx: Any, prepass: Any, entry: dict[str, Any], number: int, allowed: int,
                      run_used: int, base: str) -> Verdict:
    """Send the change's failures back to emit, spending no attempt and running no review.

    The reloop `_refused_verdict` takes: ``validation`` carries the failures to emit on
    the walker, and the verdict's reason does on the spine. The next emission is amended
    into this attempt (`repo_change._commit_emission`), so the attempt stays one commit.
    """
    lead = (f"self-check fix-up {number} of {allowed}: {len(prepass.failing_ids)} test(s) "
            "this change can break fail on this attempt, before any review ran. Fix them and "
            "emit again; this attempt is not spent.")
    parts = [lead, "", *(f"- {test_id}" for test_id in prepass.failing_ids)]
    if prepass.digest_text:
        parts += ["", prepass.digest_text]
    left_lines = _design_constraints(ctx)
    if left_lines:
        parts += ["", *left_lines]
    if entry["inherited_filtered"]:
        parts += ["", "These fail on the base too, so they are not this change's: "
                  + ", ".join(entry["inherited_filtered"])]
    if base.startswith("unavailable"):
        parts += ["", f"The base comparison was {base}."]
    detail = "\n".join(parts)
    ctx.write_text("check.txt", detail + "\n")
    ctx.set_fact("validation", detail)
    ctx.set_fact("self_check_fixup_count", number)
    ctx.set_fact("total_self_check_fixups_used", run_used)
    _record_self_check(ctx, {**entry, "outcome": "fixup", "fixup": number})
    return Verdict("reloop", detail)


def _record_prepared(ctx: Any, attempt: str, targeted_command: str | None) -> None:
    # `root` is the directory the evidence attests (`authoring_evidence.record`).
    ctx.write_json(CHECK_PREP, {
        "attempt": attempt, "commit": str(ctx.facts.get("commit") or ""),
        "root": str(ctx.facts.get("artifact") or ctx.facts.get("package_path") or ""),
        "targeted_command": targeted_command})


def _record_check_verdict(ctx: Any, attempt: str, verdict: Any) -> None:
    # The command it ran: a decision stands for this attempt only while the check is
    # the same command (`graph-control amend-command` changes it).
    ctx.write_json(CHECK_VERDICT, {"attempt": attempt, "command": str(ctx.facts.get("check") or ""),
                                   "verdict": str(verdict), "reason": getattr(verdict, "reason", "")})


def _decided(ctx: Any, attempt: str) -> dict[str, Any]:
    """The decision recorded for ``attempt`` with the current check command, or {}."""
    decided = ctx.read_json(CHECK_VERDICT, {}) if ctx.exists(CHECK_VERDICT) else {}
    if (attempt and decided.get("attempt") == attempt
            and decided.get("command") == str(ctx.facts.get("check") or "")):
        return decided
    return {}


def check_beside_reviews(ctx: Any) -> dict[str, Any]:
    """Op inside the review fork: the attempt's whole check, run beside its reviews.

    It runs on the commit `prepare_check` settled, in the run's check copy
    (`repo_change.run_check` with ``beside_reviews``), and records what it decided for
    `gate_check`, which routes once the reviews are done: a node inside a fork may not
    send the run outside it. A check that raises is recorded too and raised by
    `gate_check`, because the node runner retries a failed op, and a retry here would be
    a second full check for the same failure.
    """
    from refactory_dsh.budgets import BudgetExhausted

    attempt = _prepared_attempt(ctx)
    if not attempt:
        raise ValueError("the check was not prepared for this attempt; prepare_check runs "
                         "before the review fork")
    if _decided(ctx, attempt):
        return {}  # prepare_check ran the whole check itself
    try:
        verdict = _full_check(ctx, str(ctx.facts["check"]), int(ctx.knob("check_timeout_s", 180)),
                              ctx.read_json("check-record.json", {}),
                              _prepared(ctx).get("targeted_command"), beside_reviews=True,
                              check_attempt=attempt)
    except BudgetExhausted:
        raise
    except Exception as exc:  # recorded for gate_check to raise, not retried here
        ctx.write_json(CHECK_VERDICT, {"attempt": attempt, "command": str(ctx.facts["check"]),
                                       "error": str(exc), "error_type": type(exc).__name__})
        return {}
    _record_check_verdict(ctx, attempt, verdict)
    return {}


def _prepared(ctx: Any) -> dict[str, Any]:
    return ctx.read_json(CHECK_PREP, {}) if ctx.exists(CHECK_PREP) else {}


def _prepared_attempt(ctx: Any) -> str:
    """The attempt `prepare_check` prepared, when it is the commit the run is at now."""
    prepared = _prepared(ctx)
    if prepared.get("attempt") and prepared.get("commit") == str(ctx.facts.get("commit") or ""):
        return str(prepared["attempt"])
    return ""


def gate_check(ctx: Any) -> Any:
    """Gate after the review fork: route on what the check beside the reviews decided.

    A failed check sends the build back with the check's feedback, as before; the
    reviews of that attempt are not read, because the next attempt is reviewed afresh.
    A check that stopped the run (an unrepairable failure) stops it here. A person's
    skip of the check proceeds on its skip receipt for this attempt.

    With no decision for this attempt and command -- the check command was corrected
    after the check ran (`graph-control amend-command` reruns this gate), or the check
    never recorded one -- this gate runs the whole check itself, as the sequential gate
    did, bound to the same attempt, so the reviews beside it stay current.
    """
    from refactory_dsh import authoring_evidence as evidence

    attempt = _prepared_attempt(ctx)
    if not attempt:
        raise ValueError("the check was not prepared for the attempt the run is at; "
                         "prepare_check runs before the review fork")
    decided = _decided(ctx, attempt)
    if decided:
        if decided.get("error_type"):
            # The check stopped the run, as it would have before the reviews: a
            # build-loop limit stays a BuildLoopStop, so the spine records the run failed.
            from refactory_dsh.run import BuildLoopStop

            kind = {"RuntimeError": RuntimeError, "BuildLoopStop": BuildLoopStop}.get(
                str(decided["error_type"]), ValueError)
            raise kind(str(decided.get("error") or "the check failed"))
        label = str(decided.get("verdict") or "")
        return "proceed" if label == "proceed" else Verdict(label, str(decided.get("reason") or ""))
    validation = (ctx.read_json(evidence.EVIDENCE, {}) if ctx.exists(evidence.EVIDENCE)
                  else {}).get("validation", {})
    if (validation.get("status") == "skipped" and validation.get("source") == "person"
            and validation.get("check_attempt") == attempt):
        return "proceed"
    verdict = check_artifact(ctx, check_attempt=attempt)
    _record_check_verdict(ctx, attempt, verdict)
    return verdict


def enforce_guardedness(ctx: Any) -> list[str]:
    """Return the names of non-guarding **added** tests that are not exempted.

    Only added test files (not modified ones) are enforced, as the brief
    says "a new test".  Exemptions come from two sources:

    1. design.md's ``## Test guardedness exemptions`` section (full mode).
    2. A ``# guardedness-exempt: <reason>`` comment in the test file itself
       (works in both full and fast mode, since fast has no design.md).

    ``cannot_run_on_base`` files (collection error on the base, typically
    because they import newly-added code) are excluded: they are not
    "passing without the change".

    When no guardedness record exists, the caller should note it in the
    check record.
    """
    if not ctx.exists("test-guardedness.json"):
        return []
    guardedness = ctx.read_json("test-guardedness.json", {})
    if "unavailable" in guardedness:
        return []
    summary = guardedness.get("summary", {})
    if summary.get("not_guarding", 0) == 0:
        return []

    # Determine which test files were ADDED (not modified).
    added_files: set[str] | None = None
    try:
        from refactory_dsh import worktree
        found = worktree.recorded(ctx.workdir)
        if found is not None:
            added_raw = worktree.git(
                Path(found.path), "diff", "--name-only", "--diff-filter=A",
                found.base, "HEAD",
            )
            added_files = set(f.strip() for f in added_raw.splitlines() if f.strip())
    except (ValueError, OSError, AttributeError):
        pass  # If we can't determine, enforce on all (conservative)

    # Collect non-guarding test file paths — only ADDED files.
    non_guarding = []
    for result in guardedness.get("results", []):
        if result.get("guarding") is False and result.get("file_level") != "cannot_run_on_base":
            tf = result.get("file", "unknown")
            if added_files is not None and tf not in added_files:
                continue  # Modified file, not a new test — skip
            non_guarding.append(tf)

    if not non_guarding:
        return []

    # Read exemptions from design.md (full mode).
    design_text = ctx.read_text("design.md") if ctx.exists("design.md") else ""
    exempted: set[str] = set()
    in_section = False
    for line in design_text.splitlines():
        stripped = line.strip()
        if stripped.lower() == "## test guardedness exemptions":
            in_section = True
            continue
        if in_section and stripped.startswith("## "):
            break
        if in_section and (stripped.startswith("- ") or stripped.startswith("* ")):
            bullet_text = stripped[2:].strip()
            m = _re.match(r'`([^`]+)`', bullet_text)
            if m:
                exempted.add(m.group(1).strip())
            else:
                idx = bullet_text.find(' — ')
                exempted.add(bullet_text[:idx].strip() if idx >= 0 else bullet_text.split()[0] if bullet_text else "")

    # Read marker exemptions from the test files themselves.
    try:
        from refactory_dsh import worktree as _wt
        found = _wt.recorded(ctx.workdir)
        if found is not None:
            for tf in non_guarding:
                tf_path = Path(found.path) / tf
                if tf_path.is_file():
                    try:
                        content = tf_path.read_text(errors="replace")
                        if _re.search(r'#\s*guardedness-exempt:', content):
                            exempted.add(tf)
                    except OSError:
                        pass
    except (ValueError, AttributeError):
        pass

    return [t for t in non_guarding if t not in exempted]


def report_artifact(ctx: Any) -> dict[str, Any]:
    """Say what was produced, so a run's output is discoverable rather than inferred."""
    from refactory_dsh.authoring_evidence import require_completion

    return _report_artifact(ctx, require_completion)


def report_judged(ctx: Any) -> dict[str, Any]:
    """Design mode's report: the overwatch judged the evidence map and the correction audit.

    So the run is complete when every review's sign-off is current for the artifact it
    reports (`authoring_evidence.current_problems`). The map's own shape and the audit's
    results were evidence for that judgment (`audit_corrections`), and they do not
    refuse the report a second time, as `require_completion` would.
    """
    from refactory_dsh import authoring_evidence as evidence
    from refactory_dsh.invocations import REVIEWERS

    def require(ctx: Any) -> None:
        problems = evidence.current_problems(ctx, REVIEWERS[:-1])
        if problems:
            raise ValueError("authoring sign-off is incomplete or stale: " + "; ".join(problems))

    return _report_artifact(ctx, require)


def _report_artifact(ctx: Any, require: Any) -> dict[str, Any]:
    if worktree.recorded(ctx.workdir) is not None:
        # Where the worktree stands first, so a moved HEAD is named as such and not as a
        # stale sign-off.
        worktree.require_current(ctx.workdir)

    # A fact that is not a mapping is no draft (`delivery.draft_fact` says so as a warning).
    from refactory_dsh.authoring_evidence import require_current_evidence

    draft_delivery = delivery.draft_fact(ctx)
    if draft_delivery:
        require_current_evidence(ctx)
    else:
        require(ctx)
    ev = ctx.read_json("authoring-evidence.json", None)
    audit = ctx.read_json("intent-audit.json", None)
    # Collect planning model info from worker receipts (R5).
    # Sort by modification time so the latest receipt for each node wins.
    planning_models: dict[str, str | None] = {}
    workers_dir = ctx.workdir / ".refactory" / "workers"
    if workers_dir.is_dir():
        try:
            receipt_files = sorted(
                (f for f in workers_dir.iterdir() if f.suffix == ".json"),
                key=lambda f: f.stat().st_mtime,
            )
        except OSError:
            receipt_files = []
        for wf in receipt_files:
            try:
                rec = json.loads(wf.read_text())
                nid = rec.get("node_id", "")
                if nid == "design_doc":
                    route = rec.get("actual_route") or rec.get("requested_route") or {}
                    planning_models[nid] = route.get("model") or None
            except (json.JSONDecodeError, OSError):
                pass

    from refactory_dsh.authoring_evidence import _check_skip_provenance
    skip_prov = _check_skip_provenance(ctx.workdir)
    check_skipped_info: dict[str, Any] | None = None
    check_note = ""
    if skip_prov is not None and skip_prov.get("source") == "person":
        check_skipped_info = {
            "source": skip_prov["source"],
            "at": skip_prov["at"],
            "via": "graph-control",
            "node": skip_prov["node"],
        }
        check_note = (f"the check did not run (skipped by the person at {skip_prov['at']} "
                      f"via graph-control on node {skip_prov['node']}; covers later attempts)")

    report_payload: dict[str, Any] = {
        "name": ctx.facts.get("name"),
        "artifact": ctx.facts.get("artifact"),
        "files": ctx.facts.get("files", []),
        "check": ctx.facts.get("check"),
        "checked": bool(ev and isinstance(ev.get("validation"), dict)
                        and ev["validation"].get("status") == "pass"),
        "validation_evidence": ev,
        "intent_audit": audit,
    }
    if check_skipped_info is not None:
        report_payload["check_skipped"] = check_skipped_info
        report_payload["check_note"] = check_note
    if planning_models:
        report_payload["planning_models"] = planning_models
    # Surface process notes with their followed/not-followed statement.
    process_notes = (ev or {}).get("process_notes", [])
    if process_notes:
        audit_notes = {item["id"]: item for item in (audit or {}).get("process_notes", [])
                       if isinstance(item, dict)}
        report_payload["process_notes"] = [
            {**note, **({"followed": audit_notes[note["id"]]["followed"],
                         "detail": audit_notes[note["id"]].get("detail", "")}
                        if note["id"] in audit_notes and isinstance(audit_notes[note["id"]].get("followed"), bool)
                        else {"followed": None, "detail": "compliance not verified by audit"})}
            for note in process_notes
        ]
    if draft_delivery:
        report_payload["draft"] = True
        ev_for_draft = ctx.read_json("authoring-evidence.json", None)
        report_payload["check_passed"] = bool(
            ev_for_draft and isinstance(ev_for_draft.get("validation"), dict)
            and ev_for_draft["validation"].get("status") == "pass"
        )
        report_payload["draft_reason"] = draft_delivery.get("reason", "")
        report_payload["draft_unresolved"] = draft_delivery.get("unresolved", [])
    payload = repo_change.report(ctx, "design.json", report_payload)
    return {"designed": json.dumps(payload, default=str)}


# ── design mode's QA tail: the reviews report, two judges decide ─────────────
#
# The review fork's reports are prose, and reading a decision out of prose by its
# markers ended runs whose reviewers only quoted a finding's format (b37b, measure4-
# dm149), and refused a change the overwatch had passed because a correction removed
# the site the audit looked for (b39b). So in design mode the reviews go, whole, to a
# QA judge, and the evidence goes to the overwatch, and each judge writes a JSON
# verdict (`refactory_dsh.judges`) that its gate reads. The check is decided before
# either judge runs (`gate_check`) and stays binding. create, edit and the fast modes
# keep `create_ops.gate_qa` and `create_ops.gate_overwatch`.

#: The reviewers that must report on every attempt, with how the QA judge names them.
DESIGN_QA_REVIEWS = (
    ("reviews/health-check.md", "health checker"),
    ("reviews/code-review.md", "code reviewer"),
    ("reviews/qa.md", "QA director"),
)

#: The design-blind verifier's report: an optional branch of the fork, so a verifier
#: that times out has none, and the QA judge reads the round without it.
VERIFICATION = "reviews/verification.md"

QA_SYNTHESIS = "reviews/qa-synthesized.md"


def _design_section(ctx: Any, heading: str) -> str:
    """The body of design.md's ``## heading`` section, or ``""``."""
    if not ctx.exists("design.md"):
        return ""
    lines: list[str] = []
    inside = False
    for line in ctx.read_text("design.md").splitlines():
        if line.strip().lower() == f"## {heading.lower()}":
            inside = True
            continue
        if inside and line.startswith("## "):
            break
        if inside:
            lines.append(line)
    return "\n".join(lines).strip()


def _diff_stat(ctx: Any) -> str:
    found = worktree.recorded(ctx.workdir)
    if found is None:
        files = ctx.facts.get("files") or []
        return "\n".join(str(name) for name in files) if isinstance(files, list) else str(files)
    try:
        return worktree.git(Path(found.path), "diff", "--stat", found.base, "HEAD").strip("\n")
    except Exception as exc:  # the judge reads the diff itself then
        return f"(the diff stat could not be computed: {exc})"


#: What a round of judging leaves: the judges' verdicts, the QA judge's evidence map
#: and the correction audit. Each round's are its own (`synthesize_qa`).
JUDGING_FILES = ("qa-judge.json", "overwatch-verdict.json", "intent-audit.json",
                 "correction-audit.json")

#: Where `synthesize_qa` keeps an earlier round's judging files.
JUDGING_HISTORY = Path(".refactory") / "judging"


def _set_aside_judging(ctx: Any) -> None:
    """Move the last round's judging files out of the run directory, keeping them.

    A judge a person skips, or a correction audit they skip, leaves nothing for its
    gate or the overwatch to read, rather than the last round's verdict or audit.
    """
    import uuid
    from datetime import datetime, timezone

    present = [name for name in JUDGING_FILES if ctx.path(name).is_file()]
    if not present:
        return
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]
    folder = ctx.workdir / JUDGING_HISTORY / stamp
    folder.mkdir(parents=True, exist_ok=True)
    for name in present:
        ctx.path(name).rename(folder / name)


def synthesize_qa(ctx: Any) -> dict[str, Any]:
    """Op: put this attempt's reviews, its check and its scope in one document for the QA judge.

    Each review goes in whole and unread: nothing here looks for findings, markers or
    verdicts, so how a reviewer words its report decides nothing. The verifier's report
    goes in when there is one. A review that left nothing is said to be missing, because
    an absent review is not a passed one.

    It also starts the round's judging: the last round's verdicts, evidence map and audit
    are set aside (`_set_aside_judging`), and the judges' reread counts start again.
    """
    _set_aside_judging(ctx)
    sections: list[str] = []
    missing: list[str] = []
    for name, who in DESIGN_QA_REVIEWS:
        text = ctx.read_text(name).strip() if ctx.exists(name) else ""
        if text:
            sections.append(f"## {name} (the {who})\n\n{text}")
        else:
            missing.append(name)
            sections.append(f"## {name} (the {who})\n\nNO REPORT: the {who} left nothing "
                            "for this attempt. An absent review is not a passed one.")
    verification = ctx.read_text(VERIFICATION).strip() if ctx.exists(VERIFICATION) else ""
    if verification:
        sections.append(f"## {VERIFICATION} (the design-blind verifier)\n\n{verification}")
    record = ctx.read_json("check-record.json", {}) if ctx.exists("check-record.json") else {}
    status = record.get("status") if isinstance(record, dict) else None
    check = (f"## The check\n\n`{ctx.facts.get('check') or ''}` "
             f"{'passed' if status == 'pass' else 'ran with status ' + repr(status)} on this "
             "attempt; a failing check never reaches this step.\n\n"
             + str(ctx.facts.get("validation") or "").strip())
    changes = _design_section(ctx, "Changes")
    body = (
        "# QA synthesis\n\n"
        f"{len(DESIGN_QA_REVIEWS) - len(missing)} of {len(DESIGN_QA_REVIEWS)} reviews reported"
        + (", and the design-blind verifier" if verification else "") + ".\n\n"
        + "\n\n".join(sections) + "\n\n"
        + check.rstrip() + "\n\n"
        + "## The diff stat\n\n```\n" + _diff_stat(ctx) + "\n```\n\n"
        + "## The design's Changes\n\n" + (changes or "(design.md has no Changes section)") + "\n"
    )
    ctx.write_text(QA_SYNTHESIS, body)
    return {"qa": body, "qa_missing": len(missing),
            "qa_judge_rereads": 0, "qa_judge_reread": "",
            "overwatch_rereads": 0, "overwatch_reread": "",
            "correction_audit": "(the correction audit has not run this round)"}


def _review_questions_advisory(ctx: Any) -> str:
    """The repository questions the reviews left unanswered: listed on the pull request,
    never a reason to send the change back (as in `create_ops.gate_qa`)."""
    if str(ctx.facts.get("deliverable_kind", "")) != "repository_change" or not ctx.exists(
            "changed-functions.json"):
        return ""
    from refactory_dsh.authoring_evidence import advisory_repo_questions, check_review_questions
    from refactory_dsh.changed_functions import review_functions

    try:
        changed = review_functions(json.loads(ctx.read_text("changed-functions.json")))
    except ValueError:
        return ""
    if not changed:
        return ""
    nodes = {"reviews/health-check.md": "health_checker", "reviews/code-review.md": "code_reviewer",
             "reviews/qa.md": "qa_director"}
    problems = [problem for name, _ in DESIGN_QA_REVIEWS if ctx.exists(name)
                for problem in check_review_questions(ctx.read_text(name), changed, nodes[name])]
    return advisory_repo_questions(problems) if problems else ""


def _send_back(ctx: Any, gate_file: str, fact: str, reason: str) -> Any:
    """Write why the change goes back, and spend a review attempt on it (`create_ops.repair`),
    which raises BuildLoopStop at the review limit."""
    from refactory_dsh.create_ops import repair

    ctx.write_text(gate_file, reason + "\n")
    ctx.set_fact(fact, reason)
    return repair(ctx, [reason], kind="review")


def _read_judge(ctx: Any, name: str, verdicts: tuple[str, ...], *, who: str,
                count: str, prompt_fact: str) -> tuple[Any, Any, str]:
    """``(judgment, reread, note)``: the judge's verdict, or a reread, or why there is none.

    A file with no readable verdict and a line that starts ``UNAVAILABLE:`` stops the run,
    as create's overwatch gate does; a readable verdict says so in a must-fix item, which
    its gate stops on, so a prose line beside it decides nothing. Any other file that is
    missing or is not one JSON verdict is reread: the judge is
    asked again, with the reason in its prompt (``prompt_fact``) and no builder round,
    as many times a round as the frozen ``judge_rereads`` knob allows (``count`` counts
    them; `synthesize_qa` starts it again each round). After that the gate goes on
    without a verdict, with ``note`` saying so.
    """
    from refactory_dsh import authoring_evidence as evidence
    from refactory_dsh import judges

    text = ""
    try:
        text = judges.text_of(ctx, name)
        return judges.parse(text, verdicts, name=name), None, ""
    except judges.Unreadable as exc:
        problem = str(exc)
    line = judges.UNAVAILABLE_LINE.search(text)
    if line:
        evidence.stop_unrepairable("unavailable", line[0].strip())
    used = int(ctx.facts.get(count) or 0)
    allowed = int(ctx.knob("judge_rereads", 1))
    if used < allowed:
        ctx.set_fact(count, used + 1)
        ctx.set_fact(prompt_fact, (
            f"\n## Your last verdict could not be read\n{problem}. Write `{name}` again: "
            "one JSON object in the shape above and nothing else that is JSON.\n"))
        return None, Verdict("reread", f"the {who}'s verdict could not be read ({problem}); "
                                       "it is asked again, with no builder round"), ""
    return None, None, (f"the {who}'s verdict could not be read ({problem})"
                        + (", also when it was asked again" if allowed else ""))


def _quoting(ctx: Any, note: str, name: str) -> str:
    """``note``, then what the judge wrote, so a reader of the pull request sees it."""
    from refactory_dsh import judges

    said = judges.excerpt(ctx, name)
    return note + (f". It wrote: {said}" if said else "")


def gate_qa(ctx: Any) -> Any:
    """Gate after the QA judge: route on ``qa-judge.json``, never on the reviews' prose.

    The change goes back to emit only when the judge says ``redo`` and names a must-fix
    item that holds it back (`judges.blocks`: critical or high, or it breaks an ask, a
    correction or the health check). The feedback is the must-fix items, and the round
    spends a review attempt; at the review limit that is a BuildLoopStop. Otherwise it
    goes on to the correction audit and the overwatch, and the follow-ups and any
    remaining must-fix items go to the pull request. A judge file that is missing or is
    not one JSON verdict is reread once (`_read_judge`); if it is still unreadable the
    change goes on with a note in the gate's status and the follow-ups, because the
    overwatch still judges it.

    Before the judge is read, two things are structural rather than judged, as they were:
    a review that left no report, or whose sign-off is not current for this attempt, sends
    the change back so the reviews run again; and a review that says a tool it needs is
    ``UNAVAILABLE:`` stops the run for a person.
    """
    from refactory_dsh import authoring_evidence as evidence
    from refactory_dsh import judges
    from refactory_dsh.create_ops import record_follow_ups

    for name, _ in DESIGN_QA_REVIEWS:
        if ctx.exists(name):
            unavailable = judges.UNAVAILABLE_LINE.search(ctx.read_text(name))
            if unavailable:
                evidence.stop_unrepairable("unavailable", unavailable[0])
    stale = evidence.current_problems(ctx, ("health_checker", "code_reviewer", "qa_director"))
    missing = [name for name, _ in DESIGN_QA_REVIEWS
               if not (ctx.exists(name) and ctx.read_text(name).strip())]
    if stale or missing:
        reason = "reloop: " + "; ".join(
            [*stale, *(["a review did not run: " + ", ".join(missing)] if missing else [])])
        return _send_back(ctx, "reviews/qa-gate.txt", "qa_gate", reason)
    judgment, reread, note = _read_judge(ctx, judges.QA_JUDGE, judges.QA_VERDICTS,
                                         who="QA judge", count="qa_judge_rereads",
                                         prompt_fact="qa_judge_reread")
    if reread is not None:
        ctx.write_text("reviews/qa-gate.txt", f"reread: {reread.reason}\n")
        return reread
    advisory = _review_questions_advisory(ctx)
    if judgment is not None:
        stop = judges.unavailable(judgment)
        if stop:
            evidence.stop_unrepairable("unavailable", stop)
        if judgment.verdict == "redo" and judgment.blocking:
            return _send_back(ctx, "reviews/qa-gate.txt", "qa_gate",
                              "reloop: " + judges.feedback(judgment, "QA judge"))
        # Nothing holds the change back. The items the judge named go to the pull request.
        held = ("the QA judge asked for a redo, but nothing it named is critical or high or "
                "breaks an ask, a correction or the health check")
        suffix = f" ({held})" if judgment.verdict == "redo" else " (the QA judge proceeded with this listed as must-fix)"
        follow = [*judgment.follow_ups,
                  *(f"{judges.must_fix_line(item)}{suffix}" for item in judgment.must_fix)]
        summary = held if judgment.verdict == "redo" else "the QA judge found nothing that must be fixed"
        dismissed = [f"dismissed [{item['source']}] {item['finding']} — {item['why']}"
                     for item in judgment.dismissed]
    else:
        note = _quoting(ctx, note + "; the change went on to the overwatch without it",
                        judges.QA_JUDGE)
        follow, summary, dismissed = [note], note, []
    record_follow_ups(ctx, "qa", follow)
    # The advisory goes last: delivery reads the repository-question advisory to the end.
    notes = "".join(f"{line}\n" for line in (
        *(f"follow-up: {item}" for item in follow), *dismissed, advisory) if line)
    ctx.write_text("reviews/qa-gate.txt", f"proceed: {summary}\n" + notes)
    ctx.set_fact("qa_gate", f"({summary})" + (f"\n{advisory}" if advisory else ""))
    ctx.write_text("validation.txt", "(reviews passed)\n")
    ctx.set_fact("validation", "(reviews passed)")
    return "proceed"


def _correction_audit_summary(audit: Any, problems: list[str], map_problems: list[str],
                              advisories: list[str]) -> str:
    """The correction audit as the overwatch reads it in its prompt."""
    items = [item for item in audit if isinstance(item, dict)] if isinstance(audit, list) else []
    lines: list[str] = []
    if not items:
        lines.append("No correction or constraint from the person has sites to audit.")
    for item in items:
        lines.append(f"### {item.get('id', '?')}: {item.get('text_preview', '')}")
        for number, clause in enumerate(item.get("clauses", []), 1):
            if not isinstance(clause, dict):
                continue
            lines.append(f"- clause {number} ({clause.get('status', '?')}): {clause.get('clause', '')}")
            for site in clause.get("sites", []):
                if not isinstance(site, dict):
                    continue
                tests = ", ".join(f"{test.get('name')} → {test.get('revert_result', 'not run')}"
                                  for test in site.get("tests", []) if isinstance(test, dict))
                removed = f" ({site['removed']})" if site.get("removed") else ""
                lines.append(f"  - {site.get('site', '?')}: {site.get('status', '?')}{removed}"
                             + (f"; tests: {tests}" if tests else ""))
    for title, entries in (("What the audit measured as not met", problems),
                           ("Problems in the evidence map (intent-audit.json)", map_problems),
                           ("Checks to confirm by hand", advisories)):
        if entries:
            lines += ["", f"### {title}", *(f"- {entry}" for entry in entries)]
    return "\n".join(lines)


def audit_corrections(ctx: Any) -> dict[str, Any]:
    """Op before the overwatch: measure each correction the person gave, as evidence.

    It is the audit `create_ops.gate_overwatch` runs (`authoring_evidence.
    correction_audit_problems`), on the sites the QA judge's evidence map names for each
    correction: whether each site is in the diff, and whether reverting it makes the named
    tests fail. A site whose code the change deletes is reported as removed. A site the
    change leaves as the base has it is unchanged, and measured as not met, for the
    overwatch to decide: what a correction that drops a planned change leaves, and what a
    correction the change never applied leaves, look the same. Nothing here routes:
    the overwatch reads ``correction-audit.json`` and this summary and decides each
    correction itself. Problems in the evidence map's own shape, which used to send the
    overwatch round again for free, are listed here for it too.
    """
    from refactory_dsh import authoring_evidence as evidence

    # This round's audit, even when there is nothing to measure: the overwatch reads it.
    ctx.write_json("correction-audit.json", [])
    problems = evidence.correction_audit_problems(ctx, removed_sites=True)
    if not ctx.exists("site-revert-results.json"):
        ctx.write_json("site-revert-results.json", {})
    summary = _correction_audit_summary(ctx.read_json("correction-audit.json", []), problems,
                                        evidence.audit_problems(ctx),
                                        evidence.correction_audit_advisories(ctx))
    return {"correction_audit": summary}


def gate_overwatch(ctx: Any) -> Any:
    """Gate after the overwatch: route on ``overwatch-verdict.json``.

    The change goes back to emit, spending a review attempt (a BuildLoopStop at the
    limit), when the overwatch finds a correction the person gave not met, whatever its
    verdict, or says ``redo`` and names a must-fix item that holds the change back
    (`judges.blocks`) or a correction it could not judge. The feedback names each one,
    with its evidence. Otherwise the change goes to the report, and the follow-ups, any
    remaining must-fix items and every correction the overwatch did not judge go to the
    pull request, named. The correction audit never refuses by itself: it was the
    overwatch's evidence. A verdict file that is missing or is not one JSON verdict is
    reread once (`_read_judge`); if it is still unreadable the change goes on with a
    note, because sending the builder back cannot repair a judge's file.
    """
    from refactory_dsh import authoring_evidence as evidence
    from refactory_dsh import judges
    from refactory_dsh.create_ops import record_follow_ups

    stale = evidence.current_problems(ctx, ("overwatch",))
    if stale:
        return _send_back(ctx, "reviews/overwatch-gate.txt", "overwatch_review",
                          "reloop: evidence problems: " + "; ".join(stale))
    judgment, reread, note = _read_judge(ctx, judges.OVERWATCH_VERDICT, judges.OVERWATCH_VERDICTS,
                                         who="overwatch", count="overwatch_rereads",
                                         prompt_fact="overwatch_reread")
    if reread is not None:
        ctx.write_text("reviews/overwatch-gate.txt", f"reread: {reread.reason}\n")
        return reread
    by_hand = evidence.correction_audit_advisories(ctx)
    advisory = evidence.advisory_correction_audit(by_hand) + "\n" if by_hand else ""
    if judgment is None:
        note = _quoting(ctx, note + "; acceptance was not judged, so check each ask and "
                        "correction by hand", judges.OVERWATCH_VERDICT)
        record_follow_ups(ctx, "overwatch", [note])
        ctx.write_text("reviews/overwatch-gate.txt", f"proceed: {note}\n" + advisory)
        ctx.set_fact("overwatch_review", "(the overwatch's verdict could not be read)")
        ctx.write_text("validation.txt", "(overwatch verdict unreadable)\n")
        ctx.set_fact("validation", "(overwatch verdict unreadable)")
        return "proceed"
    stop = judges.unavailable(judgment)
    if stop:
        evidence.stop_unrepairable("unavailable", stop)
    if judgment.verdict == "redo" and (judgment.blocking or judgment.unmet or judgment.unjudged):
        return _send_back(ctx, "reviews/overwatch-gate.txt", "overwatch_review",
                          "reloop: " + judges.feedback(judgment, "overwatch"))
    if judgment.unmet:
        # A pass that leaves a correction the person gave unmet does not ship.
        return _send_back(ctx, "reviews/overwatch-gate.txt", "overwatch_review",
                          "reloop: " + judges.feedback(judgment, "overwatch", passed=True))
    held = ("the overwatch asked for a redo, but nothing it named is critical or high or "
            "breaks an ask, a correction or the health check")
    suffix = f" ({held})" if judgment.verdict == "redo" else " (the overwatch passed with this listed as must-fix)"
    judged = {item["id"] for item in judgment.corrections}
    required = [item["id"] for item in (ctx.read_json(evidence.EVIDENCE, {}) or {}).get("requirements", [])
                if isinstance(item, dict) and item.get("kind") in ("correction", "constraint")]
    follow = [
        *judgment.follow_ups,
        *(f"{judges.must_fix_line(item)}{suffix}" for item in judgment.must_fix),
        *(f"correction {judges.correction_line(item)} (the overwatch could not judge it)"
          for item in judgment.unjudged),
        *(f"correction {key}: the overwatch did not judge it" for key in required if key not in judged),
    ]
    record_follow_ups(ctx, "overwatch", follow)
    status = held if judgment.verdict == "redo" else "overwatch passed"
    ctx.write_text("reviews/overwatch-gate.txt", f"proceed: {status}\n"
                   + "".join(f"follow-up: {item}\n" for item in follow)
                   + "".join(f"correction {judges.correction_line(item)}\n"
                             for item in judgment.corrections)
                   + advisory)
    ctx.set_fact("overwatch_review", "(overwatch passed)")
    ctx.write_text("validation.txt", "(overwatch passed)\n")
    ctx.set_fact("validation", "(overwatch passed)")
    return "proceed"
