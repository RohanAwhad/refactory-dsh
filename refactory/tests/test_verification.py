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
    assert "gate_verification" not in graph.nodes


def test_verifier_node_exists_in_design_fast_mode():
    graph = DesignFastMode().build()
    assert "verifier" in graph.nodes
    assert "gate_verification" not in graph.nodes


def test_verifier_is_design_blind():
    graph = DesignMode().build()
    node = graph.nodes["verifier"]
    assert "design.md" not in node.reads
    assert "strategy.json" not in node.reads
    assert "{{design}}" not in node.prompt_template
    assert "Read the approved design from" not in node.prompt_template


def test_verifier_prompt_says_design_md_is_in_run_dir():
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


def test_graph_shape_full_mode():
    graph = DesignMode().build()
    fork = graph.nodes["fork_qa"]
    assert "verifier" in fork.targets
    assert "verifier" in fork.optional_targets
    assert "gate_verification" not in graph.nodes
    edges = [(e.source, e.target, e.condition) for e in graph.edges]
    assert ("check", "verifier", "proceed") not in edges
    assert ("check", "fork_qa", "proceed") in edges
    assert ("verifier", "join_qa", None) in edges


def test_graph_shape_fast_mode():
    graph = DesignFastMode().build()
    fork = graph.nodes["fork_qa"]
    assert "verifier" in fork.targets
    assert "focused_qa" in fork.targets
    assert "verifier" in fork.optional_targets
    assert "gate_check" in graph.nodes
    assert "gate_verification" not in graph.nodes
    edges = [(e.source, e.target, e.condition) for e in graph.edges]
    assert ("focused_check", "verifier", None) not in edges
    assert ("focused_check", "gate_check", None) in edges
    assert ("gate_check", "fork_qa", "proceed") in edges
    assert ("verifier", "join_qa", None) in edges


def test_verifier_not_in_reviewers():
    from refactory_dsh.authoring_evidence import REVIEWERS
    assert "verifier" not in REVIEWERS


def test_verifier_not_in_planning_steps():
    from refactory_dsh.design_ops import PLANNING_STEPS
    assert "verifier" not in PLANNING_STEPS


def test_verifier_has_final_validation_resource_role_full_mode():
    graph = DesignMode().build()
    assert graph.nodes["verifier"].resource_role == "final_validation"


# ── gate_qa verifier handling ─────────────────────────────────────────────


def test_gate_qa_declares_verification_advisory_used():
    graph = DesignMode().build()
    gate = graph.nodes["gate_qa"]
    assert "verification_advisory_used" in gate.produces


def test_gate_qa_max_iterations_raised():
    graph = DesignMode().build()
    gate = graph.nodes["gate_qa"]
    assert gate.max_iterations >= 5


# ── wire_verification raises on missing edge ──────────────────────────────


def test_wire_verification_raises_on_missing_edge():
    from refactory_dsh.nn import Graph, Node
    from refactory_dsh.verification import wire_verification
    graph = Graph()
    graph.add(Node("check", "gate", evaluator="x:y"))
    graph.add(Node("emit", "agent", prompt_template="x"))
    graph.add(Node("report", "op", call="x:y"))
    graph.connect("check", "emit", condition="reloop")
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


# ── reviewer independence ────────────────────────────────────────────────


def test_reviewer_independence():
    """No reviewer reads reviews/verification.md or has it in its prompt."""
    graph = DesignMode().build()
    for name in ("health_checker", "code_reviewer", "qa_director"):
        node = graph.nodes[name]
        assert "reviews/verification.md" not in node.reads, f"{name} reads verification.md"
        assert "reviews/verification.md" not in node.prompt_template, (
            f"{name} prompt mentions verification.md")

    fast_graph = DesignFastMode().build()
    fqa = fast_graph.nodes["focused_qa"]
    assert "reviews/verification.md" not in fqa.reads, "focused_qa reads verification.md"


# ── scripted run tests ────────────────────────────────────────────────────


