"""Tests for check_digest: failure digest parsing."""

from __future__ import annotations

from refactory_dsh.check_digest import (
    CheckDigest,
    FailureEntry,
    RunnerDigest,
    build_probe_command,
    digest_check_output,
    format_digest,
)
from refactory_dsh.base_check import strip_ansi


def test_digest_from_pytest_output():
    """Digest from pytest output has failing test IDs first."""
    output = (
        "============================= FAILURES =============================\n"
        "_________________________ test_addition _________________________\n"
        "    def test_addition():\n"
        ">       assert 1 + 1 == 3\n"
        "E       AssertionError: assert 2 == 3\n"
        "\n"
        "    File tests/test_math.py:5\n"
        "============================= short test summary info =============================\n"
        "FAILED tests/test_math.py::test_addition - AssertionError: assert 2 == 3\n"
        "PASSED tests/test_math.py::test_subtraction\n"
        "1 failed, 1 passed in 0.5s\n"
    )
    digest = digest_check_output(output)
    assert digest.runners, "should have at least one runner"
    pytest_runner = digest.runners[0]
    assert pytest_runner.name == "pytest"
    assert not pytest_runner.passed
    assert len(pytest_runner.failures) >= 1
    assert "test_addition" in pytest_runner.failures[0].test_id


def test_digest_from_vitest_output():
    """Digest from vitest output strips source-map noise."""
    output = (
        "Sourcemap for foo.js points to missing source files\n"
        " FAIL  src/foo.test.ts > math > should add\n"
        "   AssertionError: expected 3 to be 4\n"
        "   ❯ src/foo.test.ts:5:10\n"
        " ✓ src/foo.test.ts > math > should subtract\n"
        "\n"
        " Test Files  1 failed\n"
    )
    digest = digest_check_output(output)
    formatted = format_digest(digest)
    assert "Sourcemap" not in formatted
    assert "should add" in formatted or "math" in formatted


def test_digest_from_node_test_runner():
    """Node test runner output is parsed, not raw-tail fallback."""
    output = (
        "TAP version 13\n"
        "not ok 1 should add numbers\n"
        "  ---\n"
        "  operator: deepEqual\n"
        "  expected: 4\n"
        "  actual: 3\n"
        "  at: tests/math.test.js:5:3\n"
        "  ...\n"
        "ok 2 should subtract numbers\n"
        "# tests 2\n"
        "# pass 1\n"
        "# fail 1\n"
    )
    digest = digest_check_output(output)
    # Should have a node_test_runner parser result, not just raw_tail
    node_runners = [r for r in digest.runners if r.name == "node_test_runner"]
    assert node_runners, "node test runner should be parsed, not just raw-tail fallback"
    assert len(node_runners[0].failures) >= 1
    assert "should add" in node_runners[0].failures[0].test_id


def test_strip_ansi():
    """ANSI codes are stripped."""
    text = "\x1b[31mFAILED\x1b[0m tests/test_foo.py::test_bar"
    assert strip_ansi(text) == "FAILED tests/test_foo.py::test_bar"


def test_format_digest_has_failing_ids_first():
    """Formatted digest has failing test IDs at the top."""
    digest = CheckDigest(
        runners=[RunnerDigest(
            name="pytest", passed=False,
            failures=[FailureEntry("test_foo.py::test_bar", "AssertionError: 1 != 2", "File test_foo.py:5")],
        )],
        raw_tail="some tail output",
    )
    formatted = format_digest(digest)
    lines = formatted.strip().splitlines()
    # The first non-header line should contain the test ID
    content_lines = [l for l in lines if not l.startswith("##") and l.strip()]
    assert content_lines, "should have content"
    assert "test_bar" in content_lines[0]


def test_build_probe_command_pytest():
    """build_probe_command targets pytest tests."""
    cmd, refusal = build_probe_command(
        "python -m pytest -q tests", ["tests/test_foo.py::test_bar"]
    )
    assert refusal is None
    assert "test_foo.py::test_bar" in cmd


def test_build_probe_command_vitest():
    """build_probe_command targets vitest tests."""
    cmd, refusal = build_probe_command(
        "npx vitest run", ["src/foo.test.ts > suite > test_add"]
    )
    assert refusal is None
    assert "foo.test.ts" in cmd


def test_build_probe_command_node_refused():
    """build_probe_command refuses node's test runner."""
    cmd, refusal = build_probe_command(
        "node --test tests/", ["should add numbers"]
    )
    assert cmd == ""
    assert refusal is not None


def test_build_probe_command_pnpm_refused():
    """build_probe_command refuses pnpm test."""
    cmd, refusal = build_probe_command(
        "pnpm test", ["tests/test_foo.py::test_bar"]
    )
    assert cmd == ""
    assert "pnpm test" in refusal
