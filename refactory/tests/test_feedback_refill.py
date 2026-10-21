"""Tests for feedback re-fill: declared feedback keys are re-derived from persisted files."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest


def _make_workdir(tmp_path: Path) -> Path:
    """Create a minimal run directory."""
    workdir = tmp_path / "run"
    workdir.mkdir()
    (workdir / ".refactory").mkdir()
    return workdir


def test_refill_from_check_record(tmp_path):
    """Validation is re-derived from check-record.json."""
    from refactory_dsh.feedback_refill import refill

    workdir = _make_workdir(tmp_path)
    (workdir / "check-record.json").write_text(json.dumps({
        "status": "fail",
        "validation": "## Failing tests\n\n- test_foo.py::test_bar",
    }))
    values = {"name": "test", "brief": "test brief"}
    updated, refilled = refill(workdir, ["validation"], values)
    assert "validation" in refilled
    assert "Failing tests" in updated["validation"]


def test_refill_from_initial(tmp_path):
    """A redo before QA re-fills from feedback-initial.json, not refused."""
    from refactory_dsh.feedback_refill import refill

    workdir = _make_workdir(tmp_path)
    (workdir / "feedback-initial.json").write_text(json.dumps({
        "validation": "(no check has run yet)",
        "qa_gate": "(no review has refused anything yet)",
        "overwatch_review": "(no audit has refused anything yet)",
    }))
    values = {"name": "test", "brief": "test brief"}
    updated, refilled = refill(workdir, ["validation", "qa_gate", "overwatch_review"], values)
    assert set(refilled) == {"validation", "qa_gate", "overwatch_review"}
    assert updated["validation"] == "(no check has run yet)"


def test_refill_refuses_when_nothing_exists(tmp_path):
    """When no gate file or initial record exists, refill refuses."""
    from refactory_dsh.feedback_refill import refill

    workdir = _make_workdir(tmp_path)
    values = {"name": "test"}
    with pytest.raises(ValueError, match="unfilled"):
        refill(workdir, ["validation"], values)


def test_refill_skips_filled_keys(tmp_path):
    """Keys already in values are not re-filled."""
    from refactory_dsh.feedback_refill import refill

    workdir = _make_workdir(tmp_path)
    values = {"validation": "already set"}
    updated, refilled = refill(workdir, ["validation"], values)
    assert refilled == []
    assert updated["validation"] == "already set"


def test_refill_validation_from_newer_validation_txt(tmp_path):
    """When validation.txt is newer than check-record.json, use it."""
    from refactory_dsh.feedback_refill import refill
    import time

    workdir = _make_workdir(tmp_path)
    (workdir / "check-record.json").write_text(json.dumps({
        "status": "fail", "validation": "old digest",
    }))
    time.sleep(0.05)
    (workdir / "validation.txt").write_text("newer verification feedback\n")
    values = {}
    updated, refilled = refill(workdir, ["validation"], values)
    assert "validation" in refilled
    assert "newer verification" in updated["validation"]


def test_refill_qa_gate_from_file(tmp_path):
    """qa_gate is re-filled from reviews/qa-gate.txt."""
    from refactory_dsh.feedback_refill import refill

    workdir = _make_workdir(tmp_path)
    (workdir / "reviews").mkdir()
    (workdir / "reviews" / "qa-gate.txt").write_text("some QA feedback\n")
    values = {}
    updated, refilled = refill(workdir, ["qa_gate"], values)
    assert "qa_gate" in refilled
    assert "QA feedback" in updated["qa_gate"]


def test_refill_overwatch_from_file(tmp_path):
    """overwatch_review is re-filled from reviews/overwatch-gate.txt."""
    from refactory_dsh.feedback_refill import refill

    workdir = _make_workdir(tmp_path)
    (workdir / "reviews").mkdir()
    (workdir / "reviews" / "overwatch-gate.txt").write_text("audit feedback\n")
    values = {}
    updated, refilled = refill(workdir, ["overwatch_review"], values)
    assert "overwatch_review" in refilled
    assert "audit feedback" in updated["overwatch_review"]


def test_refill_repair_feedback_from_file(tmp_path):
    """repair_feedback is re-filled from repair.txt."""
    from refactory_dsh.feedback_refill import refill

    workdir = _make_workdir(tmp_path)
    (workdir / "repair.txt").write_text("repair notes\n")
    values = {}
    updated, refilled = refill(workdir, ["repair_feedback"], values)
    assert "repair_feedback" in refilled
    assert "repair notes" in updated["repair_feedback"]


def test_feedback_refill_entry_point():
    """The refactory.feedback_refill entry point is installed and resolves."""
    import importlib.metadata
    eps = importlib.metadata.entry_points(group='refactory.feedback_refill')
    assert len(list(eps)) > 0, "refactory.feedback_refill entry point should be installed"
    ep = list(eps)[0]
    fn = ep.load()
    assert callable(fn)


def test_fill_feedback_reads_declarations(tmp_path):
    """fill_feedback reads feedback_keys from declarations.json when not passed."""
    from refactory_dsh.agent_delivery import fill_feedback

    workdir = _make_workdir(tmp_path)
    decls = {
        "emit": {
            "kind": "agent", "reads": [], "writes": ["emit.txt"],
            "produces": ["emit"], "resource_role": "work",
            "feedback_keys": ["validation", "qa_gate"],
        }
    }
    (workdir / ".refactory" / "declarations.json").write_text(json.dumps(decls))
    (workdir / "feedback-initial.json").write_text(json.dumps({
        "validation": "(no check has run yet)",
        "qa_gate": "(no review has refused anything yet)",
    }))
    values = {"name": "test"}
    updated, refilled = fill_feedback(workdir, "emit", values)
    assert "validation" in refilled
    assert "qa_gate" in refilled


def test_redo_after_refusal_refills_from_check_record(tmp_path):
    """A redo after a materialise refusal re-fills from check-record.json's stored validation."""
    from refactory_dsh.feedback_refill import refill

    workdir = _make_workdir(tmp_path)
    refusal_msg = "materialise could not apply emit.txt: bad edit"
    (workdir / "check-record.json").write_text(json.dumps({
        "status": "refused", "validation": refusal_msg,
    }))
    values = {}
    updated, refilled = refill(workdir, ["validation"], values)
    assert "validation" in refilled
    assert updated["validation"] == refusal_msg


def test_redo_after_guardedness_refills_from_check_record(tmp_path):
    """A redo after a guardedness block re-fills from check-record.json."""
    from refactory_dsh.feedback_refill import refill

    workdir = _make_workdir(tmp_path)
    guarded_msg = "BLOCKING: these new tests pass without the change: test_foo"
    (workdir / "check-record.json").write_text(json.dumps({
        "status": "guarded", "validation": guarded_msg,
    }))
    values = {}
    updated, refilled = refill(workdir, ["validation"], values)
    assert "validation" in refilled
    assert updated["validation"] == guarded_msg
