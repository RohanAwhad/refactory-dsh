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
    def __init__(self, files=None, facts=None):
        self._files = dict(files or {})
        self.facts = dict(facts or {})
        self.workdir = Path(".")
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


def test_guardedness_enforced_in_check_artifact():
    """Verify that check_artifact calls enforce_guardedness (integration-level check).

    Without the fix, check_artifact would return 'proceed' even with non-guarding tests.
    This test mocks the environment to confirm the wiring exists.
    """
    from refactory_dsh import design_ops
    import inspect
    source = inspect.getsource(design_ops.check_artifact)
    assert "enforce_guardedness" in source, (
        "check_artifact must call enforce_guardedness"
    )


def test_guardedness_enforced_in_focused_check():
    """Verify that focused_check calls enforce_guardedness for detection.

    focused_check is an op node and cannot return a Verdict to reloop,
    so it detects non-guarding tests and records guardedness_blocked in
    focused-check.json. gate_verification (the first gate after focused_check)
    reads the flag and does the actual reloop.
    """
    from refactory_dsh import fast_ops
    import inspect
    source = inspect.getsource(fast_ops.focused_check)
    assert "enforce_guardedness" in source, (
        "focused_check must call enforce_guardedness for detection"
    )
    assert "guardedness_blocked" in source, (
        "focused_check must set guardedness_blocked in the payload"
    )


def test_guardedness_enforcement_routed_through_gate_verification_fast():
    """gate_verification in fast_ops enforces guardedness via the guardedness_blocked flag.

    focused_check is an op node whose Verdict returns are silently ignored by the
    executor, so the actual reloop happens in gate_verification — the first gate
    node after focused_check in the fast-mode pipeline.
    """
    from refactory_dsh import fast_ops
    import inspect
    source = inspect.getsource(fast_ops.gate_verification)
    assert "guardedness_blocked" in source, (
        "gate_verification must check guardedness_blocked from focused-check.json"
    )


def test_gate_verification_fast_blocks_on_guardedness():
    """gate_verification in fast mode returns reloop when guardedness is blocked."""
    from refactory_dsh.fast_ops import gate_verification

    ctx = FakeCtx(
        files={
            "focused-check.json": json.dumps({
                "status": "pass",
                "guardedness_blocked": True,
            }),
            "check.txt": "BLOCKING: these new tests pass without the change: test_foo.py",
            "reviews/verification.md": "No blocking findings.\n\nVERDICT: PASS",
        }
    )
    ctx._knobs = {"repair_attempts": "2"}
    result = gate_verification(ctx)
    assert isinstance(result, Verdict)
    assert str(result) == "reloop"
    assert "test_foo.py" in result.reason


def test_focused_check_declares_check_txt_write():
    """focused_check must declare check.txt in its writes for artifact mode.

    Without the fix, focused_check writes check.txt (for guardedness blocking
    and inherited-failure messages) but does not declare it, causing
    ExecutionError at runtime when enforce_writes is True.
    """
    from refactory_dsh.fast import DesignFastMode

    graph = DesignFastMode().build()
    node = graph.nodes["focused_check"]
    assert "check.txt" in node.writes, (
        "focused_check must declare check.txt in its writes; "
        "it writes this file for guardedness blocking and inherited-failure messages"
    )


def test_guardedness_reloops_in_fast_mode_integration(tmp_path, monkeypatch):
    """Guardedness enforcement in fast mode reloops through the real executor.

    This exercises the full pipeline: focused_check detects non-guarding tests,
    writes check.txt and sets guardedness_blocked in focused-check.json, the
    verifier runs, then gate_verification reads the flag and reloops to emit.
    On the second attempt guardedness passes and the run completes.

    Without the fix (check.txt in focused_check's writes and guardedness_blocked
    routing in gate_verification), this test would either raise ExecutionError
    (undeclared write) or proceed without relooping.
    """
    monkeypatch.setenv("REFACTORY_PROJECT", str(tmp_path))
    from tests_support import FakeModel, render, VERIFICATION_ANSWER
    from refactory_dsh import base_check
    from refactory_dsh.fast import DesignFastMode

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
        render({"tool.py": "print('v1')\n"}),       # first emit: guardedness blocks
        VERIFICATION_ANSWER,                         # verifier after first check
        # gate_verification sees guardedness_blocked → reloops to emit
        render({"tool.py": "print('v2')\n"}),       # second emit after reloop
        VERIFICATION_ANSWER,                         # verifier after second check
        FAST_QA,                                     # focused QA
    ]

    mode = DesignFastMode()
    state = mode.state_dict()
    state["knobs"]["repair_attempts"]["value"] = 2
    mode.load_state_dict(state)
    result = mode.run(
        tmp_path / "run", model=FakeModel(answers),
        task={"name": "guard_fast", "brief": "a tool", "check": "python tool.py"},
    )
    assert result.exit_ok, result.error
    assert call_count[0] >= 2, "guardedness must be computed at least twice"
    assert int(result.facts.get("repairs_used", 0)) >= 1, (
        "the pipeline must have relooped at least once for guardedness"
    )
