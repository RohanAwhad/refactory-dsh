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
    # The prompt says "Do NOT read design.md" which is correct — it tells the
    # verifier to avoid design.md, not to consume it as input.
    assert "Read the approved design from" not in node.prompt_template


def test_verifier_prompt_says_design_md_is_in_run_dir():
    """The prompt must say design.md is in the run directory and must not be read."""
    assert "design.md is in the run directory" in VERIFIER_PROMPT


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


def test_verifier_has_final_validation_resource_role_full_mode():
    graph = DesignMode().build()
    assert graph.nodes["verifier"].resource_role == "final_validation"


# ── gate_verification declaration tests ───────────────────────────────────


def test_gate_verification_declares_writes_full_mode():
    graph = DesignMode().build()
    gate = graph.nodes["gate_verification"]
    assert "validation.txt" in gate.writes
    assert "reviews/verification-gate.txt" in gate.writes
    assert "reviews/verification-unresolved.md" in gate.writes


def test_gate_verification_declares_writes_fast_mode():
    graph = DesignFastMode().build()
    gate = graph.nodes["gate_verification"]
    assert "fast-report.json" in gate.writes
    assert "repair.txt" in gate.writes
    assert "reviews/verification-gate.txt" in gate.writes
    assert "reviews/verification-unresolved.md" in gate.writes


def test_gate_verification_does_not_spend_review_attempts_full():
    """Full-mode gate_verification must NOT declare review_attempts_used."""
    graph = DesignMode().build()
    gate = graph.nodes["gate_verification"]
    assert "review_attempts_used" not in gate.produces, (
        "verification findings must not spend review_attempts"
    )
    assert "attempts_used" not in gate.produces, (
        "verification findings must not spend overall attempts"
    )


def test_gate_verification_does_not_spend_repair_attempts_fast():
    """Fast-mode gate_verification must NOT declare review_repairs_used."""
    graph = DesignFastMode().build()
    gate = graph.nodes["gate_verification"]
    assert "review_repairs_used" not in gate.produces, (
        "verification findings must not spend repair_attempts"
    )


# ── wire_verification raises on missing edge ──────────────────────────────


def test_wire_verification_raises_on_missing_edge():
    """wire_verification must raise when the edge it tries to remove does not exist."""
    from refactory_dsh.nn import Graph, Node
    from refactory_dsh.verification import wire_verification
    graph = Graph()
    graph.add(Node("check", "gate", evaluator="x:y"))
    graph.add(Node("emit", "agent", prompt_template="x"))
    graph.add(Node("report", "op", call="x:y"))
    graph.connect("check", "emit", condition="reloop")
    # No check→report(proceed) edge — should raise.
    with pytest.raises(ValueError, match="no edge"):
        wire_verification(graph, check_node="check", proceed_target="report")


# ── fix_pack gets the verifier ────────────────────────────────────────────


def test_fix_pack_has_verifier():
    from refactory_dsh import registry
    from refactory_dsh.paths import REPO_ROOT
    mode = registry.resolve(REPO_ROOT, "fix_pack/make_pass")[1]()
    graph = mode.graph()
    assert "verifier" in graph.nodes
    assert "gate_verification" in graph.nodes
    edges = [(e.source, e.target, e.condition) for e in graph.edges]
    assert ("check", "verifier", "proceed") in edges
    assert ("gate_verification", "report", "proceed") in edges
    assert ("gate_verification", "emit", "reloop") in edges
    assert ("check", "report", "proceed") not in edges


# ── scripted run tests ────────────────────────────────────────────────────


