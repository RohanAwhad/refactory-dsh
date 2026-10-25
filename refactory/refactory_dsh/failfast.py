"""Fail-fast targeted pre-pass, stuck detection, and convergence history helpers.

Shared by ``check_artifact`` (design mode) and ``focused_check`` (fast mode)
so that the logic is not duplicated across two entry points. Design mode's
``prepare_check`` runs the same pre-pass as its self-check (b41a): given a
selection, it runs those tests in chunks within a time limit.
"""

from __future__ import annotations

import math
import re
import os
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, Callable

#: How many chunks the self-check cuts its selection into. Each chunk is its own
#: targeted command, so a limit that runs out stops between chunks and every
#: finished chunk reports; more chunks cut closer to the limit, but each pays the
#: runner's start-up again.
SELF_CHECK_CHUNKS = 6


@dataclass
class PrepassResult:
    """Outcome of a targeted pre-pass run."""

    ran: bool  # whether a targeted command was executed
    early_stop: bool  # whether the pre-pass stopped early
    targeted_command: str | None  # the targeted command string, or None
    check_mode: str  # "targeted" if early_stop, else "full"
    result: Any | None  # SandboxResult from the targeted run, or None
    digest_text: str  # formatted digest, or ""
    runners: list[dict] = field(default_factory=list)  # parsed runner dicts
    raw_tail: str = ""  # raw_tail from the targeted digest
    # ── the self-check's account (b41a); empty for the dm-128 pre-pass ──
    selection: list[str] = field(default_factory=list)  # what it set out to run, in order
    ran_ids: list[str] = field(default_factory=list)  # in chunks that finished
    cut_ids: list[str] = field(default_factory=list)  # not started, or killed by the limit
    failing_ids: list[str] = field(default_factory=list)  # failed, and not inherited
    commands: list[str] = field(default_factory=list)  # one per chunk that ran
    notes: list[str] = field(default_factory=list)  # why a chunk did not count
    limit_s: float = 0.0
    elapsed_s: float = 0.0
    outputs: list[str] = field(default_factory=list)  # each failing chunk's output
    left_out: list[str] = field(default_factory=list)  # failures not shown: inherited


def _skip() -> PrepassResult:
    return PrepassResult(
        ran=False, early_stop=False, targeted_command=None,
        check_mode="full", result=None, digest_text="", runners=[], raw_tail="",
    )