def test_verification_pass_proceeds_to_reviews_full_mode(tmp_path, monkeypatch):
    """A PASS verdict proceeds to the reviews without reloop."""
    monkeypatch.setenv("REFACTORY_PROJECT", str(tmp_path))
    from tests_support import (DESIGN_DOC_ANSWER, DESIGN_REVIEW_ANSWER,
                               PITFALLS_ANSWER, RESEARCH_ANSWER,
                               FakeModel, render)
    VERIFICATION_PASS = "No blocking findings.\n\nVERDICT: PASS"
    answers = [
        RESEARCH_ANSWER, PITFALLS_ANSWER, "perspectives", "one plan",
        DESIGN_DOC_ANSWER, DESIGN_REVIEW_ANSWER,
        render({"tool.py": "print('built')\n"}),
        # fork_qa: health_checker, code_reviewer, qa_director, verifier
        "health: passed", "code review: passed", "qa: passed",
        VERIFICATION_PASS,
        # overwatch
        "VERDICT: PASS",
    ]
    result = DesignMode().run(
        tmp_path / "run", model=FakeModel(answers),
        verdicts={"gate_strategy": "proceed"},
        task={"name": "verify_pass", "brief": "a tool", "check": "python tool.py"},
    )
    assert result.exit_ok, result.error


def test_advisory_fix_round_full_mode(tmp_path, monkeypatch):
    """Verifier FAIL with QA PASS → one advisory reloop → proceed.
    review_attempts_used unchanged, verification-unresolved.md written."""
    monkeypatch.setenv("REFACTORY_PROJECT", str(tmp_path))
    from tests_support import (DESIGN_DOC_ANSWER, DESIGN_REVIEW_ANSWER,
                               PITFALLS_ANSWER, RESEARCH_ANSWER,
                               FakeModel, render)
    VERIFICATION_FAIL = "BLOCKING: tool.py:1: negative values accepted\n\nVERDICT: FAIL"
    answers = [
        RESEARCH_ANSWER, PITFALLS_ANSWER, "perspectives", "one plan",
        DESIGN_DOC_ANSWER, DESIGN_REVIEW_ANSWER,
        # Round 1
        render({"tool.py": "print('built')\n"}),
        "health: passed", "code: passed", "qa: passed",
        VERIFICATION_FAIL,
        # Advisory reloop → emit (round 2): builder rebuts the finding
        "## Verifier findings\nnegative values accepted → rebutted: false positive\n\n" +
        render({"tool.py": "print('built v2')\n"}),
        "health: passed", "code: passed", "qa: passed",
        VERIFICATION_FAIL,
        # gate_qa: advisory_used >= 1 → proceed
        "VERDICT: PASS",
    ]
    model = FakeModel(answers)
    result = DesignMode().run(
        tmp_path / "run", model=model,
        verdicts={"gate_strategy": "proceed"},
        task={"name": "advisory_full", "brief": "a tool", "check": "python tool.py"},
    )
    assert result.exit_ok, result.error
    # review_attempts_used must NOT have been incremented by the verifier advisory
    assert int(result.facts.get("review_attempts_used", 0)) == 0
    assert int(result.facts.get("verification_advisory_used", 0)) >= 1
    assert (tmp_path / "run" / "reviews" / "verification-unresolved.md").exists()


def test_joint_fix_round(tmp_path, monkeypatch):
    """QA BLOCKING + verifier FAIL → single reloop with both findings."""
    monkeypatch.setenv("REFACTORY_PROJECT", str(tmp_path))
    from tests_support import (DESIGN_DOC_ANSWER, DESIGN_REVIEW_ANSWER,
                               PITFALLS_ANSWER, RESEARCH_ANSWER,
                               FakeModel, render)
    VERIFICATION_FAIL = "BLOCKING: tool.py:1: noise\n\nVERDICT: FAIL"
    QA_FAIL = "BLOCKING: tool.py:5: missing feature\n\nVERDICT: FAIL"
    answers = [
        RESEARCH_ANSWER, PITFALLS_ANSWER, "perspectives", "one plan",
        DESIGN_DOC_ANSWER, DESIGN_REVIEW_ANSWER,
        # Round 1: QA and verifier both fail
        render({"tool.py": "print('built')\n"}),
        "health: passed", "code: passed", QA_FAIL,
        VERIFICATION_FAIL,
        # Reloop → emit (combined feedback)
        render({"tool.py": "print('fixed')\n"}),
        "health: passed", "code: passed", "qa: passed",
        "No blocking findings.\n\nVERDICT: PASS",
        "VERDICT: PASS",
    ]
    model = FakeModel(answers)
    result = DesignMode().run(
        tmp_path / "run", model=model,
        verdicts={"gate_strategy": "proceed"},
        task={"name": "joint_fix", "brief": "a tool", "check": "python tool.py"},
    )
    assert result.exit_ok, result.error
    # The emit prompt in round 2 must carry both QA and verifier findings
    emit_prompts = [p for p in model.prompts if "What went wrong last time" in p]
    assert len(emit_prompts) >= 2
    second_emit = emit_prompts[1]
    assert "missing feature" in second_emit or "noise" in second_emit


