"""Parse check output into a structured per-runner failure digest.

Extracts each failing test's id, assertion and short traceback from pytest,
vitest and node's test runner output.  Unknown runners fall back to the raw
tail.  ANSI codes, passing-test lines and source-map noise are stripped.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field
from typing import Any

from refactory_dsh.base_check import parse_test_failures, strip_ansi

# ── data ──────────────────────────────────────────────────────────────────────

RAW_TAIL_LIMIT = 100


@dataclass
class FailureEntry:
    test_id: str
    assertion: str
    traceback: str


@dataclass
class RunnerDigest:
    name: str
    passed: bool
    failures: list[FailureEntry] = field(default_factory=list)


@dataclass
class CheckDigest:
    runners: list[RunnerDigest] = field(default_factory=list)
    raw_tail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "runners": [
                {
                    "name": r.name,
                    "passed": r.passed,
                    "failures": [
                        {"test_id": f.test_id, "assertion": f.assertion, "traceback": f.traceback}
                        for f in r.failures
                    ],
                }
                for r in self.runners
            ],
            "raw_tail": self.raw_tail,
        }


# ── ANSI / noise filters ─────────────────────────────────────────────────────

_SOURCEMAP_WARNING = re.compile(
    r"^.*Sourcemap\s.*points\sto\smissing\ssource\sfiles.*$", re.MULTILINE
)
_PASSING_PYTEST = re.compile(r"^.*\s+PASSED\s*$", re.MULTILINE)
_PASSING_VITEST = re.compile(r"^\s*[✓✔]\s+.*$", re.MULTILINE)


def _clean(output: str) -> str:
    """Strip ANSI codes, source-map warnings and passing-test lines."""
    text = strip_ansi(output)
    text = _SOURCEMAP_WARNING.sub("", text)
    text = _PASSING_PYTEST.sub("", text)
    text = _PASSING_VITEST.sub("", text)
    # collapse runs of blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


# ── pytest parser ─────────────────────────────────────────────────────────────

_PYTEST_SECTION = re.compile(
    r"^={3,}\s*(FAILURES|ERRORS|short test summary info)\s*={3,}$", re.MULTILINE
)
_PYTEST_HEADER = re.compile(r"^_{3,}\s*(.+?)\s*_{3,}$", re.MULTILINE)
_PYTEST_FAILED_LINE = re.compile(r"^FAILED\s+([\w/.:\[\]-]+(?:::[\w\[\]-]+)+)", re.MULTILINE)
_PYTEST_ERROR_LINE = re.compile(
    r"^(?:ERROR|ERRORS?)\s+([\w/.:\[\]-]+(?:::[\w\[\]-]+)*)\s*[-–—]", re.MULTILINE
)


def _parse_pytest(text: str) -> RunnerDigest | None:
    """Parse pytest output into a RunnerDigest, or None if not pytest."""
    failed_ids: set[str] = set()
    for m in _PYTEST_FAILED_LINE.finditer(text):
        failed_ids.add(m.group(1).strip())
    for m in _PYTEST_ERROR_LINE.finditer(text):
        failed_ids.add(m.group(1).strip())
    if not failed_ids:
        return None

    failures: list[FailureEntry] = []
    # Try to extract assertion and traceback from FAILURES section
    failures_section = ""
    for m in _PYTEST_SECTION.finditer(text):
        if m.group(1) == "FAILURES":
            end = text.find("\n===", m.end())
            failures_section = text[m.end():end] if end > 0 else text[m.end():]
            break

    headers = list(_PYTEST_HEADER.finditer(failures_section))
    for i, hdr in enumerate(headers):
        test_name = hdr.group(1).strip()
        start = hdr.end()
        end = headers[i + 1].start() if i + 1 < len(headers) else len(failures_section)
        block = failures_section[start:end].strip()

        # Find the assertion line
        assertion = ""
        traceback_line = ""
        lines = block.splitlines()
        for line in lines:
            stripped = line.strip()
            if stripped.startswith(("AssertionError", "AssertionError:", "assert ",
                                   "AssertError", "E       ", "E  ",
                                   "> ", "raise ", "TypeError", "ValueError",
                                   "KeyError", "AttributeError", "ImportError",
                                   "ModuleNotFoundError", "NameError", "RuntimeError",
                                   "FileNotFoundError", "IndexError", "Exception")):
                if stripped.startswith("E "):
                    stripped = stripped[2:].strip()
                if not assertion:
                    assertion = stripped[:200]
            if stripped.startswith("File ") and "test" in stripped.lower():
                traceback_line = stripped[:200]

        # Match test_name to one of our failed_ids
        matched_id = ""
        for fid in failed_ids:
            if test_name in fid or fid.endswith(test_name):
                matched_id = fid
                break
        if not matched_id:
            matched_id = test_name

        failures.append(FailureEntry(
            test_id=matched_id,
            assertion=assertion or "(see traceback)",
            traceback=traceback_line,
        ))

    # Ensure every failed_id has an entry
    seen = {f.test_id for f in failures}
    for fid in sorted(failed_ids):
        if fid not in seen:
            failures.append(FailureEntry(test_id=fid, assertion="(see raw output)", traceback=""))

    return RunnerDigest(name="pytest", passed=False, failures=failures)


# ── vitest parser ─────────────────────────────────────────────────────────────

_VITEST_FAIL_LINE = re.compile(r"^\s*(?:FAIL|×)\s+(.+)$", re.MULTILINE)
_VITEST_ASSERT = re.compile(r"^\s*(AssertionError|Error|Expected|Received|expect\()", re.MULTILINE)


def _parse_vitest(text: str) -> RunnerDigest | None:
    """Parse vitest output into a RunnerDigest, or None if not vitest."""
    fail_matches = list(_VITEST_FAIL_LINE.finditer(text))
    candidates = [m.group(1).strip() for m in fail_matches if " > " in m.group(1)]
    if not candidates:
        return None

    failures: list[FailureEntry] = []
    for test_id in candidates:
        # Find assertion near the test id
        idx = text.find(test_id)
        if idx < 0:
            idx = 0
        block = text[idx:idx + 2000]
        assertion = ""
        traceback_line = ""
        for line in block.splitlines():
            stripped = line.strip()
            if stripped.startswith(("AssertionError", "Error:", "Expected", "Received",
                                   "expect(", "TypeError", "ReferenceError")):
                if not assertion:
                    assertion = stripped[:200]
            if stripped.startswith("❯") or (stripped.startswith("File ") and "/" in stripped):
                traceback_line = stripped[:200]
        failures.append(FailureEntry(
            test_id=test_id,
            assertion=assertion or "(see raw output)",
            traceback=traceback_line,
        ))

    return RunnerDigest(name="vitest", passed=False, failures=failures)


# ── node test runner parser ───────────────────────────────────────────────────

_NODE_TEST_FAIL = re.compile(r"^\s*(?:✖|not ok)\s+(?:\d+\s+)?(.+)$", re.MULTILINE)
_NODE_TEST_DIAG = re.compile(r"^\s+---$", re.MULTILINE)


def _parse_node_test_runner(text: str) -> RunnerDigest | None:
    """Parse node's built-in test runner output into a RunnerDigest."""
    fail_matches = list(_NODE_TEST_FAIL.finditer(text))
    if not fail_matches:
        return None
    # Confirm this is actually node's test runner (look for TAP-style or node:test markers)
    if "node --test" not in text and "# tests" not in text and "not ok" not in text and "✖" not in text:
        return None

    failures: list[FailureEntry] = []
    for m in fail_matches:
        test_id = m.group(1).strip()
        # Look for assertion info in the diagnostic block after this test
        idx = m.end()
        block = text[idx:idx + 1500]
        assertion = ""
        traceback_line = ""
        in_diag = False
        for line in block.splitlines():
            stripped = line.strip()
            if stripped == "---":
                in_diag = True
                continue
            if stripped == "...":
                break
            if in_diag:
                if stripped.startswith(("operator:", "expected:", "actual:", "message:")):
                    if not assertion:
                        assertion = stripped[:200]
                if "Error:" in stripped and not assertion:
                    assertion = stripped[:200]
            if stripped.startswith("at ") and ("test" in stripped.lower() or "/" in stripped):
                traceback_line = stripped[:200]
        failures.append(FailureEntry(
            test_id=test_id,
            assertion=assertion or "(see raw output)",
            traceback=traceback_line,
        ))

    return RunnerDigest(name="node_test_runner", passed=False, failures=failures)


