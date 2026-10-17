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

from reference_ir import needs_factory, validate_reference

MODE = "circle_packing/adaptive"


def _state() -> dict:
    _package, mode_class = registry.resolve(Path("."), MODE)
    return mode_class().state_dict()


# ── the graph ──────────────────────────────────────────────────────────────
@pytest.mark.toolchain("factory")
@needs_factory
def test_a_mode_lowers_to_an_ir_refactory_accepts():
    workflow = lowering.to_workflow(_state(), name=MODE)
    assert validate_reference(workflow) == [], "the reference validator refused it"


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


# ── check envelope ─────────────────────────────────────────────────────────
#
# The check step runs dependency installs, setup, generate and the check
# itself.  Its spine envelope must cover all of them, or the spine kills it.


def _check_gate_state(*, check_timeout_s: int = 180) -> dict:
    """A minimal state with one fn gate whose produces include repo_change's check facts."""
    from refactory_dsh.repo_change import CHECK_PRODUCES, CHECK_WRITES

    graph = Graph()
    graph.add(Node(
        "check", "gate", evaluator="somepackage.ops:check_artifact",
        knobs=("check_timeout_s",),
        writes=("check.txt", *CHECK_WRITES),
        produces=("validation", *CHECK_PRODUCES),
        max_iterations=4,
    ))
    return {
        "version": 1,
        "graph": graph.to_state(),
        "knobs": {"check_timeout_s": {"value": check_timeout_s, "spec": {"default": 180}}},
        "ports": [],
        "memory": [],
    }


def _check_op_state(*, check_timeout_s: int = 60) -> dict:
    """A minimal state with one op whose produces include repo_change's check facts."""
    from refactory_dsh.repo_change import CHECK_PRODUCES, CHECK_WRITES

    graph = Graph()
    graph.add(Node(
        "focused_check", "op", call="somepackage.ops:focused_check",
        knobs=("check_timeout_s",),
        writes=("focused-check.json", *CHECK_WRITES),
        produces=("focused_check", *CHECK_PRODUCES),
    ))
    return {
        "version": 1,
        "graph": graph.to_state(),
        "knobs": {"check_timeout_s": {"value": check_timeout_s, "spec": {"default": 60}}},
        "ports": [],
        "memory": [],
    }


def _fix_pack_state(*, check_timeout_s: int = 300) -> dict:
    """A minimal state with fix_pack's check gate that produces repo_change's check facts."""
    from refactory_dsh.repo_change import CHECK_PRODUCES, CHECK_WRITES

    graph = Graph()
    graph.add(Node(
        "check", "gate", evaluator="refactory_packages.fix_pack.ops:check",
        knobs=("attempts", "check_timeout_s"),
        writes=("feedback.txt", *CHECK_WRITES),
        produces=("feedback", "attempts_used", *CHECK_PRODUCES),
        max_iterations=5,
    ))
    return {
        "version": 1,
        "graph": graph.to_state(),
        "knobs": {
            "attempts": {"value": 3, "spec": {"default": 3}},
            "check_timeout_s": {"value": check_timeout_s, "spec": {"default": 300}},
        },
        "ports": [],
        "memory": [],
    }


def test_check_gate_envelope_grows_with_check_timeout_s():
    from refactory_dsh.worktree import INSTALL_TIMEOUT_S
    from refactory_dsh.model import NODE_DISPATCH_OVERHEAD_S

    for timeout in (180, 600, 1500):
        state = _check_gate_state(check_timeout_s=timeout)
        workflow = lowering.to_workflow(state, name="design")
        lowered = workflow.nodes["check"]
        expected = 3 * INSTALL_TIMEOUT_S + 3 * timeout + NODE_DISPATCH_OVERHEAD_S
        assert lowered.command_timeout_s == expected, (
            f"check_timeout_s={timeout}: expected {expected}, got {lowered.command_timeout_s}"
        )
        assert lowered.command_timeout_s > timeout, (
            "the envelope must exceed the check's own budget"
        )

    # Increasing check_timeout_s must increase command_timeout_s.
    w1 = lowering.to_workflow(_check_gate_state(check_timeout_s=180), name="d")
    w2 = lowering.to_workflow(_check_gate_state(check_timeout_s=600), name="d")
    assert w2.nodes["check"].command_timeout_s > w1.nodes["check"].command_timeout_s


