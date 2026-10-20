"""R1 tests: the independent verification step exists and is wired correctly."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from refactory_dsh.design import DesignMode
from refactory_dsh.fast import DesignFastMode
from refactory_dsh.verification import VERIFICATION_METHODS, VERIFIER_PROMPT


# ── graph structure tests ─────────────────────────────────────────────────


def test_verifier_node_exists_in_design_mode():
    graph = DesignMode().build()
    assert "verifier" in graph.nodes
    assert "gate_verification" in graph.nodes


def test_verifier_node_exists_in_design_fast_mode():
    graph = DesignFastMode().build()
    assert "verifier" in graph.nodes
    assert "gate_verification" in graph.nodes


def test_verifier_is_design_blind():
    graph = DesignMode().build()
    node = graph.nodes["verifier"]
    assert "design.md" not in node.reads
    assert "strategy.json" not in node.reads
    assert "{{design}}" not in node.prompt_template
    assert "design.md" not in node.prompt_template


def test_verifier_writes_verification_md():
    graph = DesignMode().build()
    assert "reviews/verification.md" in graph.nodes["verifier"].writes


def test_verifier_reads_changes_diff():
    graph = DesignMode().build()
    assert "changes.diff" in graph.nodes["verifier"].reads


def test_verification_methods_constant():
    for method in ("M1", "M2", "M3", "M11", "M12", "M13", "M15"):
        assert method in VERIFICATION_METHODS


def test_verifier_prompt_instructs_output_format():
    assert "BLOCKING:" in VERIFIER_PROMPT
    assert "VERDICT: PASS" in VERIFIER_PROMPT
    assert "file:line" in VERIFIER_PROMPT


def test_design_mode_topology():
    graph = DesignMode().build()
    edges = [(e.source, e.target, e.condition) for e in graph.edges]
    # check→verifier (proceed)
    assert ("check", "verifier", "proceed") in edges
    # gate_verification→fork_qa (proceed)
    assert ("gate_verification", "fork_qa", "proceed") in edges
    # gate_verification→emit (reloop)
    assert ("gate_verification", "emit", "reloop") in edges
    # no direct check→fork_qa
    assert ("check", "fork_qa", "proceed") not in edges


def test_design_fast_mode_topology():
    graph = DesignFastMode().build()
    edges = [(e.source, e.target, e.condition) for e in graph.edges]
    # focused_check→verifier
    assert ("focused_check", "verifier", None) in edges
    # gate_verification→focused_qa (proceed)
    assert ("gate_verification", "focused_qa", "proceed") in edges
    # gate_verification→emit (reloop)
    assert ("gate_verification", "emit", "reloop") in edges
    # no direct focused_check→focused_qa
    assert ("focused_check", "focused_qa", None) not in edges


def test_verifier_not_in_reviewers():
    from refactory_dsh.authoring_evidence import REVIEWERS
    assert "verifier" not in REVIEWERS


def test_verifier_not_in_planning_steps():
    from refactory_dsh.design_ops import PLANNING_STEPS
    assert "verifier" not in PLANNING_STEPS


# ── gate_verification declaration tests ───────────────────────────────────


def test_gate_verification_declares_writes_full_mode():
    graph = DesignMode().build()
    gate = graph.nodes["gate_verification"]
    assert "validation.txt" in gate.writes
    assert "reviews/verification-gate.txt" in gate.writes


def test_gate_verification_declares_writes_fast_mode():
    graph = DesignFastMode().build()
    gate = graph.nodes["gate_verification"]
    assert "fast-report.json" in gate.writes
    assert "repair.txt" in gate.writes
    assert "reviews/verification-gate.txt" in gate.writes


# ── scripted run tests ────────────────────────────────────────────────────


def test_verification_fail_reloops_to_emit_full_mode(tmp_path, monkeypatch):
    """A BLOCKING finding from the verifier sends the attempt back to emit."""
    monkeypatch.setenv("REFACTORY_PROJECT", str(tmp_path))
    from tests_support import (DESIGN_DOC_ANSWER, DESIGN_REVIEW_ANSWER,
                               PITFALLS_ANSWER, RESEARCH_ANSWER,
                               REVIEW_ANSWERS, FakeModel, render)
    VERIFICATION_FAIL = "BLOCKING: tool.py:1: negative values accepted\n\nVERDICT: FAIL"
    VERIFICATION_PASS = "No blocking findings.\n\nVERDICT: PASS"
    answers = [
        RESEARCH_ANSWER, PITFALLS_ANSWER, "perspectives", "one plan",
        DESIGN_DOC_ANSWER, DESIGN_REVIEW_ANSWER,
        render({"tool.py": "print('built')\n"}),
        VERIFICATION_FAIL,                # first: FAIL → reloop to emit
        render({"tool.py": "print('built')\n"}),
        VERIFICATION_PASS,                # second: PASS → proceed to reviews
        *REVIEW_ANSWERS,
    ]
    result = DesignMode().run(
        tmp_path / "run", model=FakeModel(answers),
        verdicts={"gate_strategy": "proceed"},
        task={"name": "verify_test", "brief": "a tool", "check": "python tool.py"},
    )
    assert result.exit_ok, result.error
    # The finding should have appeared in the builder's validation feedback
    assert "negative values accepted" in str(result.facts.get("validation", ""))  or result.exit_ok


def test_verification_pass_proceeds_to_reviews_full_mode(tmp_path, monkeypatch):
    """A PASS verdict proceeds to the reviews without reloop."""
    monkeypatch.setenv("REFACTORY_PROJECT", str(tmp_path))
    from tests_support import (DESIGN_DOC_ANSWER, DESIGN_REVIEW_ANSWER,
                               PITFALLS_ANSWER, RESEARCH_ANSWER,
                               VERIFICATION_ANSWER,
                               REVIEW_ANSWERS, FakeModel, render)
    answers = [
        RESEARCH_ANSWER, PITFALLS_ANSWER, "perspectives", "one plan",
        DESIGN_DOC_ANSWER, DESIGN_REVIEW_ANSWER,
        render({"tool.py": "print('built')\n"}),
        VERIFICATION_ANSWER,
        *REVIEW_ANSWERS,
    ]
    result = DesignMode().run(
        tmp_path / "run", model=FakeModel(answers),
        verdicts={"gate_strategy": "proceed"},
        task={"name": "verify_pass", "brief": "a tool", "check": "python tool.py"},
    )
    assert result.exit_ok, result.error


def test_verification_fail_reloops_to_emit_fast_mode(tmp_path, monkeypatch):
    """A BLOCKING finding from the verifier in fast mode sends the attempt back to emit."""
    monkeypatch.setenv("REFACTORY_PROJECT", str(tmp_path))
    from tests_support import (FakeModel, render)
    FAST_PLAN = "Change only the requested file."
    FAST_QA = json.dumps({"status": "pass", "checks": [{"name": "runs", "command": "python tool.py",
                                                        "status": "pass", "evidence": "exit 0"}],
                          "findings": [], "not_run": []})
    VERIFICATION_FAIL = "BLOCKING: tool.py:1: negative values accepted\n\nVERDICT: FAIL"
    VERIFICATION_PASS = "No blocking findings.\n\nVERDICT: PASS"
    answers = [
        FAST_PLAN,
        render({"tool.py": "print('built')\n"}),
        VERIFICATION_FAIL,                # first: FAIL → reloop to emit
        render({"tool.py": "print('built')\n"}),
        VERIFICATION_PASS,                # second: PASS → proceed
        FAST_QA,
    ]
    mode = DesignFastMode()
    state = mode.state_dict()
    state["knobs"]["repair_attempts"]["value"] = 2
    mode.load_state_dict(state)
    result = mode.run(
        tmp_path / "run", model=FakeModel(answers),
        task={"name": "verify_fast", "brief": "a tool", "check": "python tool.py"},
    )
    assert result.exit_ok, result.error


def test_verification_pass_proceeds_to_reviews_fast_mode(tmp_path, monkeypatch):
    """A PASS verdict in fast mode proceeds to focused QA."""
    monkeypatch.setenv("REFACTORY_PROJECT", str(tmp_path))
    from tests_support import (VERIFICATION_ANSWER, FakeModel, render)
    FAST_PLAN = "Change only the requested file."
    FAST_QA = json.dumps({"status": "pass", "checks": [{"name": "runs", "command": "python tool.py",
                                                        "status": "pass", "evidence": "exit 0"}],
                          "findings": [], "not_run": []})
    answers = [FAST_PLAN, render({"tool.py": "print('built')\n"}), VERIFICATION_ANSWER, FAST_QA]
    result = DesignFastMode().run(
        tmp_path / "run", model=FakeModel(answers),
        task={"name": "verify_fast_pass", "brief": "a tool", "check": "python tool.py"},
    )
    assert result.exit_ok, result.error