# ── public API ────────────────────────────────────────────────────────────────

def digest_check_output(output: str) -> CheckDigest:
    """Parse check output and return a structured digest."""
    cleaned = _clean(output)
    digest = CheckDigest()

    # Try each runner parser
    pytest_result = _parse_pytest(cleaned)
    if pytest_result:
        digest.runners.append(pytest_result)

    vitest_result = _parse_vitest(cleaned)
    if vitest_result:
        digest.runners.append(vitest_result)

    node_result = _parse_node_test_runner(cleaned)
    if node_result:
        digest.runners.append(node_result)

    # Raw tail: last RAW_TAIL_LIMIT lines of cleaned output
    lines = cleaned.strip().splitlines()
    digest.raw_tail = "\n".join(lines[-RAW_TAIL_LIMIT:]) if lines else ""

    return digest


def format_digest(digest: CheckDigest) -> str:
    """Format a CheckDigest as human-readable text for the builder."""
    parts: list[str] = []
    for runner in digest.runners:
        if not runner.failures:
            continue
        parts.append(f"## Failing tests ({runner.name})\n")
        for f in runner.failures:
            line = f"- **{f.test_id}**"
            if f.assertion:
                line += f" — {f.assertion}"
            parts.append(line)
            if f.traceback:
                parts.append(f"  {f.traceback}")
        parts.append("")

    if digest.raw_tail:
        parts.append(f"## Raw tail (last {RAW_TAIL_LIMIT} lines)\n")
        parts.append(digest.raw_tail)

    return "\n".join(parts)


