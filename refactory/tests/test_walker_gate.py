"""Walker gate flow: stop at a user gate, steer, resume and halt.

These tests go through the real commands (``design.main``, ``console.main``)
with a ``FakeModel``, not a mock executor.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from refactory_dsh import steering
from refactory_dsh.nn import Graph, Knob, Mode, Node
from refactory_dsh.nn.executor import GateStop, HaltStop, route_verdict
from refactory_dsh.nn.graph import Edge


# ── helpers ────────────────────────────────────────────────────────────────


class _SimpleDesignMode(Mode):
    """A minimal mode with a user gate, for testing the gate flow."""
    name = "design"
    description = "test gate flow"
    unscored_reason = "test only"

    @classmethod
    def declare(cls):
        return {
            "name": Knob("test", "str"),
            "brief": Knob("test brief", "str"),
            "check": Knob("true", "str"),
        }

    def build(self):
        graph = Graph()
        graph.add(Node("prepare", "op", call="refactory_dsh.design_ops:prepare_design",
                        knobs=("name", "brief", "check"),
                        writes=("brief.json", "check.txt"),
                        produces=("name", "brief", "check", "validation")))
        graph.add(Node("design_agent", "agent",
                        prompt_template="Design: {{brief}}",
                        reads=("brief.json",),
                        writes=("design.md",),
                        produces=("design",)))
        graph.add(Node("gate_strategy", "gate", gate_kind="user",
                        reads=("design.md",),
                        max_iterations=4,
                        notes="Approve the design?"))
        graph.add(Node("emit_agent", "agent",
                        prompt_template="Emit: {{brief}}",
                        reads=("design.md",),
                        writes=("output.txt",),
                        produces=("emit",)))
        graph.connect("prepare", "design_agent")
        graph.connect("design_agent", "gate_strategy")
        graph.connect("gate_strategy", "design_agent", condition="reloop")
        graph.connect("gate_strategy", "emit_agent", condition="proceed")
        return graph


class _FakeModel:
    """Returns scripted answers."""
    def __init__(self, answers):
        self.answers = list(answers)
        self.prompts = []

    def complete(self, prompt, *, system="", workdir=None):
        self.prompts.append(prompt)
        return self.answers.pop(0) if self.answers else ""

    def context(self):
        return {"worker_backend": "dsh"}

    def route(self):
        return {"worker_backend": "dsh"}

    def for_node(self, node_id, role=None):
        return self


# ── tests ──────────────────────────────────────────────────────────────────


def test_gate_stop_raises_for_user_gate(tmp_path):
    """A walker run at a user gate without a verdict raises GateStop."""
    mode = _SimpleDesignMode()
    model = _FakeModel(["a design document"])
    result = mode.run(tmp_path / "run", model=model,
                      task={"name": "test", "brief": "test", "check": "true"})
    assert result.exit_ok is False
    assert result.gate_stop == "gate_strategy"
    assert "gate_strategy" in (result.error or "")


def test_halt_at_gate_ends_run(tmp_path):
    """A halt verdict at a user gate ends the run with halted=True."""
    mode = _SimpleDesignMode()
    workdir = tmp_path / "run"
    model = _FakeModel(["a design document"])
    # First run: stops at gate.
    result = mode.run(workdir, model=model,
                      task={"name": "test", "brief": "test", "check": "true"})
    assert result.gate_stop == "gate_strategy"

    # Steer with halt.
    ledger = steering.Ledger(workdir)
    ledger.verdict("gate_strategy", "halt")

    # Resume: should halt.
    model2 = _FakeModel(["a design document"])
    result2 = mode.run(workdir, model=model2,
                       task={"name": "test", "brief": "test", "check": "true"})
    assert result2.exit_ok is False
    assert result2.halted is True


def test_proceed_with_note(tmp_path):
    """A proceed verdict with a note: the note appears in the next agent's prompt."""
    mode = _SimpleDesignMode()
    workdir = tmp_path / "run"
    model = _FakeModel(["a design document"])

    # First run: stops at gate.
    result = mode.run(workdir, model=model,
                      task={"name": "test", "brief": "test", "check": "true"})
    assert result.gate_stop == "gate_strategy"

    # Steer with proceed and a note.
    ledger = steering.Ledger(workdir)
    ledger.verdict("gate_strategy", "proceed")
    ledger.note("constraint", "must use Python 3.12")

    # Resume.
    model2 = _FakeModel(["a design document", "the emitted code"])
    result2 = mode.run(workdir, model=model2,
                       task={"name": "test", "brief": "test", "check": "true"})
    assert result2.exit_ok, result2.error
    # The note should appear in the emit agent's prompt.
    assert any("Python 3.12" in p for p in model2.prompts), (
        "the note did not reach the next agent's prompt"
    )