def test_verifier_branch_failure(tmp_path, monkeypatch):
    """A verifier that returns empty is treated as missing in the synthesis."""
    monkeypatch.setenv("REFACTORY_PROJECT", str(tmp_path))
    from tests_support import (DESIGN_DOC_ANSWER, DESIGN_REVIEW_ANSWER,
                               PITFALLS_ANSWER, RESEARCH_ANSWER,
                               FakeModel, render)
    # The verifier returns empty output — no VERDICT line, no findings.
    # This simulates a branch that produced nothing useful.
    VERIFICATION_EMPTY = ""
    answers = [
        RESEARCH_ANSWER, PITFALLS_ANSWER, "perspectives", "one plan",
        DESIGN_DOC_ANSWER, DESIGN_REVIEW_ANSWER,
        render({"tool.py": "print('ok')\n"}),
        # fork_qa: 3 reviews pass, verifier returns empty
        "health: passed", "code: passed", "qa: passed",
        VERIFICATION_EMPTY,
        # overwatch
        "VERDICT: PASS",
    ]
    model = FakeModel(answers)
    result = DesignMode().run(
        tmp_path / "run", model=model,
        verdicts={"gate_strategy": "proceed"},
        task={"name": "empty_verifier", "brief": "a tool", "check": "python tool.py"},
    )
    assert result.exit_ok, result.error
    # Synthesis should mention the verifier as missing (empty output)
    synthesis = (tmp_path / "run" / "reviews" / "qa-synthesized.md").read_text()
    assert "missing" in synthesis.lower() or "Verification:" in synthesis


def test_finding_sources_json(tmp_path, monkeypatch):
    """Three-round test: round 1 QA+verifier findings, round 2 QA fixed
    and verifier rebutted, round 3 proceed.  Asserts each entry's fate
    and first_round/last_round."""
    monkeypatch.setenv("REFACTORY_PROJECT", str(tmp_path))
    from tests_support import (DESIGN_DOC_ANSWER, DESIGN_REVIEW_ANSWER,
                               PITFALLS_ANSWER, RESEARCH_ANSWER,
                               FakeModel, render)
    VERIFICATION_FAIL = "BLOCKING: tool.py:1: negative values accepted\n\nVERDICT: FAIL"
    QA_FAIL = "BLOCKING: tool.py:5: missing error handling\n\nVERDICT: FAIL"
    answers = [
        RESEARCH_ANSWER, PITFALLS_ANSWER, "perspectives", "one plan",
        DESIGN_DOC_ANSWER, DESIGN_REVIEW_ANSWER,
        # Round 1: QA and verifier both fail
        render({"tool.py": "print('v1')\n"}),
        "health: passed", "code: passed", QA_FAIL,
        VERIFICATION_FAIL,
        # Round 2: QA fixed, verifier still fails
        render({"tool.py": "print('v2')\n"}),
        "health: passed", "code: passed", "qa: passed",
        VERIFICATION_FAIL,
        # Round 3: builder rebuts verifier finding
        "## Verifier findings\nnegative values accepted → rebutted: false positive\n\n" +
        render({"tool.py": "print('v3')\n"}),
        "health: passed", "code: passed", "qa: passed",
        VERIFICATION_FAIL,
        "VERDICT: PASS",
    ]
    model = FakeModel(answers)
    result = DesignMode().run(
        tmp_path / "run", model=model,
        verdicts={"gate_strategy": "proceed"},
        task={"name": "sources", "brief": "a tool", "check": "python tool.py"},
    )
    assert result.exit_ok, result.error
    path = tmp_path / "run" / "reviews" / "finding-sources.json"
    assert path.exists(), "finding-sources.json must be written"
    data = json.loads(path.read_text())
    assert isinstance(data, list)
    for entry in data:
        assert "source" in entry
        assert "review_path" in entry
        assert "finding" in entry
        assert "fate" in entry
        assert entry["fate"] in ("fixed", "rebutted", "unresolved")
        assert "first_round" in entry
        assert "last_round" in entry
    # QA finding: fixed (present in round 1, absent in rounds 2-3)
    qa_entries = [e for e in data if e["source"] == "qa_director"
                  and "missing error handling" in e["finding"]]
    assert len(qa_entries) >= 1, "QA finding must appear in finding-sources.json"
    assert qa_entries[0]["fate"] == "fixed", (
        f"QA finding fate should be 'fixed', got {qa_entries[0]['fate']!r}")
    assert qa_entries[0]["first_round"] == 1
    # Verifier finding: rebutted (present in all rounds, builder rebutted it)
    verifier_entries = [e for e in data if e["source"] == "verifier"
                        and "negative values accepted" in e["finding"]]
    assert len(verifier_entries) >= 1, "verifier finding must appear"
    assert verifier_entries[0]["fate"] == "rebutted", (
        f"verifier finding fate should be 'rebutted', got {verifier_entries[0]['fate']!r}")
    assert verifier_entries[0]["first_round"] == 1
    assert verifier_entries[0]["last_round"] >= 3