def build_probe_command(check_command: str, test_ids: list[str]) -> tuple[str, str | None]:
    """Build a targeted command per runner from the check command and failing test IDs.

    Returns (command, None) on success or ("", refusal_reason) on failure.
    Classifies each test id by runner so a mixed check gets only its own ids per step.
    """
    if not test_ids:
        return "", "no test IDs to target"

    from refactory_dsh.base_check import _steps

    # Classify test IDs by runner
    pytest_ids: list[str] = []
    vitest_ids: list[str] = []
    node_ids: list[str] = []
    for tid in test_ids:
        if "::" in tid:
            pytest_ids.append(tid)
        elif " > " in tid:
            vitest_ids.append(tid)
        else:
            node_ids.append(tid)

    if node_ids and not pytest_ids and not vitest_ids:
        return "", "targeting node's test runner is not supported"

    steps = _steps(check_command)
    targeted_parts: list[str] = []
    for step in steps:
        step = step.strip()
        if not step:
            continue
        # Detect runner for this step
        if "pytest" in step or "python -m pytest" in step:
            if not pytest_ids:
                continue  # skip steps with no failures
            # Replace positional paths with test IDs
            targeted_parts.append(f"{step} {' '.join(shlex.quote(tid) for tid in pytest_ids)}")
        elif "vitest" in step:
            if not vitest_ids:
                continue
            # Map vitest ids to file filters plus -t patterns
            files: set[str] = set()
            patterns: set[str] = set()
            for vid in vitest_ids:
                parts = vid.split(" > ")
                if parts:
                    files.add(parts[0].strip())
                if len(parts) > 1:
                    patterns.add(re.escape(parts[-1].strip()))
            file_args = " ".join(shlex.quote(f) for f in sorted(files))
            pattern_args = " ".join(f"-t {shlex.quote(p)}" for p in sorted(patterns))
            targeted_parts.append(f"{step} {file_args} {pattern_args}".strip())
        elif "pnpm test" in step:
            return "", "targeting pnpm test is deferred to the fail-fast run"
        elif "node " in step and ("--test" in step or "node_modules" not in step):
            return "", "targeting node's test runner is not supported"
        else:
            # Keep build steps and other non-test steps
            targeted_parts.append(step)

    if not targeted_parts:
        return "", "no targetable test steps found in the check command"

    return " && ".join(targeted_parts), None