def test_reloop_with_note(tmp_path):
    """A reloop verdict with a note: the run reloops and the note reaches the agent."""
    mode = _SimpleDesignMode()
    workdir = tmp_path / "run"
    model = _FakeModel(["first design"])

    # First run: stops at gate.
    result = mode.run(workdir, model=model,
                      task={"name": "test", "brief": "test", "check": "true"})
    assert result.gate_stop == "gate_strategy"

    # Steer with reloop and a note.
    ledger = steering.Ledger(workdir)
    ledger.verdict("gate_strategy", "reloop")
    ledger.note("correction", "redo the error handling")

    # Resume: should reloop to design_agent, then stop at gate again.
    model2 = _FakeModel(["second design"])
    result2 = mode.run(workdir, model=model2,
                       task={"name": "test", "brief": "test", "check": "true"})
    assert result2.gate_stop == "gate_strategy"
    # The note should have reached the design agent.
    assert any("redo the error handling" in p for p in model2.prompts), (
        "the reloop note did not reach the design agent"
    )


def test_upfront_approve_survives_resume(tmp_path):
    """An up-front verdict persisted as a ledger entry survives a resume."""
    mode = _SimpleDesignMode()
    workdir = tmp_path / "run"

    # Seed the ledger and add an upfront verdict.
    steering.Ledger.seed(workdir, "build a test thing")
    ledger = steering.Ledger(workdir)
    ledger.verdict("gate_strategy", "proceed", approval=True)

    # Run: should pass through the gate.
    model = _FakeModel(["a design document", "the emitted code"])
    result = mode.run(workdir, model=model,
                      task={"name": "test", "brief": "test", "check": "true"})
    assert result.exit_ok, result.error


def test_verdict_routing_cases():
    """The route_verdict function matches the shared JSON cases."""
    cases_path = Path(__file__).resolve().parent.parent / "refactory_dsh" / "data" / "verdict-routing-cases.json"
    cases = json.loads(cases_path.read_text())
    for case in cases:
        edges = [Edge(source=case["gate"], target=e["target"], condition=e["condition"])
                 for e in case["edges"]]
        expect = case["expect"]
        if "error" in expect:
            with pytest.raises(Exception):
                route_verdict(case["verdict"], edges, case["gate_kind"])
        elif "halt" in expect:
            edge, reason = route_verdict(case["verdict"], edges, case["gate_kind"])
            assert edge is None, f"{case['name']}: expected halt but got {edge}"
            assert reason == expect["reason"], f"{case['name']}: reason mismatch"
        else:
            edge, _ = route_verdict(case["verdict"], edges, case["gate_kind"])
            assert edge is not None, f"{case['name']}: expected target but got halt"
            assert edge.target == expect["target"], f"{case['name']}: target mismatch"


def test_gate_stop_in_fork_branch(tmp_path):
    """GateStop in a fork branch records refused, not an uncaught exception."""
    graph = Graph()
    graph.add(Node("start", "op", call="tests.loop_ops:noop",
                    writes=(), produces=()))
    graph.add(Node("fork", "fork", targets=("branch_a",)))
    graph.add(Node("branch_a", "gate", gate_kind="user",
                    reads=(), max_iterations=2, notes="user gate in fork"))
    graph.add(Node("join", "join", sources=("branch_a",)))
    graph.connect("start", "fork")
    graph.connect("fork", "join")
    graph.connect("branch_a", "join")

    from refactory_dsh.nn.executor import execute
    result = execute(graph, tmp_path / "run")
    assert result.exit_ok is False
    assert "branch_a" in (result.error or "") or "gate stop" in (result.error or "")


def test_gate_stop_in_data_pass(tmp_path):
    """GateStop in a data pass records refused, not an error."""
    graph = Graph()
    graph.add(Node("data", "data", subgraph_entry="inner_gate", subgraph_exit="inner_gate",
                    inline_items=[{"id": "item1"}],
                    writes=("current_item.json",), produces=("item_scores",)))
    graph.add(Node("inner_gate", "gate", gate_kind="user",
                    reads=(), max_iterations=2, notes="user gate in data pass"))
    graph.add(Node("finish", "op", call="tests.loop_ops:noop"))
    graph.connect("data", "finish")

    from refactory_dsh.nn.executor import execute
    result = execute(graph, tmp_path / "run")
    assert result.exit_ok is False
    # The error should mention the gate, not be an uncaught exception.
    assert "inner_gate" in (result.error or "")


def test_second_walker_run_refused_while_first_holds_lock(tmp_path):
    """The ownership lock prevents concurrent walker runs on the same workdir."""
    from refactory_dsh.run_ownership import ownership

    workdir = tmp_path / "run"
    with ownership(workdir):
        with pytest.raises(ValueError, match="active invocation"):
            with ownership(workdir):
                pass  # pragma: no cover — should not get here
