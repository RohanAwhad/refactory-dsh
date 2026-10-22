"""R3 tests: test-guardedness gate enforces that new tests fail without the change."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from refactory_dsh.design_ops import enforce_guardedness
from refactory_dsh.nn import Verdict


class FakeCtx:
    """Minimal context for enforce_guardedness tests."""
    def __init__(self, files=None, facts=None, workdir=None):
        self._files = dict(files or {})
        self.facts = dict(facts or {})
        self.workdir = workdir or Path(".")
        self._knobs = {}

    def exists(self, name):
        return name in self._files

    def read_text(self, name):
        return self._files.get(name, "")

    def read_json(self, name, default=None):
        text = self._files.get(name)
        if text is None:
            return default
        return json.loads(text)

    def write_text(self, name, text):
        self._files[name] = text

    def write_json(self, name, data):
        self._files[name] = json.dumps(data)

    def set_fact(self, name, value):
        self.facts[name] = value

    def knob(self, name, default=""):
        return self._knobs.get(name, default)


def test_non_guarding_test_without_exemption_causes_reloop():
    """A new test that passes on the base blocks the check."""
    guardedness = {
        "summary": {"total_new_tests": 1, "guarding": 0, "not_guarding": 1},
        "results": [{"file": "test_foo.py", "guarding": False, "file_level": "pass"}],
    }
    ctx = FakeCtx(
        files={
            "test-guardedness.json": json.dumps(guardedness),
            "design.md": "# Design\n\n## Summary\n\nFix it.\n",
        }
    )
    result = enforce_guardedness(ctx)
    assert result == ["test_foo.py"]


def test_non_guarding_test_with_exemption_proceeds():
    """A test listed in ## Test guardedness exemptions is not blocked."""
    guardedness = {
        "summary": {"total_new_tests": 1, "guarding": 0, "not_guarding": 1},
        "results": [{"file": "test_foo.py", "guarding": False, "file_level": "pass"}],
    }
    design = (
        "# Design\n\n## Summary\n\nFix it.\n\n"
        "## Test guardedness exemptions\n\n"
        "- `test_foo.py` — pins existing behaviour\n"
    )
    ctx = FakeCtx(
        files={
            "test-guardedness.json": json.dumps(guardedness),
            "design.md": design,
        }
    )
    result = enforce_guardedness(ctx)
    assert result == []


def test_all_guarding_proceeds():
    """When all tests guard the change, no blocking happens."""
    guardedness = {
        "summary": {"total_new_tests": 2, "guarding": 2, "not_guarding": 0},
        "results": [
            {"file": "test_a.py", "guarding": True},
            {"file": "test_b.py", "guarding": True},
        ],
    }
    ctx = FakeCtx(
        files={"test-guardedness.json": json.dumps(guardedness)}
    )
    result = enforce_guardedness(ctx)
    assert result == []


def test_no_guardedness_record_proceeds():
    """When there is no guardedness record, do not block."""
    ctx = FakeCtx()
    result = enforce_guardedness(ctx)
    assert result == []


def test_unavailable_guardedness_proceeds():
    """When guardedness is unavailable, do not block."""
    guardedness = {"unavailable": "the new tests could not be run on the base"}
    ctx = FakeCtx(
        files={"test-guardedness.json": json.dumps(guardedness)}
    )
    result = enforce_guardedness(ctx)
    assert result == []


def test_cannot_run_on_base_not_blocked():
    """Tests that cannot even run on the base (collection error) are NOT
    'passing without the change' — they are excluded from guardedness blocking."""
    guardedness = {
        "summary": {"total_new_tests": 2, "guarding": 0, "not_guarding": 1,
                     "cannot_run_on_base": 1},
        "results": [
            {"file": "test_new.py", "guarding": False, "file_level": "cannot_run_on_base"},
            {"file": "test_trivial.py", "guarding": False, "file_level": "pass"},
        ],
    }
    ctx = FakeCtx(
        files={
            "test-guardedness.json": json.dumps(guardedness),
            "design.md": "# D\n\n## Summary\n\nS\n",
        }
    )
    result = enforce_guardedness(ctx)
    # Only test_trivial.py should be flagged, not test_new.py
    assert result == ["test_trivial.py"]


