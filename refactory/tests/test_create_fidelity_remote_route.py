"""Controlled command transport and native reference regressions, no model I/O."""
from __future__ import annotations

import copy
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from benchmarks.create_fidelity.contract import reference
from benchmarks.create_fidelity.remote_route import (
    COMMANDS, LEGACY_ROUTE, SHELL_ROUTE, SEGMENT_ROUTE, SOURCE_GRAPH_SHA256,
    configure, graph_digest, halt_continuation, execute_segmented,
)


class FnNode:
    def __init__(self, state):
        self.state = state
        self.command = state.get("command")
        self.evaluator_type = state.get("evaluator_type")

    def model_copy(self, *, update):
        return FnNode({**self.state, **update})


class ControlledWorkflow:
    def __init__(self, graph):
        self.graph = copy.deepcopy(graph)
        self.nodes = {key: FnNode(value) for key, value in self.graph["nodes"].items()}
        self.edges = [SimpleNamespace(**row) for row in self.graph["edges"]]
        self.start_node = self.graph["start_node"]

    def to_dict(self):
        return {**self.graph, "nodes": {key: value.state for key, value in self.nodes.items()}}

    def model_copy(self, *, deep, update=None):
        assert deep
        return ControlledWorkflow({**self.to_dict(), **(update or {})})


def test_explicit_shell_route_changes_only_exact_pinned_commands_and_preserves_original():
    original = reference()["graph"]
    assert graph_digest(original) == SOURCE_GRAPH_SHA256
    workflow = ControlledWorkflow(original)
    mapped, receipt = configure(workflow, SHELL_ROUTE)
    assert workflow.to_dict() == original
    expected = copy.deepcopy(original)
    assert {row["node_id"] for row in receipt["commands"]} == set(COMMANDS)
    for row in receipt["commands"]:
        assert row["original"] == COMMANDS[row["node_id"]]
        expected["nodes"][row["node_id"]]["command"] = row["effective"]
    assert mapped.to_dict() == expected
    assert receipt["original_graph"] == original
    assert receipt["effective_graph"] == expected
    assert receipt["execution_correction"]
    assert receipt["effective_graph_sha256"] == graph_digest(expected)
    assert len(expected["nodes"]) == 29 and len(expected["edges"]) == 33


def test_legacy_route_keeps_native_python_failure_configuration_distinct():
    original = reference()["graph"]
    mapped, receipt = configure(ControlledWorkflow(original), LEGACY_ROUTE)
    assert mapped.to_dict() == original
    assert not receipt["execution_correction"]
    assert receipt["commands"] == []
    assert receipt["original_graph_sha256"] == receipt["effective_graph_sha256"]


@pytest.mark.parametrize("change", ["command", "gate", "timeout", "reads", "edge"])
def test_shell_mapping_fails_closed_for_changed_source_graph(change):
    graph = copy.deepcopy(reference()["graph"])
    if change == "command":
        graph["nodes"]["init_user_intent"]["command"] += " extra"
    elif change == "gate":
        graph["nodes"]["gate_strategy"]["evaluator_type"] = "agent"
    elif change == "timeout":
        graph["nodes"]["research_director"]["timeout"] = 10
    elif change == "reads":
        graph["nodes"]["builder"]["reads"] = []
    else:
        graph["edges"].pop()
    with pytest.raises(ValueError, match="exact pinned"):
        configure(ControlledWorkflow(graph), SHELL_ROUTE)


def test_unknown_remote_route_is_rejected():
    with pytest.raises(ValueError, match="Unsupported remote"):
        configure(ControlledWorkflow(reference()["graph"]), "silently-fixed")


def native_halt(gate, reason=None):
    if reason is None:
        reason = (f"gate '{gate}' returned unparseable verdict "
                  '(expected pass | fail | revert | reloop | {"passed": bool}): HALT')
    return SimpleNamespace(success=False, halted=True, halt_reason=reason, completed_files=set(),
        node_outputs={gate: "halt"}, nodes_executed=1, duration_ms=1.0,
        events=[{"type": "gate.verdict", "node_id": gate, "verdict_type": "halt", "reason": reason}])