def test_focused_check_op_gets_check_envelope():
    from refactory_dsh.worktree import INSTALL_TIMEOUT_S
    from refactory_dsh.model import NODE_DISPATCH_OVERHEAD_S

    timeout = 60
    state = _check_op_state(check_timeout_s=timeout)
    workflow = lowering.to_workflow(state, name="design_fast")
    lowered = workflow.nodes["focused_check"]
    expected = 3 * INSTALL_TIMEOUT_S + 3 * timeout + NODE_DISPATCH_OVERHEAD_S
    assert lowered.command_timeout_s == expected
    assert lowered.command_timeout_s > timeout


def test_fix_pack_check_gate_gets_check_envelope():
    from refactory_dsh.worktree import INSTALL_TIMEOUT_S
    from refactory_dsh.model import NODE_DISPATCH_OVERHEAD_S

    timeout = 300
    state = _fix_pack_state(check_timeout_s=timeout)
    workflow = lowering.to_workflow(state, name="fix_pack")
    lowered = workflow.nodes["check"]
    expected = 3 * INSTALL_TIMEOUT_S + 3 * timeout + NODE_DISPATCH_OVERHEAD_S
    assert lowered.command_timeout_s == expected
    assert lowered.command_timeout_s > timeout


def test_non_check_gate_keeps_default_timeout():
    workflow = lowering.to_workflow(_state(), name=MODE)
    assert workflow.nodes["budget_check"].command_timeout_s is None


def test_check_timeout_s_out_of_range_is_refused():
    for bad_value in (0, 100000):
        state = _check_gate_state(check_timeout_s=bad_value)
        with pytest.raises(lowering.LoweringError, match="check_timeout_s must be between 1 and 86400"):
            lowering.to_workflow(state, name="design")


def test_op_without_check_produces_keeps_default_timeout():
    """An op whose produces do not include CHECK_PRODUCES keeps command_timeout_s=None."""
    graph = Graph()
    graph.add(Node("work", "op", call="refactory_dsh.fast_ops:focused_check",
                    produces=("focused_check", "output_path"),
                    writes=("focused-check.json",)))
    state = {
        "version": 1,
        "graph": graph.to_state(),
        "knobs": {"check_timeout_s": {"value": 60, "spec": {"default": 60}}},
        "ports": [],
        "memory": [],
    }
    wf = lowering.to_workflow(state, name="fast_no_artifact")
    assert wf.nodes["work"].command_timeout_s is None, (
        "an op without CHECK_PRODUCES should not get the check envelope"
    )


# ── fidelity to refactory's IR ─────────────────────────────────────────────
#
# Issue #21 said not to invent a command string, because `FnNode.callable_name`
# already exists. It does exist — in refactory's own `FnNode`
# (`factory/workflow/primitives.py`). What the issue did not know is that DSH's
# spine does not model it: its `WorkflowNode` has `command` and no
# `callable_name`, and it executes the command. So a graph carrying only
# `callable_name` is a graph whose nodes never run. Both fields, and these hold it.


def test_an_op_node_names_its_callable_as_well_as_its_command(tmp_path):
    """The IR keeps the language's own field; the runtime keeps the one it reads."""
    from pathlib import Path as _Path
    from refactory_dsh import registry

    mode = registry.resolve(_Path("."), "circle_packing/adaptive")[1]()
    path, _workflow = lowering.write_mode_graph(
        mode.state_dict(), tmp_path, name="circle_packing"
    )
    written = json.loads(path.read_text())
    ops = [node for node in written["nodes"].values() if node["_type"] == "FnNode"]
    assert ops, "the mode lowered to no op nodes"
    for node in ops:
        assert node.get("callable_name"), (
            f"FnNode {node['id']} drops refactory's own way of naming what it runs"
        )
        assert node.get("command"), (
            f"FnNode {node['id']} has no command, and DSH's spine executes the "
            "command — this node would never run"
        )


def test_every_declared_callable_resolves(tmp_path):
    """`callable_name` is a claim a reader will try to import."""
    from pathlib import Path as _Path
    from refactory_dsh import registry
    from refactory_dsh.nn.resolve import resolve as resolve_target

    mode = registry.resolve(_Path("."), "circle_packing/adaptive")[1]()
    path, _workflow = lowering.write_mode_graph(
        mode.state_dict(), tmp_path, name="circle_packing"
    )
    for node in json.loads(path.read_text())["nodes"].values():
        target = node.get("callable_name")
        if target:
            assert callable(resolve_target(target)), f"{target} does not resolve"