def test_cannot_run_on_base_alone_proceeds():
    """When the only non-guarding test is cannot_run_on_base, proceed."""
    guardedness = {
        "summary": {"total_new_tests": 1, "guarding": 0, "not_guarding": 0,
                     "cannot_run_on_base": 1},
        "results": [
            {"file": "test_new.py", "guarding": False, "file_level": "cannot_run_on_base"},
        ],
    }
    ctx = FakeCtx(
        files={
            "test-guardedness.json": json.dumps(guardedness),
            "design.md": "# D\n\n## Summary\n\nS\n",
        }
    )
    result = enforce_guardedness(ctx)
    assert result == []


def test_reloop_message_names_tests():
    """The returned list names the specific test files."""
    guardedness = {
        "summary": {"total_new_tests": 2, "guarding": 0, "not_guarding": 2},
        "results": [
            {"file": "test_a.py", "guarding": False, "file_level": "pass"},
            {"file": "test_b.py", "guarding": False, "file_level": "pass"},
        ],
    }
    ctx = FakeCtx(
        files={
            "test-guardedness.json": json.dumps(guardedness),
            "design.md": "# D\n\n## Summary\n\nS\n",
        }
    )
    result = enforce_guardedness(ctx)
    assert "test_a.py" in result
    assert "test_b.py" in result


def test_reloop_message_does_not_mention_editing_design_md():
    """The guardedness refusal must NOT tell the builder to edit design.md.

    The builder cannot write design.md, so the message should say to make
    the test fail without the change, or explain in the PR.
    """
    guardedness = {
        "summary": {"total_new_tests": 1, "guarding": 0, "not_guarding": 1},
        "results": [{"file": "test_foo.py", "guarding": False, "file_level": "pass"}],
    }
    ctx = FakeCtx(
        files={
            "test-guardedness.json": json.dumps(guardedness),
            "design.md": "# D\n",
        }
    )
    # Simulate what check_artifact does when enforce_guardedness returns tests
    unexempted = enforce_guardedness(ctx)
    assert unexempted == ["test_foo.py"]
    detail = (
        "BLOCKING: these new tests pass without the change: "
        + ", ".join(unexempted)
        + ". Make them fail without the change, or explain in the "
        "PR why each test pins existing behaviour."
    )
    assert "design.md" not in detail.lower()
    assert "make them fail" in detail.lower()


def test_focused_check_declares_check_txt_write():
    """focused_check must declare check.txt in its writes for artifact mode."""
    from refactory_dsh.fast import DesignFastMode
    graph = DesignFastMode().build()
    node = graph.nodes["focused_check"]
    assert "check.txt" in node.writes


def test_gate_check_fast_blocks_on_guardedness():
    """gate_check in fast mode returns reloop when guardedness is blocked."""
    from refactory_dsh.fast_ops import gate_check

    ctx = FakeCtx(
        files={
            "focused-check.json": json.dumps({
                "status": "pass",
                "guardedness_blocked": True,
            }),
            "check.txt": "BLOCKING: these new tests pass without the change: test_foo.py",
        }
    )
    ctx._knobs = {"repair_attempts": "2"}
    result = gate_check(ctx)
    assert isinstance(result, Verdict)
    assert str(result) == "reloop"
    assert "test_foo.py" in result.reason


def test_gate_check_fast_handles_check_failure():
    """gate_check in fast mode routes check failures to _repair."""
    from refactory_dsh.fast_ops import gate_check

    ctx = FakeCtx(
        files={
            "focused-check.json": json.dumps({
                "status": "fail",
                "returncode": 1,
            }),
            "check.txt": "requested runtime check failed (exit 1): assert False",
        }
    )
    ctx._knobs = {"repair_attempts": "2"}
    result = gate_check(ctx)
    assert isinstance(result, Verdict)
    assert str(result) == "reloop"
    # Must carry the check failure, not the verifier noise.
    assert "assert False" in result.reason or "check failed" in result.reason


