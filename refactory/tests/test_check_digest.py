"""Tests for check_digest: failure digest parsing.

Tests are driven through check_artifact (design) and focused_check (design_fast)
as the brief requires, with unit tests for node's test runner and formatting.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from refactory_dsh.check_digest import (
    CheckDigest,
    FailureEntry,
    RunnerDigest,
    build_probe_command,
    digest_check_output,
    format_digest,
)
from refactory_dsh.base_check import strip_ansi
from refactory_dsh import design_ops, repo_change, worktree, nn
from refactory_dsh.nn import Verdict


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_run_with_worktree(tmp_path, *, check_cmd="echo ok", emit_content="===FILE: tool.py===\nprint('ok')\n"):
    """Create a minimal run directory with a worktree so check_artifact can run."""
    from tests_support import git_repo, git, render
    repo = git_repo(tmp_path / "repo")
    run = tmp_path / "run"
    run.mkdir()
    (run / ".refactory").mkdir(parents=True)

    # Set up the worktree
    branch = "refactory/run-test"
    git(repo, "checkout", "-b", branch)
    (repo / "tool.py").write_text("print('old')\n")
    git(repo, "add", "tool.py")
    git(repo, "commit", "-q", "-m", "base")
    base = git(repo, "rev-parse", "HEAD")
    head = base

    found = worktree.Worktree(target=str(repo), path=str(repo), branch=branch, base=base, head=head, run=str(run))
    worktree._write_json(run / ".refactory" / "slot" / "run-dac367f4e563.json", {
        "target": str(repo), "path": str(repo), "branch": branch, "base": base, "head": head,
    })
    (run / ".refactory" / "slot").mkdir(parents=True, exist_ok=True)
    from dataclasses import asdict
    worktree._write_json(run / ".refactory" / "slot" / "run-dac367f4e563.json", asdict(found))

    ctx = nn.RunContext(run, facts={"check": check_cmd, "name": "test-digest", "brief": "test"},
                        knobs={"check_timeout_s": 60, "attempts": 3})
    ctx._declared = ("check.txt", "validation.txt", "authoring-evidence.json", "check-record.json",
                     "test-guardedness.json")
    ctx._declared_facts = ("validation", "check_attempts_used", "review_attempts_used",
                          "attempts_used", "runtime_evidence", "inherited_failures")
    ctx.enforce_writes = False
    return ctx, repo, run


# ── digest through check_artifact (design mode) ──────────────────────────────

def test_digest_from_pytest_output(tmp_path, monkeypatch):
    """Digest from pytest output via check_artifact, with failing test IDs first."""
    pytest_output = (
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
    # Monkeypatch so check_artifact's run_check returns our pytest output
    monkeypatch.setattr(repo_change, "refused", lambda ctx: None)
    from refactory_dsh.sandbox import SandboxResult
    fake_result = SandboxResult(ok=False, returncode=1, elapsed_s=1.0,
                                 stdout=pytest_output, stderr="")
    monkeypatch.setattr(repo_change, "run_check", lambda ctx, cmd, timeout: fake_result)
    # Stub out base_check and authoring_evidence to avoid side effects
    from refactory_dsh import base_check, authoring_evidence
    monkeypatch.setattr(base_check, "compute_test_guardedness", lambda *a, **k: None)
    monkeypatch.setattr(authoring_evidence, "record", lambda *a, **k: None)
    monkeypatch.setattr(authoring_evidence, "stop_unrepairable", lambda *a, **k: None)
    # Stub repair to just return the detail
    from refactory_dsh import create_ops
    monkeypatch.setattr(create_ops, "repair", lambda ctx, details: Verdict("reloop", details[0]))

    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / ".refactory").mkdir(exist_ok=True)
    ctx = nn.RunContext(run_dir, facts={"check": "python -m pytest", "name": "test", "brief": "test"},
                        knobs={"check_timeout_s": 60, "attempts": 3})
    ctx._declared = ("check.txt", "validation.txt", "authoring-evidence.json", "check-record.json",
                     "test-guardedness.json")
    ctx._declared_facts = ("validation", "check_attempts_used", "review_attempts_used",
                          "attempts_used", "runtime_evidence", "inherited_failures")
    ctx.enforce_writes = False

    result = design_ops.check_artifact(ctx)
    assert isinstance(result, Verdict)
    assert str(result) == "reloop"
    # The digest should contain the failing test ID
    assert "test_addition" in result.reason
    # The check-record.json should have been written
    cr = json.loads((tmp_path / "run" / "check-record.json").read_text())
    assert cr["status"] == "fail"
    assert "test_addition" in cr.get("digest", "")


def test_digest_from_vitest_output(tmp_path, monkeypatch):
    """Digest from vitest output via focused_check, source-map noise stripped."""
    from refactory_dsh import fast_ops
    vitest_output = (
        "Sourcemap for foo.js points to missing source files\n"
        " FAIL  src/foo.test.ts > math > should add\n"
        "   AssertionError: expected 3 to be 4\n"
        "   ❯ src/foo.test.ts:5:10\n"
        " ✓ src/foo.test.ts > math > should subtract\n"
        "\n"
        " Test Files  1 failed\n"
    )
    from refactory_dsh.sandbox import SandboxResult
    fake_result = SandboxResult(ok=False, returncode=1, elapsed_s=1.0,
                                 stdout=vitest_output, stderr="")
    monkeypatch.setattr(repo_change, "run_check", lambda ctx, cmd, timeout: fake_result)

    # Stub out base_check and authoring evidence
    from refactory_dsh import base_check, authoring_evidence
    monkeypatch.setattr(base_check, "compute_test_guardedness", lambda *a, **k: None)
    monkeypatch.setattr(authoring_evidence, "record", lambda *a, **k: None)
    monkeypatch.setattr(authoring_evidence, "stop_unrepairable", lambda *a, **k: None)

    run = tmp_path / "run"
    run.mkdir(parents=True)
    (run / ".refactory").mkdir()
    (run / "emit.txt").write_text("===FILE: answer.py===\nprint(1)\n")
    (run / "focused-check.json").write_text("{}")
    (run / "focused-source.json").write_text("{}")
    (run / "authoring-evidence.json").write_text("{}")

    ctx = nn.RunContext(run, facts={"check": "npx vitest run", "name": "test", "brief": "test",
                                    "artifact": str(run)},
                        knobs={"check_timeout_s": 60, "repair_attempts": 1})
    ctx._declared = ("focused-check.json", "focused-source.json", "authoring-evidence.json",
                     "check-record.json", "check.txt")
    ctx._declared_facts = ("focused_check", "output_path", "review_files", "runtime_evidence",
                          "inherited_failures")
    ctx.enforce_writes = False

    fast_ops.focused_check(ctx)
    cr = json.loads((run / "check-record.json").read_text())
    assert cr["status"] == "fail"
    digest = cr.get("digest", "")
    assert "Sourcemap" not in digest
    assert "should add" in digest or "math" in digest


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


def test_build_probe_command_pnpm_skipped_for_pytest_ids():
    """build_probe_command skips pnpm test when only pytest IDs are targeted."""
    cmd, refusal = build_probe_command(
        "pnpm test && .venv/bin/python -m pytest -q refactory/tests",
        ["refactory/tests/test_foo.py::test_bar"],
    )
    assert refusal is None
    assert "test_foo.py::test_bar" in cmd
    assert "pnpm test" not in cmd


def test_build_probe_command_pnpm_vitest_targeting(tmp_path, monkeypatch):
    """build_probe_command targets pnpm test with vitest IDs (no -- separator)."""
    (tmp_path / "package.json").write_text('{"scripts": {"test": "vitest run"}}')
    from pathlib import Path
    cmd, refusal = build_probe_command(
        "pnpm test", ["src/foo.test.ts > suite > test_add"], root=tmp_path,
    )
    assert refusal is None
    assert "pnpm test" in cmd
    assert "src/foo.test.ts" in cmd
    assert "-t" in cmd
    assert "--" not in cmd  # No -- separator
    assert cmd.count("-t") == 1  # Single -t flag


def test_build_probe_command_pnpm_not_vitest(tmp_path):
    """pnpm test with non-vitest script is refused."""
    (tmp_path / "package.json").write_text('{"scripts": {"test": "jest"}}')
    cmd, refusal = build_probe_command(
        "pnpm test", ["src/foo.test.ts > suite > test_add"], root=tmp_path,
    )
    assert cmd == ""
    assert "vitest" in refusal


def test_build_probe_command_pnpm_and_pytest_mixed(tmp_path):
    """Mixed pnpm test && pytest with failures from both runners."""
    (tmp_path / "package.json").write_text('{"scripts": {"test": "vitest run"}}')
    cmd, refusal = build_probe_command(
        "pnpm test && .venv/bin/python -m pytest -q tests",
        ["src/foo.test.ts > suite > test_add", "tests/test_bar.py::test_baz"],
        root=tmp_path,
    )
    assert refusal is None
    assert "pnpm test" in cmd
    assert "src/foo.test.ts" in cmd
    assert "test_bar.py::test_baz" in cmd


def test_build_probe_command_pnpm_no_package_json(tmp_path):
    """pnpm test with missing package.json is refused."""
    cmd, refusal = build_probe_command(
        "pnpm test", ["src/foo.test.ts > suite > test_add"], root=tmp_path,
    )
    assert cmd == ""
    assert "vitest" in refusal


def test_build_probe_command_pytest_drops_positional_paths():
    """build_probe_command drops positional paths from pytest, running only named tests."""
    cmd, refusal = build_probe_command(
        ".venv/bin/python -m pytest -q tests",
        ["tests/test_foo.py::test_bar", "tests/test_foo.py::test_baz"],
    )
    assert refusal is None
    # The positional "tests" path must not appear as a bare word that would
    # cause the whole directory to run.
    import shlex
    tokens = shlex.split(cmd)
    # After the pytest executable, only flags and the two test IDs should remain.
    assert "tests/test_foo.py::test_bar" in tokens
    assert "tests/test_foo.py::test_baz" in tokens
    # "tests" alone (the positional path) must be gone.
    pytest_idx = None
    for i, t in enumerate(tokens):
        if "pytest" in t:
            pytest_idx = i
            break
    # Every token after the pytest command should be a flag or a test ID
    if pytest_idx is not None:
        for t in tokens[pytest_idx + 1:]:
            assert t.startswith("-") or "::" in t, \
                f"unexpected positional path in targeted command: {t!r}"


def test_build_probe_command_unrecognised_refused():
    """build_probe_command refuses an unrecognised runner."""
    cmd, refusal = build_probe_command(
        "make check", ["tests/test_foo.py::test_bar"]
    )
    assert cmd == ""
    assert "no recognised" in refusal