def self_check_limit(self_check_timeout_s: float, check_timeout_s: float) -> float:
    """The self-check's time limit: its own knob, and never more than a third of the check's."""
    return float(min(self_check_timeout_s, check_timeout_s // 3))


def _runner_of(name: str) -> str:
    from refactory_dsh.check_digest import _SCRIPT_TEST_FILE

    if " > " in name or _SCRIPT_TEST_FILE.search(name.split("::")[0]):
        return "vitest"
    return "pytest" if "::" in name or name.endswith(".py") else "other"


def _chunks(selection: list[str]) -> list[list[str]]:
    """The selection in order, cut into about `SELF_CHECK_CHUNKS` chunks of one runner each.

    The targeted command keeps the check's ``&&`` between steps, so a chunk that held
    two runners' tests would not run the second runner's once the first failed.
    """
    size = max(1, math.ceil(len(selection) / SELF_CHECK_CHUNKS))
    chunks: list[list[str]] = []
    for name in selection:
        if chunks and len(chunks[-1]) < size and _runner_of(chunks[-1][-1]) == _runner_of(name):
            chunks[-1].append(name)
        else:
            chunks.append([name])
    return chunks


def _output(result: Any) -> str:
    return "\n".join(filter(None, [
        getattr(result, "stdout", None) or "",
        getattr(result, "stderr", None) or "",
    ])).strip()


def _chunk_failures(result: Any, inherited: set[str]) -> tuple[set[str], str]:
    """The chunk's failures that are not inherited, or why the chunk says nothing.

    The same rule as the pre-pass's early stop: a preparation failure, a failure that
    is not the code's (a timeout, a signal), and pytest's "nothing collected" exits are
    not a verdict on the tests, and neither is a failure that names no test.
    """
    from refactory_dsh import authoring_evidence as evidence
    from refactory_dsh import base_check

    if getattr(result, "ok", False):
        return set(), ""
    if getattr(result, "prep_failure", None) is not None:
        return set(), "its environment could not be prepared"
    if evidence.check_failure_kind(result) != "code_failure":
        return set(), "it did not fail on the code (a timeout or a signal)"
    rc = getattr(result, "returncode", None)
    if rc in (4, 5):
        return set(), f"pytest collected no test (exit {rc})"
    found = base_check.parse_test_failures(_output(result))
    if not found:
        return set(), f"it failed (exit {rc}) without naming a failing test"
    return found - inherited, ""


def _self_check(
    selection: list[str],
    prev_record: dict[str, Any],
    check_command: str,
    run_check_fn: Callable[[str, float], Any],
    limit_s: float,
    root: str | None,
) -> PrepassResult:
    """Run ``selection`` in chunks until the limit is spent; report what failed.

    The cut is never made by killing the runner, because a killed runner reports
    no failure: no new chunk starts once the limit is spent, and a finished chunk's
    failures count even when a later chunk is killed. Tests ``build_probe_command``
    cannot target (a runner it does not support) are left out and named.
    """
    from pathlib import Path

    from refactory_dsh.check_digest import build_probe_command

    inherited = set(prev_record.get("inherited", []))
    root_path = Path(root) if root else None
    started = time.monotonic()
    ran: list[str] = []
    cut: list[str] = []
    failing: list[str] = []
    commands: list[str] = []
    notes: list[str] = []
    failed_outputs: list[str] = []
    last = None
    chunks = _chunks(selection)
    for index, chunk in enumerate(chunks):
        remaining = limit_s - (time.monotonic() - started)
        if remaining < 1:
            for later in chunks[index:]:
                cut.extend(later)
            notes.append(f"the {limit_s:.0f} s limit was spent before chunk {index + 1} "
                          f"of {len(chunks)}; it and the rest did not run")
            break
        ids = [name for name in chunk if "::" in name or " > " in name]
        files = [name for name in chunk if name not in ids]
        command, refusal = build_probe_command(check_command, ids, root=root_path, test_files=files)
        if refusal:
            cut.extend(chunk)
            notes.append(f"chunk {index + 1} could not be targeted: {refusal}")
            continue
        result = run_check_fn(command, remaining)
        last = result
        if getattr(result, "timed_out", False):
            for later in chunks[index:]:
                cut.extend(later)
            notes.append(f"chunk {index + 1} of {len(chunks)} was stopped at the "
                         f"{limit_s:.0f} s limit, so it and the rest did not report")
            break
        ran.extend(chunk)
        commands.append(command)
        found, why_not = _chunk_failures(result, inherited)
        if why_not:
            notes.append(f"chunk {index + 1} did not count: {why_not}")
        for test_id in sorted(found):
            if test_id not in failing:
                failing.append(test_id)
        if found:
            failed_outputs.append(_output(result))
    digest_text, runners, raw_tail = failure_digest(failed_outputs, inherited)
    return PrepassResult(
        ran=bool(commands), early_stop=bool(failing),
        targeted_command="\n".join(commands) or None,
        check_mode="targeted" if failing else "full", result=last,
        digest_text=digest_text, runners=runners, raw_tail=raw_tail,
        selection=list(selection), ran_ids=ran, cut_ids=cut, failing_ids=failing,
        commands=commands, notes=notes, limit_s=limit_s,
        elapsed_s=round(time.monotonic() - started, 1),
        outputs=failed_outputs, left_out=sorted(inherited),
    )


def without_failures(prepass: PrepassResult, test_ids: list[str]) -> PrepassResult:
    """The self-check's result with ``test_ids`` gone from its failures and its digest."""
    from dataclasses import replace

    leave_out = {*prepass.left_out, *test_ids}
    digest_text, runners, raw_tail = failure_digest(prepass.outputs, leave_out)
    failing = [test_id for test_id in prepass.failing_ids if test_id not in leave_out]
    return replace(prepass, failing_ids=failing, early_stop=bool(failing),
                   check_mode="targeted" if failing else "full",
                   digest_text=digest_text if failing else "", runners=runners if failing else [],
                   raw_tail=raw_tail if failing else "", left_out=sorted(leave_out))


def failure_digest(outputs: list[str], leave_out: set[str]) -> tuple[str, list[dict], str]:
    """The digest of the failing chunks' outputs, without the failures in ``leave_out``.

    Each output is digested on its own, as one runner's report, and the runners merged:
    the builder is shown the failures it is sent back for, and no inherited one.
    """
    from refactory_dsh.check_digest import CheckDigest, RunnerDigest, digest_check_output, format_digest

    if not outputs:
        return "", [], ""
    merged: dict[str, RunnerDigest] = {}
    tail = ""
    for output in outputs:
        digest = digest_check_output(output)
        tail = digest.raw_tail or tail
        for runner in digest.runners:
            kept = [entry for entry in runner.failures if entry.test_id not in leave_out]
            if not kept:
                continue
            into = merged.setdefault(runner.name, RunnerDigest(name=runner.name, passed=False))
            into.failures.extend(entry for entry in kept
                                 if entry.test_id not in {f.test_id for f in into.failures})
    combined = CheckDigest(runners=list(merged.values()), raw_tail=tail)
    return format_digest(combined), combined.to_dict()["runners"], tail[-500:]


def targeted_prepass(
    prev_record: dict[str, Any],
    check_command: str,
    run_check_fn: Callable[[str, float], Any],
    check_timeout_s: float,
    *,
    root: str | None = None,
    selection: list[str] | None = None,
    self_check_timeout_s: float | None = None,
) -> PrepassResult:
    """Run previously-failing tests first and stop early while one still fails.

    Parameters
    ----------
    prev_record:
        The previous ``check-record.json`` contents (read before running).
    check_command:
        The full check command string.
    run_check_fn:
        ``(command, timeout) -> SandboxResult`` callback.
    check_timeout_s:
        The full check's timeout.  The full check still gets this value
        unmodified.
    root:
        Working directory root for ``build_probe_command``'s package.json
        lookup (correction item 4).
    selection:
        The self-check's tests, in the order they run (`test_selection`): whole test
        files and test ids, the last check's failures among them. Given, the pre-pass
        runs on every attempt, round 1 included, in chunks within
        ``self_check_limit(self_check_timeout_s, check_timeout_s)``, and reports every
        failure it found that is not inherited. Not given (``check_artifact`` and
        ``focused_check``), it is the dm-128 pre-pass, unchanged.
    self_check_timeout_s:
        The self-check's own time limit (its knob), used with ``selection``.
    """
    if selection is not None:
        if not selection:
            return _skip()
        limit = self_check_limit(300 if self_check_timeout_s is None else self_check_timeout_s,
                                 check_timeout_s)
        return _self_check(selection, prev_record, check_command, run_check_fn, limit, root)

    # 1. Skip when previous record is not a failure.
    if prev_record.get("status") != "fail":
        return _skip()

    # 2. Extract failing test IDs from runners.
    failing_ids: list[str] = []
    for runner in prev_record.get("runners", []):
        for f in runner.get("failures", []):
            tid = f.get("test_id")
            if tid:
                failing_ids.append(tid)

    if not failing_ids:
        return _skip()

    # 3. Read inherited set from the previous record's "inherited" field.
    inherited_set = set(prev_record.get("inherited", []))

    # 4. Filter to non-inherited.
    non_inherited = [tid for tid in failing_ids if tid not in inherited_set]
    if not non_inherited:
        return _skip()

    # 5. Build targeted command.
    from pathlib import Path
    from refactory_dsh.check_digest import build_probe_command
    root_path = Path(root) if root else None
    targeted_cmd, refusal = build_probe_command(check_command, non_inherited, root=root_path)
    if refusal:
        return _skip()

    # 6. Pre-pass timeout.
    prepass_timeout = min(check_timeout_s // 3, 120)

    # 7. Run the targeted command.
    result = run_check_fn(targeted_cmd, prepass_timeout)

    # 8. Apply the early-stop rule.
    from refactory_dsh import authoring_evidence as evidence
    from refactory_dsh import base_check

    early_stop = True

    # Condition a: no prep failure.
    if getattr(result, "prep_failure", None) is not None:
        early_stop = False

    # Condition b: must be a code_failure.
    if early_stop and evidence.check_failure_kind(result) != "code_failure":
        early_stop = False

    # Condition c: exit code not 4 or 5.
    rc = getattr(result, "returncode", None)
    if early_stop and rc is not None and rc in (4, 5):
        early_stop = False

    # Condition d: parse failures and check that a targeted test still fails.
    if early_stop:
        raw_output = "\n".join(filter(None, [
            getattr(result, "stdout", None) or "",
            getattr(result, "stderr", None) or "",
        ])).strip()
        found_failures = base_check.parse_test_failures(raw_output)
        non_inherited_found = found_failures - inherited_set
        if not any(tid in non_inherited_found for tid in non_inherited):
            early_stop = False

    # Build the digest for the targeted output.
    from refactory_dsh.check_digest import digest_check_output, format_digest
    raw_combined = "\n".join(filter(None, [
        getattr(result, "stdout", None) or "",
        getattr(result, "stderr", None) or "",
    ])).strip()
    digest = digest_check_output(raw_combined)
    digest_text = format_digest(digest)
    runners = digest.to_dict().get("runners", [])
    raw_tail = digest.raw_tail[-500:]

    if early_stop:
        return PrepassResult(
            ran=True, early_stop=True, check_mode="targeted",
            targeted_command=targeted_cmd, result=result,
            digest_text=digest_text, runners=runners, raw_tail=raw_tail,
        )
    else:
        return PrepassResult(
            ran=True, early_stop=False, check_mode="full",
            targeted_command=targeted_cmd, result=result,
            digest_text=digest_text, runners=runners, raw_tail=raw_tail,
        )


def _extract_assertion_key(assertion: str) -> str:
    """Extract the ``>`` source line from an assertion string.

    "Same assertion" means the same ``>`` source line, even when the
    values differ (correction item 14).
    """
    for line in assertion.splitlines():
        stripped = line.strip()
        if stripped.startswith(">"):
            return stripped
    return assertion.strip()


def detect_stuck(
    prev_record: dict[str, Any],
    curr_runners: list[dict[str, Any]],
    curr_raw_output: str,
    worktree_path: str | None,
    prev_commit: str | None,
    curr_commit: str | None,
) -> str | None:
    """Detect when the same non-inherited test fails with the same assertion.

    Returns a formatted stuck notice string, or ``None``.
    """
    # Guard 1: previous record must be a failure.
    if prev_record.get("status") != "fail":
        return None

    # Guard 2: skip when same commit (retry/resume of the same attempt).
    if prev_commit is not None and curr_commit is not None and prev_commit == curr_commit:
        return None

    # Build prev_failures: test_id → assertion key.
    prev_failures: dict[str, str] = {}
    for runner in prev_record.get("runners", []):
        for f in runner.get("failures", []):
            tid = f.get("test_id", "")
            assertion = f.get("assertion", "")
            if tid:
                prev_failures[tid] = _extract_assertion_key(assertion)

    # Build curr_failures: test_id → assertion key.
    curr_failures: dict[str, str] = {}
    for runner in curr_runners:
        for f in runner.get("failures", []):
            tid = f.get("test_id", "")
            assertion = f.get("assertion", "")
            if tid:
                curr_failures[tid] = _extract_assertion_key(assertion)

    # Inherited set from previous record.
    inherited_set = set(prev_record.get("inherited", []))

    # Compare only tests both checks ran.
    common_ids = set(prev_failures) & set(curr_failures)
    stuck_tests = [
        (tid, curr_failures[tid])
        for tid in sorted(common_ids)
        if tid not in inherited_set and prev_failures[tid] == curr_failures[tid]
    ]

    if not stuck_tests:
        return None

    # Build diff output.
    diff_output = ""
    if worktree_path is not None:
        # Extract test file from the first stuck test.
        diff_files: set[str] = set()
        for tid, _ in stuck_tests:
            if "::" in tid:
                diff_files.add(tid.split("::")[0])
            elif " > " in tid:
                diff_files.add(tid.split(" > ")[0].strip())

        # The path:NN: lookup is documented as a known limitation — the whole
        # HEAD^..HEAD fallback is the documented behaviour.
        try:
            file_args = ["--"] + sorted(diff_files) if diff_files else []
            cp = subprocess.run(
                ["git", "diff", "HEAD^..HEAD"] + file_args,
                cwd=worktree_path, capture_output=True, text=True, timeout=10,
                env={**os.environ, "GIT_LITERAL_PATHSPECS": "1"},
            )
            diff_output = cp.stdout.strip()
        except (subprocess.TimeoutExpired, OSError):
            diff_output = ""

        # If diff is empty for the specific files, show the whole diff bounded.
        if not diff_output:
            try:
                cp = subprocess.run(
                    ["git", "diff", "HEAD^..HEAD"],
                    cwd=worktree_path, capture_output=True, text=True, timeout=10,
                )
                lines = cp.stdout.strip().splitlines()
                diff_output = "\n".join(lines[:200])
            except (subprocess.TimeoutExpired, OSError):
                diff_output = "(diff unavailable)"

    # Format the notice.
    parts = [
        "## Stuck: same failure on consecutive checks\n",
        "The following test(s) failed with the same assertion as the previous attempt.",
        "Try a different approach.\n",
    ]
    for tid, assertion_key in stuck_tests:
        parts.append(f"- **{tid}**: {assertion_key}")
    parts.append("")

    if diff_output:
        parts.append("### Your last change (HEAD^..HEAD) for the relevant files:")
        parts.append("```diff")
        parts.append(diff_output)
        parts.append("```")

    return "\n".join(parts)


def record_check_history(
    ctx: Any,
    check_mode: str,
    result_output: str,
    check_command: str,
) -> None:
    """Append to convergence-detection history, guarding on check_mode.

    Targeted runs are excluded so a subset never looks like convergence.
    """
    if check_mode == "targeted":
        return

    from refactory_dsh import base_check

    current_failures = sorted(base_check.parse_test_failures(result_output))
    history = list(ctx.facts.get("check_failures_history", []))
    history.append(current_failures)
    ctx.set_fact("check_failures_history", history)

    blocker = base_check.excusal_blocker(
        check_command, result_output, set(current_failures)
    ) if current_failures else "empty failure set"
    blocker_history = list(ctx.facts.get("check_blocker_history", []))
    blocker_history.append(blocker)
    ctx.set_fact("check_blocker_history", blocker_history)
