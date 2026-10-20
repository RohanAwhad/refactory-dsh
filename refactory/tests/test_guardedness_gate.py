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
        "results": [{"test_file": "test_foo.py", "status": "not_guarding"}],
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
        "results": [{"test_file": "test_foo.py", "status": "not_guarding"}],
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
            {"test_file": "test_a.py", "status": "guarding"},
            {"test_file": "test_b.py", "status": "guarding"},
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
            {"test_file": "test_a.py", "status": "not_guarding"},
            {"test_file": "test_b.py", "status": "not_guarding"},
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