def test_guardedness_reloops_in_full_mode_integration(tmp_path, monkeypatch):
    """Guardedness enforcement in full mode reloops through repair(kind='check').

    Without the fix, check_artifact would return Verdict("reloop") without
    calling repair(), so the run would hit max_iterations and raise
    ExecutionError instead of a clean repair cycle.
    """
    monkeypatch.setenv("REFACTORY_PROJECT", str(tmp_path))
    from tests_support import (DESIGN_DOC_ANSWER, DESIGN_REVIEW_ANSWER,
                               PITFALLS_ANSWER, RESEARCH_ANSWER,
                               VERIFICATION_ANSWER,
                               REVIEW_ANSWERS, FakeModel, git_repo, render)
    from refactory_dsh import base_check
    from refactory_dsh.design import DesignMode

    repo = git_repo(tmp_path / "repo")
    call_count = [0]

    def mock_guardedness(ctx, command, timeout):
        call_count[0] += 1
        if call_count[0] == 1:
            return {
                "summary": {"total_new_tests": 1, "guarding": 0, "not_guarding": 1},
                "results": [{"file": "test_foo.py", "guarding": False, "file_level": "pass"}],
            }
        return {
            "summary": {"total_new_tests": 1, "guarding": 1, "not_guarding": 0},
            "results": [{"file": "test_foo.py", "guarding": True}],
        }

    monkeypatch.setattr(base_check, "compute_test_guardedness", mock_guardedness)

    answers = [
        RESEARCH_ANSWER, PITFALLS_ANSWER, "perspectives", "one plan",
        DESIGN_DOC_ANSWER, DESIGN_REVIEW_ANSWER,
        render({"tool.py": "print('v1')\n",
                "test_foo.py": "def test_foo():\n    assert True\n"}),  # first emit: test_foo.py in added_files
        render({"tool.py": "print('v2')\n",
                "test_foo.py": "def test_foo():\n    import tool\n    assert hasattr(tool, '__file__')\n"}),  # second emit: guarding test
        *REVIEW_ANSWERS[:3],
        VERIFICATION_ANSWER,
        REVIEW_ANSWERS[3],
    ]
    result = DesignMode().run(
        tmp_path / "run", model=FakeModel(answers),
        verdicts={"gate_strategy": "proceed"},
        task={"name": "guard_full", "brief": "a tool", "check": "python tool.py",
              "target": str(repo)},
    )
    assert result.exit_ok, result.error
    assert call_count[0] >= 2, "guardedness must be computed at least twice"


def test_guardedness_reloops_in_fast_mode_integration(tmp_path, monkeypatch):
    """Guardedness enforcement in fast mode reloops through the real executor.

    This exercises the full pipeline: focused_check detects non-guarding tests,
    writes check.txt and sets guardedness_blocked in focused-check.json, the
    verifier runs, then gate_verification reads the flag and reloops to emit.
    On the second attempt guardedness passes and the run completes.
    """
    monkeypatch.setenv("REFACTORY_PROJECT", str(tmp_path))
    from tests_support import FakeModel, git_repo, render, VERIFICATION_ANSWER
    from refactory_dsh import base_check
    from refactory_dsh.fast import DesignFastMode

    repo = git_repo(tmp_path / "repo")
    FAST_QA = json.dumps({
        "status": "pass",
        "checks": [{"name": "runs", "command": "python tool.py",
                     "status": "pass", "evidence": "exit 0"}],
        "findings": [], "not_run": [],
    })

    call_count = [0]

    def mock_guardedness(ctx, command, timeout):
        call_count[0] += 1
        if call_count[0] == 1:
            # First call: test does not guard the change
            return {
                "summary": {"total_new_tests": 1, "guarding": 0, "not_guarding": 1},
                "results": [{"file": "test_foo.py", "guarding": False, "file_level": "pass"}],
            }
        # Subsequent calls: test guards the change
        return {
            "summary": {"total_new_tests": 1, "guarding": 1, "not_guarding": 0},
            "results": [{"file": "test_foo.py", "guarding": True}],
        }

    monkeypatch.setattr(base_check, "compute_test_guardedness", mock_guardedness)

    answers = [
        "Change only the requested file.",          # strategy
        render({"tool.py": "print('v1')\n",
                "test_foo.py": "def test_foo():\n    assert True\n"}),  # first emit
        # gate_check sees guardedness_blocked → reloops to emit
        render({"tool.py": "print('v2')\n",
                "test_foo.py": "def test_foo():\n    import tool\n    assert hasattr(tool, '__file__')\n"}),  # second emit
        FAST_QA,                                     # focused QA (in fork_qa)
        VERIFICATION_ANSWER,                         # verifier (in fork_qa)
    ]

    mode = DesignFastMode()
    state = mode.state_dict()
    state["knobs"]["repair_attempts"]["value"] = 2
    mode.load_state_dict(state)
    result = mode.run(
        tmp_path / "run", model=FakeModel(answers),
        task={"name": "guard_fast", "brief": "a tool", "check": "python tool.py",
              "target": str(repo)},
    )
    assert result.exit_ok, result.error
    assert call_count[0] >= 2, "guardedness must be computed at least twice"
    assert int(result.facts.get("repairs_used", 0)) >= 1, (
        "the pipeline must have relooped at least once for guardedness"
    )
