"""Lowering a mode state to refactory's IR, and the runner that executes a node.

The DSH issue: the IR is the seam between a mode's state dict and the runtime that
walks it. These are the fast checks; the end-to-end one is the equivalence test.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from refactory_dsh import lowering, registry
from refactory_dsh.nn import Graph, Node

MODE = "circle_packing/adaptive"


def _state() -> dict:
    _package, mode_class = registry.resolve(Path("."), MODE)
    return mode_class().state_dict()


# ── the graph ──────────────────────────────────────────────────────────────
def test_a_mode_lowers_to_an_ir_refactory_accepts():
    workflow = lowering.to_workflow(_state(), name=MODE)
    assert lowering.validate_ir(workflow) == [], "the language's own validator refused it"


def test_every_node_survives_with_its_declarations():
    state = _state()
    workflow = lowering.to_workflow(state, name=MODE)
    graph = Graph.from_state(state["graph"])
    assert sorted(workflow.nodes) == sorted(graph.nodes)
    for node_id, node in graph.nodes.items():
        lowered = workflow.nodes[node_id]
        assert set(lowered.reads) == set(node.reads)
        assert set(lowered.writes) == set(node.writes)


def test_the_kinds_map_to_the_ir_vocabulary():
    workflow = lowering.to_workflow(_state(), name=MODE)
    assert type(workflow.nodes["generate"]).__name__ == "FnNode"
    assert type(workflow.nodes["budget_check"]).__name__ == "GateNode"
    assert workflow.nodes["budget_check"].evaluator_type == "fn"
    assert workflow.start_node == "task_input"


def test_an_op_runs_through_the_runner_and_not_a_baked_path():
    workflow = lowering.to_workflow(_state(), name=MODE)
    command = workflow.nodes["generate"].command
    assert command.startswith(lowering.RUNNER)
    assert "refactory_packages.circle_packing.ops:generate" in command
    assert "/" not in command.split(lowering.RUNNER)[1], (
        "the command must not bake an absolute path; --python-bin supplies the PATH"
    )


def test_a_gate_carries_its_iteration_bound():
    workflow = lowering.to_workflow(_state(), name=MODE)
    assert workflow.nodes["budget_check"].max_iterations == 64


def test_the_ir_round_trips_through_json():
    """What the science bundle actually does: write a file, load it back."""
    workflow = lowering.to_workflow(_state(), name=MODE)
    payload = json.loads(json.dumps(workflow.to_dict(), default=str))
    again = type(workflow).from_dict(payload)
    assert sorted(again.nodes) == sorted(workflow.nodes)
    assert again.start_node == workflow.start_node


def test_an_agent_node_lowers_to_the_ir_agent_node():
    class WithAgent(lowering.Graph.__class__ if False else object):  # noqa: SIM108
        pass

    graph = Graph()
    graph.add(Node("ask", "agent", prompt_template="say hi",
                   writes=("answer.txt",), produces=("answer",)))
    state = {"version": 1, "graph": graph.to_state(), "knobs": {}, "ports": [], "memory": []}
    workflow = lowering.to_workflow(state, name="agent-mode")
    lowered = workflow.nodes["ask"]
    assert type(lowered).__name__ == "AgentNode"
    assert lowered.prompt_template == "say hi"
    assert lowered.role.value == "ceo"


def test_an_unknown_agent_role_is_refused_with_the_known_ones():
    graph = Graph()
    graph.add(Node("ask", "agent", prompt_template="hi", role="wizard"))
    state = {"version": 1, "graph": graph.to_state(), "knobs": {}, "ports": [], "memory": []}
    with pytest.raises(lowering.LoweringError, match="wizard"):
        lowering.to_workflow(state, name="bad-role")


# ── knobs, declarations, files ─────────────────────────────────────────────
def test_knobs_travel_in_the_command_environment_with_their_types():
    state = _state()
    env = lowering.knob_environment(state)
    values = json.loads(env[lowering.KNOBS_ENV])
    assert values["grid"] == 10
    assert values["order"] == "desc"
    assert isinstance(values["grid"], int), "a numeric knob must stay numeric"


def test_extra_environment_is_merged():
    env = lowering.knob_environment(_state(), extra={"REFACTORY_WORKDIR": "/tmp/x"})
    assert env["REFACTORY_WORKDIR"] == "/tmp/x"
    assert lowering.KNOBS_ENV in env


def test_the_ir_records_the_knob_values_it_cannot_type():
    """The IR's field is `str | float` and strict, so bools are projected."""
    state = _state()
    workflow = lowering.to_workflow(state, name=MODE)
    assert isinstance(workflow.knob_values["grid"], float)
    assert workflow.knob_values["order"] == "desc"
    assert set(workflow.knob_specs) == set(state["knobs"])


def test_declarations_are_written_for_the_runner(tmp_path):
    state = _state()
    path = lowering.write_declarations(state, tmp_path)
    assert path.exists()
    loaded = lowering.read_declarations(tmp_path)
    assert loaded["generate"]["writes"] == ["candidate.json"]
    assert loaded["exit"]["produces"], "the facts a node produces must reach the runner"


def test_the_manifest_mirrors_what_the_spine_passes():
    state = _state()
    manifest = lowering.node_manifest(state, "budget_check")
    assert manifest["type"] == "GateNode"
    assert manifest["reads"] == ["budget_state.json"]


def test_the_graph_is_written_where_the_bundle_loads_it(tmp_path):
    path, workflow = lowering.write_mode_graph(_state(), tmp_path, name="circle_packing")
    assert path.name == "circle_packing.graph.json"
    payload = json.loads(path.read_text())
    assert payload["start_node"] == "task_input"
    assert payload["nodes"]["generate"]["_type"] == "FnNode"


def test_a_seed_state_with_no_ports_lowers_without_them():
    state = _state()
    assert state["ports"] == []
    assert lowering.to_workflow(state, name=MODE).name == MODE