@pytest.mark.parametrize("gate", ["gate_has_factory", "gate_factory_md_exists", "gate_precheck"])
def test_only_declared_native_halt_boundaries_continue(gate):
    graph, _ = configure(ControlledWorkflow(reference()["graph"]), SEGMENT_ROUTE)
    result = native_halt(gate)
    continuation = halt_continuation(graph, result)
    assert continuation["gate_id"] == gate
    assert continuation["terminal_failure"] == (gate == "gate_precheck")


@pytest.mark.parametrize("gate,reason", [
    ("gate_has_factory", "gate command failed: HALT stdout, exit 1"),
    ("gate_precheck", "gate command failed: factory precheck"),
    ("gate_has_factory", "HALT"),
    ("gate_has_factory", "unexpected message HALT"),
    ("gate_strategy", None), ("gate_build", None), ("gate_qa", None),
])
def test_command_failure_unexpected_or_real_human_ai_halt_never_continues(gate, reason):
    graph, _ = configure(ControlledWorkflow(reference()["graph"]), SEGMENT_ROUTE)
    assert halt_continuation(graph, native_halt(gate, reason)) is None


def test_changed_boundary_edge_never_authorizes_continuation():
    graph, _ = configure(ControlledWorkflow(reference()["graph"]), SEGMENT_ROUTE)
    edge = next(e for e in graph.edges if e.source == "gate_has_factory" and e.condition == "halt")
    edge.target = "builder"
    assert halt_continuation(graph, native_halt("gate_has_factory")) is None


def test_precheck_diagnostic_archive_success_remains_overall_failure(tmp_path):
    graph, _ = configure(ControlledWorkflow(reference()["graph"]), SEGMENT_ROUTE)
    calls, records = [], []

    class Executor:
        completed_files = set()

        def __init__(self, workflow):
            self.workflow = workflow

        async def execute(self):
            calls.append(self.workflow.start_node)
            if len(calls) == 1:
                return native_halt("gate_precheck", "precheck failed: ['controlled guard failure']")
            return SimpleNamespace(success=True, halted=False, halt_reason="", completed_files=set(),
                node_outputs={"archivist_build":"Actual controlled archive result"}, nodes_executed=1,
                duration_ms=1.0, events=[{"type":"node.completed", "node_id":"archivist_build"}])

    result = asyncio.run(execute_segmented(graph, tmp_path, Executor, timeout_s=10,
        record=lambda index, value: records.append(value)))
    assert calls == ["init_user_intent", "archivist_build"]
    assert not result["success"] and result["halted"]
    assert result["diagnostic_archive_after_precheck_failure"]
    assert "controlled guard failure" in result["error"]
    settled = [r for r in records if r["status"] == "settled"]
    assert len(settled) == 2 and settled[0]["native_result"]["halted"]
    assert settled[1]["native_result"]["success"]


def test_changed_prior_output_stops_segment_state_restoration(tmp_path):
    graph, _ = configure(ControlledWorkflow(reference()["graph"]), SEGMENT_ROUTE)
    path = tmp_path / "actual-producer-output.txt"
    path.write_text("actual prior native producer output")
    records = []

    class Executor:
        def __init__(self, graph):
            self.completed_files = set()

        async def execute(self):
            result = native_halt("gate_has_factory")
            result.completed_files = {path.name}
            return result

    def record(index, value):
        records.append(value)
        if index == 1:
            path.write_text("changed after input verification")

    # The actual supplied artifact changes after prior result hashes were bound.
    with pytest.raises(ValueError, match="output changed"):
        asyncio.run(execute_segmented(graph, tmp_path, Executor, timeout_s=10, record=record))


