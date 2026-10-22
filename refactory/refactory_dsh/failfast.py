"""Fail-fast targeted pre-pass, stuck detection, and convergence history helpers.

Shared by ``check_artifact`` (design mode) and ``focused_check`` (fast mode)
so that the logic is not duplicated across two entry points.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from typing import Any, Callable


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


def _skip() -> PrepassResult:
    return PrepassResult(
        ran=False, early_stop=False, targeted_command=None,
        check_mode="full", result=None, digest_text="", runners=[], raw_tail="",
    )


def targeted_prepass(
    prev_record: dict[str, Any],
    check_command: str,
    run_check_fn: Callable[[str, float], Any],
    check_timeout_s: float,
    *,
    root: str | None = None,
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
    """
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