def test_verification_fail_reloops_to_emit_full_mode(tmp_path, monkeypatch):
    """A BLOCKING finding from the verifier sends the attempt back to emit,
    and the BLOCKING text appears in what the next emit receives."""
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
        render({"tool.py": "print('built v2')\n"}),  # different content to avoid no-change refusal
        VERIFICATION_PASS,                # second: PASS → proceed to reviews
        *REVIEW_ANSWERS,
    ]
    model = FakeModel(answers)
    result = DesignMode().run(
        tmp_path / "run", model=model,
        verdicts={"gate_strategy": "proceed"},
        task={"name": "verify_test", "brief": "a tool", "check": "python tool.py"},
    )
    assert result.exit_ok, result.error
    # The second emit prompt must contain the BLOCKING text from the verifier
    emit_prompts = [p for p in model.prompts if "What went wrong last time" in p]
    assert len(emit_prompts) >= 2, "emit must have been called at least twice"
    second_emit = emit_prompts[1]
    assert "negative values accepted" in second_emit, (
        "the BLOCKING finding must reach the builder in the next emit's prompt"
    )


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
    """A BLOCKING finding from the verifier in fast mode sends the attempt back to emit,
    and the BLOCKING text appears in what the next emit receives."""
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
        render({"tool.py": "print('built v2')\n"}),  # different content to avoid no-change refusal
        VERIFICATION_PASS,                # second: PASS → proceed
        FAST_QA,
    ]
    mode = DesignFastMode()
    state = mode.state_dict()
    state["knobs"]["repair_attempts"]["value"] = 2
    mode.load_state_dict(state)
    model = FakeModel(answers)
    result = mode.run(
        tmp_path / "run", model=model,
        task={"name": "verify_fast", "brief": "a tool", "check": "python tool.py"},
    )
    assert result.exit_ok, result.error
    # The second emit prompt must contain the BLOCKING text from the verifier
    emit_prompts = [p for p in model.prompts if "Repair feedback:" in p]
    assert len(emit_prompts) >= 2, "emit must have been called at least twice"
    second_emit = emit_prompts[1]
    assert "negative values accepted" in second_emit, (
        "the BLOCKING finding must reach the builder in the next emit's prompt"
    )


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


def test_noisy_verifier_does_not_end_run_full_mode(tmp_path, monkeypatch):
    """Five consecutive verifier FAILs must not end the run; the gate proceeds."""
    monkeypatch.setenv("REFACTORY_PROJECT", str(tmp_path))
    from tests_support import (DESIGN_DOC_ANSWER, DESIGN_REVIEW_ANSWER,
                               PITFALLS_ANSWER, RESEARCH_ANSWER,
                               REVIEW_ANSWERS, FakeModel, render)
    VERIFICATION_FAIL = "BLOCKING: tool.py:1: noise\n\nVERDICT: FAIL"
    # The verifier fails on every attempt.  After VERIFIER_GATE_LIMIT reloops
    # the gate must proceed to reviews instead of raising.
    answers = [
        RESEARCH_ANSWER, PITFALLS_ANSWER, "perspectives", "one plan",
        DESIGN_DOC_ANSWER, DESIGN_REVIEW_ANSWER,
        render({"tool.py": "print('built')\n"}),
        VERIFICATION_FAIL,                   # attempt 1 → reloop
        render({"tool.py": "print('v2')\n"}),
        VERIFICATION_FAIL,                   # attempt 2 → reloop
        render({"tool.py": "print('v3')\n"}),
        VERIFICATION_FAIL,                   # attempt 3 → proceed (at limit)
        *REVIEW_ANSWERS,
    ]
    result = DesignMode().run(
        tmp_path / "run", model=FakeModel(answers),
        verdicts={"gate_strategy": "proceed"},
        task={"name": "noisy_full", "brief": "a tool", "check": "python tool.py"},
    )
    assert result.exit_ok, result.error
    # Unresolved findings must be recorded.
    assert (tmp_path / "run" / "reviews" / "verification-unresolved.md").exists()


def test_noisy_verifier_does_not_end_run_fast_mode(tmp_path, monkeypatch):
    """Five consecutive verifier FAILs in fast mode must not end the run."""
    monkeypatch.setenv("REFACTORY_PROJECT", str(tmp_path))
    from tests_support import FakeModel, render
    FAST_PLAN = "Change only the requested file."
    FAST_QA = json.dumps({"status": "pass", "checks": [{"name": "runs", "command": "python tool.py",
                                                         "status": "pass", "evidence": "exit 0"}],
                          "findings": [], "not_run": []})
    VERIFICATION_FAIL = "BLOCKING: tool.py:1: noise\n\nVERDICT: FAIL"
    answers = [
        FAST_PLAN,
        render({"tool.py": "print('v1')\n"}),
        VERIFICATION_FAIL,
        render({"tool.py": "print('v2')\n"}),
        VERIFICATION_FAIL,
        render({"tool.py": "print('v3')\n"}),
        VERIFICATION_FAIL,                   # at limit → proceed
        FAST_QA,
    ]
    mode = DesignFastMode()
    state = mode.state_dict()
    state["knobs"]["repair_attempts"]["value"] = 3
    mode.load_state_dict(state)
    result = mode.run(
        tmp_path / "run", model=FakeModel(answers),
        task={"name": "noisy_fast", "brief": "a tool", "check": "python tool.py"},
    )
    assert result.exit_ok, result.error
    assert (tmp_path / "run" / "reviews" / "verification-unresolved.md").exists()