def test_controller_deadline_cancels_wait_and_records_missing_native_result(tmp_path):
    graph, _ = configure(ControlledWorkflow(reference()["graph"]), SEGMENT_ROUTE)
    records = []

    class Executor:
        def __init__(self, graph):
            self.completed_files = set()

        async def execute(self):
            await asyncio.Event().wait()

    with pytest.raises(TimeoutError):
        asyncio.run(execute_segmented(graph, tmp_path, Executor, timeout_s=0.02,
            record=lambda index, value: records.append(value)))
    assert records[-1]["status"] == "interrupted"
    assert records[-1]["native_result"] is None


def test_repeated_boundary_never_forms_an_implicit_controller_retry(tmp_path):
    graph, _ = configure(ControlledWorkflow(reference()["graph"]), SEGMENT_ROUTE)

    class Executor:
        def __init__(self, graph):
            self.completed_files = set()

        async def execute(self):
            return native_halt("gate_has_factory")

    with pytest.raises(ValueError, match="boundary repeated"):
        asyncio.run(execute_segmented(graph, tmp_path, Executor, timeout_s=10, record=lambda *_:None))


@pytest.mark.parametrize("directory", ["ordinary-project", "project with 'single' and \"double\" $dollar `tick`"])
def test_actual_pinned_executor_shell_transport_and_unrepaired_bootstrap_halt(tmp_path, directory):
    """Optional real source integration; configure explicit paths to exercise it.

    Missing external source remains skipped, never replaced by a passing fake.
    The executor performs its real subprocesses; no remote source is modified.
    """
    source = os.environ.get("CREATE_FIDELITY_REFERENCE")
    if not source:
        pytest.skip("Set CREATE_FIDELITY_REFERENCE to the exact pinned reference checkout")
    python = os.environ.get("CREATE_FIDELITY_REFERENCE_PYTHON", sys.executable)
    project = tmp_path / directory
    project.mkdir()
    adapter = Path(__file__).resolve().parents[1]
    code = r'''
import asyncio, hashlib, json, os, sys
from pathlib import Path
from factory.workflow.contributed.create_v2.workflow import workflow
from factory.workflow.executor import WorkflowExecutor
from factory.workflow.primitives import Workflow, FnNode, Edge
from benchmarks.create_fidelity.remote_route import configure, SHELL_ROUTE, halt_continuation
source, project = map(Path, sys.argv[1:])
tracked = [source/'factory/workflow/executor.py', source/'factory/workflow/contributed/create_v2/workflow.py']
before = [hashlib.sha256(p.read_bytes()).hexdigest() for p in tracked]
assert before == ['90bca0dc0021716fb044f7dbfedf1371c338de3b105527e95171fb4e9f1d686b',
                  '5bf4080484f60c10969a37fafc5cd445907f8d88e2173c5a83443907143eb183']
original = workflow()
mapped, receipt = configure(original, SHELL_ROUTE)
os.environ['FACTORY_IDEA'] = 'Controlled transport fixture, no booking deliverable'
async def run():
    old = Workflow(name='legacy-control', nodes={'init_user_intent':original.nodes['init_user_intent']}, edges=[], start_node='init_user_intent')
    old_result = await WorkflowExecutor(old, project).execute()
    assert not old_result.success and not (project/'.factory/strategy/user-intent.md').exists()
    for node_id in ('init_user_intent', 'synthesize_qa'):
        if node_id == 'synthesize_qa':
            reviews = project/'.factory/reviews'; reviews.mkdir(parents=True)
            (reviews/'adversarial-transport-latest.md').write_text('# Controlled report\n- ERROR: controlled fixture defect\n')
            (reviews/'health-check.md').write_text('# Controlled health input\nTransport fixture only.\n')
            (reviews/'code-review.md').write_text('# Controlled code input\nTransport fixture only.\n')
        nodes = {node_id:mapped.nodes[node_id]}; edges = []; start = node_id
        if node_id == 'synthesize_qa':
            # Admit real supplied test inputs through an actual native producer,
            # so the executor's completed-files barrier remains exercised.
            nodes['controlled_inputs'] = FnNode(id='controlled_inputs', command='true',
                writes={'.factory/reviews/health-check.md', '.factory/reviews/code-review.md'})
            edges = [Edge(source='controlled_inputs', target=node_id)]; start = 'controlled_inputs'
        control = Workflow(name='shell-control-'+node_id, nodes=nodes, edges=edges, start_node=start)
        result = await WorkflowExecutor(control, project).execute()
        assert result.success, result.halt_reason
    assert 'Controlled transport fixture' in (project/'.factory/strategy/user-intent.md').read_text()
    assert 'controlled fixture defect' in (project/'.factory/reviews/qa-synthesized.md').read_text()
    # The command correction intentionally does not repair source gate semantics.
    result = await WorkflowExecutor(mapped, project).execute()
    assert result.halted and not result.success
    assert 'gate_has_factory' in result.node_outputs and 'discover' not in result.node_outputs
    assert 'unparseable verdict' in result.halt_reason or 'gate command failed' in result.halt_reason
    if 'gate command failed' in result.halt_reason:
        assert halt_continuation(mapped, result) is None
    else:
        assert halt_continuation(mapped, result)['target'] == 'discover'
    # Actual pinned evaluator/parser negatives, in explicit controlled graphs.
    # The commands are test probes, never accepted source execution mappings.
    import shlex
    for probe in ('import sys; print("HALT"); sys.exit(1)', 'print("HALT unexpected")'):
        gate = mapped.nodes['gate_has_factory'].model_copy(update={
            'evaluator_command':'env python3 -c '+shlex.quote(probe), 'reads':set()})
        nodes = {'gate_has_factory':gate, 'discover':FnNode(id='discover',command='true')}
        control = Workflow(name='native-negative',nodes=nodes,
            edges=[Edge(source='gate_has_factory',target='discover',condition='proceed')],start_node='gate_has_factory')
        negative = await WorkflowExecutor(control,project).execute()
        assert negative.halted and halt_continuation(mapped,negative) is None
    gate = mapped.nodes['gate_strategy'].model_copy(update={'reads':set()})
    control = Workflow(name='native-human-halt',nodes={'gate_strategy':gate,'builder':FnNode(id='builder',command='true')},
        edges=[Edge(source='gate_strategy',target='builder',condition='proceed')],start_node='gate_strategy')
    negative = await WorkflowExecutor(control,project,input_fn=lambda _: 'halt',auto_approve=False).execute()
    assert negative.halted and halt_continuation(mapped,negative) is None
    import factory.agents.runner as runner
    original_invoke = runner.invoke_agent
    async def controlled_external_model(*args,**kwargs):
        return 'HALT reason="controlled real model-boundary output"',0
    runner.invoke_agent = controlled_external_model
    try:
        gate = mapped.nodes['gate_build'].model_copy(update={'reads':set()})
        control = Workflow(name='native-ai-halt',nodes={'gate_build':gate,'fork_qa':FnNode(id='fork_qa',command='true')},
            edges=[Edge(source='gate_build',target='fork_qa',condition='proceed')],start_node='gate_build')
        negative = await WorkflowExecutor(control,project).execute()
        assert negative.halted and halt_continuation(mapped,negative) is None
    finally:
        runner.invoke_agent = original_invoke
    print(json.dumps({'transport':'passed','empty_project_bootstrap':'source gate HALT defect retained',
                      'halt_reason':result.halt_reason,'execution':receipt}))
asyncio.run(run())
assert before == [hashlib.sha256(p.read_bytes()).hexdigest() for p in tracked]
'''
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1",
           "PYTHONPATH": os.pathsep.join((str(Path(source)), str(adapter))),
           "PATH": str(Path(python).parent) + os.pathsep + os.environ.get("PATH", "")}
    result = subprocess.run([python, "-B", "-c", code, source, str(project)], cwd=project,
                            env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert '"transport": "passed"' in result.stdout
