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
from refactory_dsh.nn import Verdict

_FILE_EXT_RE = _re.compile(
    r'\b[\w/.-]+\.(?:py|ts|js|mjs|cjs|jsx|tsx|json|md|yaml|yml|toml|go|rs|rb|java|c|cpp|h|sh|css|html)\b'
)

STAGING = "staging"

#: Planning steps that must not change the target or the worktree (#347).
#: They write only into the run directory; a change they make to the target
#: or the worktree fails the step and names what changed.
PLANNING_STEPS = ('research_director', 'pitfalls_researcher', 'strategy_director', 'synthesize_strategy', 'design_doc')

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
    return {"name": name, "brief": brief, "check": check, "validation": "(no check has run yet)",
            "research_review": "(no research review has run yet)", "research_scope": research_scope,
            "design_review_feedback": "(no design review has run yet)",
            "design_review_findings": "(no findings yet)",
            **pitfalls_facts,
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
            problems.append(
                "rule-site(s) with no [changed] or [left: <reason>] marker: "
                + ", ".join(parts)
            )
        if design_contract.title_is_run_name(design_text, run_name):
            problems.append(
                "the design's title is the run name; it must say what the change does"
            )

    # ── mechanical check 3a: failure trace section ──
    if design_text:
        if not design_contract.has_non_empty_section(design_text, design_contract.FAILURE_TRACE):
            problems.append(
                "the design is missing the required ## Failure trace section (or it is empty)"
            )

    # ── mechanical check 3b: ask mapping section ──
    if design_text:
        if not design_contract.has_non_empty_section(design_text, design_contract.ASK_MAPPING):
            problems.append(
                "the design is missing the required ## Ask mapping table (or it is empty)"
            )

    # ── mechanical check 4: pitfall coverage ──
    has_pitfalls = ctx.facts.get("has_pitfalls_step", False)
    if has_pitfalls:
        from refactory_dsh import pitfalls as pitfalls_mod

        if ctx.exists("pitfalls.json"):
            try:
                pitfalls_raw = ctx.read_text("pitfalls.json")
                pitfalls_data = json.loads(pitfalls_raw)
                if isinstance(pitfalls_data, list):
                    addressed, aside, pit_missing = pitfalls_mod.pitfall_coverage(
                        design_text, pitfalls_data)
                    missing_high = [pid for pid in pit_missing
                                   if any(p.get("id") == pid and p.get("risk") == "high"
                                          for p in pitfalls_data)]
                    if missing_high:
                        problems.append(
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
                            coverage_lines.append(f"  {pid} ({risk}): MISSING")
                    if coverage_lines:
                        ctx.set_fact("pitfall_coverage",
                                     "Pitfall coverage:\n" + "\n".join(coverage_lines))
            except (json.JSONDecodeError, UnicodeDecodeError):
                problems.append("pitfalls.json exists but is not valid JSON")
        else:
            problems.append(
                "pitfalls.json is missing (gate_pitfalls should have validated it)")
    else:
        ctx.set_fact("pitfall_coverage",
                     "pitfalls check skipped: no pitfalls_researcher in this graph")

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
        coverage = ctx.facts.get("pitfall_coverage", "")
        if coverage:
            ctx.set_fact("design_review_findings",
                         ctx.facts["design_review_findings"] + "\n\n" + coverage)
        return "proceed"

    report = "\n".join(f"- {p}" for p in problems)

    if used >= allowed:
        # Exhausted: proceed to gate_strategy with findings shown.
        ctx.set_fact("design_review_feedback", report)
        ctx.set_fact("design_review_findings", report)
        coverage = ctx.facts.get("pitfall_coverage", "")
        if coverage:
            ctx.set_fact("design_review_findings", report + "\n\n" + coverage)
        ctx.write_text("reviews/design-review-feedback.txt", report + "\n")
        return "proceed"

    # Reloop to design_doc with the findings.
    ctx.write_text("reviews/design-review-feedback.txt", report + "\n")
    ctx.set_fact("design_review_feedback", report)
    ctx.set_fact("design_review_findings",
                 "(no findings yet)")
    return Verdict("reloop", report)


def gate_pitfalls(ctx: Any) -> str:
    """Validate pitfalls.json structure; reloop to pitfalls_researcher when invalid.

    When attempts are exhausted, stops the run as gate_research does.
    """
    from refactory_dsh import pitfalls as pitfalls_mod

    raw = ctx.read_text("pitfalls.json") if ctx.exists("pitfalls.json") else ""
    source = str(ctx.facts.get("source") or ctx.facts.get("artifact") or "")
    problems = pitfalls_mod.validate_pitfalls(raw, source, run_dir=str(ctx.workdir))

    if not problems:
        ctx.write_text("pitfalls-review.txt", "ok\n")
        ctx.set_fact("pitfalls_review", "(pitfalls validated)")
        return "proceed"

    allowed = int(ctx.knob("pitfalls_attempts", 2))
    used = int(ctx.facts.get("pitfalls_attempts_used", 0)) + 1
    ctx.set_fact("pitfalls_attempts_used", used)

    report = "\n".join(f"- {p}" for p in problems)
    ctx.write_text("pitfalls-review.txt", report + "\n")
    ctx.set_fact("pitfalls_review", report)

    if used >= allowed:
        raise ValueError(
            f"pitfalls are still not valid after {used} attempt(s):\n{report}"
        )
    return Verdict("reloop", report)


def apply_cwd_escaped(result: Any, outcome: dict[str, Any]) -> None:
    """Record ``cwd_escaped`` into *outcome* when *result* shows a directory escape.

    Both ``check_artifact`` and ``fast_ops.focused_check`` apply this logic:
    when ``sandbox.run_command`` was called with a ``worktree_root`` and the
    command left that directory, ``result.cwd_outside`` is set.  This helper
    centralises the recording so the logic is not replicated in each call site.
    """
    if getattr(result, "cwd_outside", None):
        outcome["cwd_escaped"] = result.cwd_outside


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
        ctx.write_json("check-record.json", {
            "status": "refused", "validation": detail,
            "attempt": int(ctx.facts.get("attempts_used", 0)),
        })
        return Verdict("reloop", detail)
    command = str(ctx.facts["check"])
    result = repo_change.run_check(ctx, command, int(ctx.knob("check_timeout_s", 180)))
    detail = "\n".join((result.problem(), result.stdout or "", result.stderr or "")).strip()
    outcome = {"command": command, "status": "pass" if result.ok else evidence.check_failure_kind(
        result, budget=getattr(ctx, "budget", None)),
        "stdout": result.stdout, "stderr": result.stderr, "returncode": result.returncode,
        "timed_out": result.timed_out, "elapsed_s": result.elapsed_s}
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
            })
            return repair(ctx, [detail])
        ctx.set_fact("validation", "(the check passed)")
        ctx.write_json("check-record.json", {
            "status": "pass", "validation": "(the check passed)",
            "attempt": int(ctx.facts.get("attempts_used", 0)),
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

    # A failure the unchanged base has too is not the attempt's (`base_check.compare_failures`).
    found = worktree.recorded(ctx.workdir)
    if found is not None:
        filtered = base_check.filter_check(
            ctx, result, command, int(ctx.knob("check_timeout_s", 180)),
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

    ctx.write_json("check-record.json", {
        "status": "fail",
        "validation": detail[:4000],
        "digest": digest_text,
        "attempt": int(ctx.facts.get("attempts_used", 0)),
        "command": command,
        "runners": digest.to_dict().get("runners", []),
        "raw_tail": digest.raw_tail[-500:],
    })

    return repair(ctx, [detail])


def gate_verification(ctx: Any) -> str:
    """Proceed when the verifier finds no blocking issues; reloop to emit otherwise.

    Verifier findings do NOT spend ``review_attempts``: the verifier's noise
    is unmeasured, so it must not end a run on its own.  The gate tracks its
    own iteration count via ``verification_iterations_used`` and proceeds
    after ``VERIFIER_GATE_LIMIT`` advisory reloops, recording unresolved
    findings in ``reviews/verification-unresolved.md`` for the reviews,
    overwatch audit and PR Verification section.
    """
    from refactory_dsh.verification import VERIFIER_GATE_LIMIT, parse_verification_findings

    findings = parse_verification_findings(ctx)
    if not findings:
        ctx.write_text("reviews/verification-gate.txt", "proceed: no blocking finding\n")
        return "proceed"

    feedback = "\n".join(findings)
    ctx.write_text("reviews/verification-gate.txt", feedback + "\n")

    used = int(ctx.facts.get("verification_iterations_used", 0))
    ctx.set_fact("verification_iterations_used", used + 1)

    if used + 1 >= VERIFIER_GATE_LIMIT:
        # Proceed with unresolved findings — never end a run on verifier noise.
        ctx.write_text("reviews/verification-unresolved.md", feedback + "\n")
        return "proceed"

    ctx.write_text("validation.txt", feedback + "\n")
    ctx.set_fact("validation", feedback)
    return Verdict("reloop", feedback)


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

    if worktree.recorded(ctx.workdir) is not None:
        # Where the worktree stands first, so a moved HEAD is named as such and not as a
        # stale sign-off.
        worktree.require_current(ctx.workdir)
    require_completion(ctx)
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
                if nid in ("design_doc", "design_review"):
                    route = rec.get("actual_route") or rec.get("requested_route") or {}
                    planning_models[nid] = route.get("model") or None
            except (json.JSONDecodeError, OSError):
                pass

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
    payload = repo_change.report(ctx, "design.json", report_payload)
    return {"designed": json.dumps(payload, default=str)}