def test_fast_mode_failing_check_plus_verifier_fail(tmp_path, monkeypatch):
    """A failing check plus a verifier FAIL: emit gets the check failure, repair is spent."""
    monkeypatch.setenv("REFACTORY_PROJECT", str(tmp_path))
    from tests_support import FakeModel, render, VERIFICATION_ANSWER
    FAST_PLAN = "Change only the requested file."
    FAST_QA = json.dumps({"status": "pass", "checks": [{"name": "runs", "command": "python tool.py",
                                                         "status": "pass", "evidence": "exit 0"}],
                          "findings": [], "not_run": []})
    VERIFICATION_FAIL = "BLOCKING: tool.py:1: noise\n\nVERDICT: FAIL"
    answers = [
        FAST_PLAN,
        render({"tool.py": "import sys; sys.exit(1)\n"}),   # check fails
        VERIFICATION_FAIL,                                    # verifier also fails
        # gate_verification sees check failure → _repair with check text
        render({"tool.py": "print('fixed')\n"}),             # second emit: check passes
        VERIFICATION_ANSWER,
        FAST_QA,
    ]
    mode = DesignFastMode()
    state = mode.state_dict()
    state["knobs"]["repair_attempts"]["value"] = 2
    mode.load_state_dict(state)
    model = FakeModel(answers)
    result = mode.run(
        tmp_path / "run", model=model,
        task={"name": "check_fail_fast", "brief": "a tool", "check": "python tool.py"},
    )
    assert result.exit_ok, result.error
    # The repair feedback must carry the check failure, not the verifier noise.
    repair_prompts = [p for p in model.prompts if "Repair feedback:" in p]
    assert len(repair_prompts) >= 1, "emit must have received repair feedback"
    assert "noise" not in repair_prompts[0].lower() or "check failed" in repair_prompts[0].lower() or "exit" in repair_prompts[0].lower(), (
        "the builder must receive the check failure, not just verifier noise"
    )
    assert int(result.facts.get("repairs_used", 0)) >= 1, "a repair must have been spent"


def test_fast_mode_repair_1_two_verifier_fails_still_reaches_qa(tmp_path, monkeypatch):
    """With repair_attempts=1 and two verifier FAILs, the run still reaches QA."""
    monkeypatch.setenv("REFACTORY_PROJECT", str(tmp_path))
    from tests_support import FakeModel, render
    FAST_PLAN = "Change only the requested file."
    FAST_QA = json.dumps({"status": "pass", "checks": [{"name": "runs", "command": "python tool.py",
                                                         "status": "pass", "evidence": "exit 0"}],
                          "findings": [], "not_run": []})
    VERIFICATION_FAIL = "BLOCKING: tool.py:1: noise\n\nVERDICT: FAIL"
    answers = [
        FAST_PLAN,
        render({"tool.py": "print('ok')\n"}),
        VERIFICATION_FAIL,                    # 1st FAIL → advisory reloop
        render({"tool.py": "print('ok2')\n"}),
        VERIFICATION_FAIL,                    # 2nd FAIL → advisory reloop
        render({"tool.py": "print('ok3')\n"}),
        VERIFICATION_FAIL,                    # 3rd FAIL → at limit, proceed
        FAST_QA,
    ]
    mode = DesignFastMode()
    state = mode.state_dict()
    state["knobs"]["repair_attempts"]["value"] = 1
    mode.load_state_dict(state)
    result = mode.run(
        tmp_path / "run", model=FakeModel(answers),
        task={"name": "repair1_fast", "brief": "a tool", "check": "python tool.py"},
    )
    assert result.exit_ok, result.error
    # The verifier's advisory reloops must NOT have spent repair_attempts.
    assert int(result.facts.get("repairs_used", 0)) == 0, (
        "verifier advisory reloops must not spend repair_attempts"
    )