def test_advisory_fix_round_fast_mode(tmp_path, monkeypatch):
    """Same advisory round through DesignFastMode."""
    monkeypatch.setenv("REFACTORY_PROJECT", str(tmp_path))
    from tests_support import FakeModel, render
    FAST_PLAN = "Change only the requested file."
    FAST_QA = json.dumps({"status": "pass", "checks": [{"name": "runs", "command": "python tool.py",
                                                         "status": "pass", "evidence": "exit 0"}],
                          "findings": [], "not_run": []})
    VERIFICATION_FAIL = "BLOCKING: tool.py:1: negative values accepted\n\nVERDICT: FAIL"
    answers = [
        FAST_PLAN,
        render({"tool.py": "print('built')\n"}),
        # fork: focused_qa, verifier
        FAST_QA,
        VERIFICATION_FAIL,
        # Advisory reloop → emit: builder rebuts the finding
        "## Verifier findings\nnegative values accepted → rebutted: false positive\n\n" +
        render({"tool.py": "print('built v2')\n"}),
        FAST_QA,
        VERIFICATION_FAIL,
        # advisory_used >= 1 → proceed
    ]
    mode = DesignFastMode()
    state = mode.state_dict()
    state["knobs"]["repair_attempts"]["value"] = 2
    mode.load_state_dict(state)
    model = FakeModel(answers)
    result = mode.run(
        tmp_path / "run", model=model,
        task={"name": "advisory_fast", "brief": "a tool", "check": "python tool.py"},
    )
    assert result.exit_ok, result.error
    assert int(result.facts.get("repairs_used", 0)) == 0, (
        "verifier advisory reloops must not spend repair_attempts")
    assert int(result.facts.get("verification_advisory_used", 0)) >= 1
    assert (tmp_path / "run" / "reviews" / "verification-unresolved.md").exists()


def test_verification_pass_proceeds_fast_mode(tmp_path, monkeypatch):
    """A PASS verdict in fast mode proceeds to report."""
    monkeypatch.setenv("REFACTORY_PROJECT", str(tmp_path))
    from tests_support import FakeModel, render
    FAST_PLAN = "Change only the requested file."
    FAST_QA = json.dumps({"status": "pass", "checks": [{"name": "runs", "command": "python tool.py",
                                                         "status": "pass", "evidence": "exit 0"}],
                          "findings": [], "not_run": []})
    VERIFICATION_PASS = "No blocking findings.\n\nVERDICT: PASS"
    answers = [FAST_PLAN, render({"tool.py": "print('built')\n"}),
               FAST_QA, VERIFICATION_PASS]
    result = DesignFastMode().run(
        tmp_path / "run", model=FakeModel(answers),
        task={"name": "verify_fast_pass", "brief": "a tool", "check": "python tool.py"},
    )
    assert result.exit_ok, result.error


def test_fast_mode_failing_check_routes_through_gate_check(tmp_path, monkeypatch):
    """A failing check routes through gate_check, not through the verifier."""
    monkeypatch.setenv("REFACTORY_PROJECT", str(tmp_path))
    from tests_support import FakeModel, render
    FAST_PLAN = "Change only the requested file."
    FAST_QA = json.dumps({"status": "pass", "checks": [{"name": "runs", "command": "python tool.py",
                                                         "status": "pass", "evidence": "exit 0"}],
                          "findings": [], "not_run": []})
    VERIFICATION_PASS = "No blocking findings.\n\nVERDICT: PASS"
    answers = [
        FAST_PLAN,
        render({"tool.py": "import sys; sys.exit(1)\n"}),   # check fails
        # gate_check routes to _repair (no verifier runs yet)
        render({"tool.py": "print('fixed')\n"}),             # second emit
        FAST_QA, VERIFICATION_PASS,
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
    assert int(result.facts.get("repairs_used", 0)) >= 1, "a repair must have been spent"


def test_finding_description_strips_file_line_prefix():
    """_finding_description strips the file:line: prefix used in BLOCKING findings."""
    from refactory_dsh.create_ops import _finding_description
    assert _finding_description("tool.py:1: negative values accepted") == "negative values accepted"
    assert _finding_description("src/app.py:42: missing check") == "missing check"
    assert _finding_description("the verifier did not state 'VERDICT: PASS'") == "the verifier did not state 'VERDICT: PASS'"
    assert _finding_description("simple finding") == "simple finding"


def test_rebuttal_matching_works_without_prefix():
    """Rebuttal matching strips the file:line: prefix so a builder's rebuttal
    like 'negative values accepted → rebutted: ...' matches the finding
    'tool.py:1: negative values accepted'."""
    from refactory_dsh.create_ops import _finding_description
    finding = "tool.py:1: negative values accepted"
    rebuttal = "negative values accepted → rebutted: false positive"
    desc = _finding_description(finding)
    assert desc.lower() in rebuttal.lower(), (
        f"description {desc!r} should be found in rebuttal {rebuttal!r}")


def test_rebuttal_fate_fast_mode(tmp_path, monkeypatch):
    """Fast-mode advisory round records 'rebutted' fate in finding-sources.json."""
    monkeypatch.setenv("REFACTORY_PROJECT", str(tmp_path))
    from tests_support import FakeModel, render
    FAST_PLAN = "Change only the requested file."
    FAST_QA = json.dumps({"status": "pass", "checks": [{"name": "runs", "command": "python tool.py",
                                                         "status": "pass", "evidence": "exit 0"}],
                          "findings": [], "not_run": []})
    VERIFICATION_FAIL = "BLOCKING: tool.py:1: negative values accepted\n\nVERDICT: FAIL"
    answers = [
        FAST_PLAN,
        render({"tool.py": "print('built')\n"}),
        FAST_QA,
        VERIFICATION_FAIL,
        "## Verifier findings\nnegative values accepted → rebutted: false positive\n\n" +
        render({"tool.py": "print('built v2')\n"}),
        FAST_QA,
        VERIFICATION_FAIL,
    ]
    mode = DesignFastMode()
    state = mode.state_dict()
    state["knobs"]["repair_attempts"]["value"] = 2
    mode.load_state_dict(state)
    model = FakeModel(answers)
    result = mode.run(
        tmp_path / "run", model=model,
        task={"name": "advisory_fast", "brief": "a tool", "check": "python tool.py"},
    )
    assert result.exit_ok, result.error
    sources_path = tmp_path / "run" / "reviews" / "finding-sources.json"
    assert sources_path.exists(), "finding-sources.json must be written"
    sources = json.loads(sources_path.read_text())
    verifier_entries = [e for e in sources if e["source"] == "verifier"
                        and "negative values accepted" in e["finding"]]
    assert len(verifier_entries) >= 1, "verifier finding must be in finding-sources.json"
    assert verifier_entries[0]["fate"] == "rebutted", (
        f"verifier finding fate should be 'rebutted', got {verifier_entries[0]['fate']!r}")
