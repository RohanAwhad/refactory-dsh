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
    COMMANDS, LEGACY_ROUTE, SHELL_ROUTE, LEGACY_SEGMENT_ROUTE, SEGMENT_ROUTE, SOURCE_GRAPH_SHA256,
    configure, graph_digest, halt_continuation, execute_segmented, reachable_segment,
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
        return {**self.graph, "nodes": {key: value.state for key, value in self.nodes.items()},
                "edges": [vars(edge) for edge in self.edges]}

    def model_copy(self, *, deep, update=None):
        assert deep
        update = dict(update or {})
        if "nodes" in update:
            update["nodes"] = {key: value.state for key, value in update["nodes"].items()}
        if "edges" in update:
            update["edges"] = [vars(edge) for edge in update["edges"]]
        if "runtime_inputs" in update:
            update["runtime_inputs"] = sorted(update["runtime_inputs"])
        result = {**self.to_dict(), **update}
        if not result.get("runtime_inputs"):
            result.pop("runtime_inputs", None)
        return ControlledWorkflow(result)

    def subgraph(self, node_ids, *, name, start_node):
        graph = self.to_dict()
        return ControlledWorkflow({**graph, "name": name, "start_node": start_node,
            "nodes": {key: value for key, value in graph["nodes"].items() if key in node_ids},
            "edges": [edge for edge in graph["edges"] if edge["source"] in node_ids and edge["target"] in node_ids]})


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


@pytest.mark.parametrize("start,excluded", [
    ("init_user_intent", set()),
    ("discover", {"init_user_intent", "gate_has_factory"}),
    ("create_factory_md", {"init_user_intent", "gate_has_factory", "discover", "gate_factory_md_exists"}),
    ("archivist_build", set(reference()["graph"]["nodes"]) - {"archivist_build"}),
])
def test_reachable_segments_preserve_all_retained_source_fields_edges_and_metadata(start, excluded):
    full, _ = configure(ControlledWorkflow(reference()["graph"]), SEGMENT_ROUTE)
    original = full.to_dict()
    selected = reachable_segment(full, start).to_dict()
    assert full.to_dict() == original
    assert set(selected["nodes"]) == set(original["nodes"]) - excluded
    assert selected["nodes"] == {key: value for key, value in original["nodes"].items() if key not in excluded}
    assert selected["edges"] == [edge for edge in original["edges"]
                                 if edge["source"] not in excluded and edge["target"] not in excluded]
    assert {k: v for k, v in selected.items() if k not in {"nodes", "edges", "start_node"}} == {
        k: v for k, v in original.items() if k not in {"nodes", "edges", "start_node"}}


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


def test_actual_pinned_native_constructor_and_bootstrap_segment_progression(tmp_path):
    source = os.environ.get("CREATE_FIDELITY_REFERENCE")
    if not source:
        pytest.skip("Set CREATE_FIDELITY_REFERENCE to the exact pinned reference checkout")
    python = os.environ.get("CREATE_FIDELITY_REFERENCE_PYTHON", sys.executable)
    adapter = Path(__file__).resolve().parents[1]
    project = tmp_path / "native-bootstrap"
    project.mkdir()
    # This controlled SPEC input suppresses discovery's optional spec-generation
    # model path. It supplies no benchmark computation or acceptance output.
    (project / "SPEC.md").write_text("# Controlled native bootstrap fixture\nNo application code is authored.\n")
    native_home = tmp_path / "native-home"
    native_home.mkdir()
    code = r'''
import asyncio, hashlib, json, os, sys
from pathlib import Path
from factory.workflow.contributed.create_v2.workflow import workflow
from factory.workflow.executor import WorkflowExecutor
from factory.workflow.validation import validate_workflow
from factory.workflow.primitives import Workflow, FnNode, Edge
from benchmarks.create_fidelity.remote_route import (configure, SEGMENT_ROUTE, LEGACY_SEGMENT_ROUTE,
    reachable_segment, bound_segment, execute_segmented, graph_digest)
source, project = map(Path,sys.argv[1:])
tracked=[source/'factory/workflow/executor.py',source/'factory/workflow/contributed/create_v2/workflow.py']
before=[hashlib.sha256(p.read_bytes()).hexdigest() for p in tracked]
assert before == ['90bca0dc0021716fb044f7dbfedf1371c338de3b105527e95171fb4e9f1d686b',
                  '5bf4080484f60c10969a37fafc5cd445907f8d88e2173c5a83443907143eb183']
original=workflow(); mapped,receipt=configure(original,SEGMENT_ROUTE)
full=mapped.to_dict()
async def model_stop(role,task,path,**kwargs):
    assert role == 'ceo' and kwargs['node_id'] == 'create_factory_md'
    raise RuntimeError('CONTROLLED_EXTERNAL_MODEL_BOUNDARY_STOP')
constructors=[]
for start in ('init_user_intent','discover','create_factory_md','archivist_build'):
    if start != 'init_user_intent':
        try: WorkflowExecutor(mapped.model_copy(update={'start_node':start},deep=True),project,agent_fn=model_stop)
        except ValueError as error: assert 'unreachable from start_node' in str(error)
        else: raise AssertionError('Historical full-graph start copy unexpectedly validated')
    selected=reachable_segment(mapped,start)
    if start in ('discover','create_factory_md'):
        assert any('no predecessor writes' in issue for issue in validate_workflow(selected))
    # Constructor-only checks declare the original cut predecessor outputs as
    # boundary inputs. Actual execution below binds only real native results.
    cut=set(full['nodes'])-set(selected.nodes)
    provided={path for key in cut for path in full['nodes'][key]['writes']}
    selected=reachable_segment(mapped,start,runtime_inputs=provided)
    assert validate_workflow(selected) == []
    WorkflowExecutor(selected,project,agent_fn=model_stop)  # actual validation is enabled
    state=selected.to_dict(); retained=set(state['nodes'])
    assert state['nodes']=={k:v for k,v in full['nodes'].items() if k in retained}
    assert state['edges']==[e for e in full['edges'] if e['source'] in retained and e['target'] in retained]
    assert {k:v for k,v in state.items() if k not in ('nodes','edges','start_node','runtime_inputs')} == {
        k:v for k,v in full.items() if k not in ('nodes','edges','start_node','runtime_inputs')}
    assert set(state.get('runtime_inputs',[])) <= provided
    constructors.append({'start':start,'nodes':len(retained),'edges':len(state['edges'])})
records=[]
def executor(graph):
    return WorkflowExecutor(graph,project,agent_fn=model_stop,auto_approve=False)
def record(index,value): records.append(value)
os.environ['FACTORY_IDEA']='Controlled native bootstrap progression; no benchmark deliverable'
result=asyncio.run(execute_segmented(mapped,project,executor,timeout_s=20,record=record))
assert not result['success'] and result['halted']
assert result['error']=="node 'create_factory_md' failed: CONTROLLED_EXTERNAL_MODEL_BOUNDARY_STOP",result['error']
settled=[r for r in records if r['status']=='settled']
assert [r['start_node'] for r in settled]==['init_user_intent','discover','create_factory_md']
assert settled[0]['continuation']['target']=='discover'
assert settled[1]['continuation']['target']=='create_factory_md'
assert (project/'.factory/eval_profile.json').is_file()
assert '.factory/eval_profile.json' in settled[2]['restored_completed_inputs']
assert all(r['full_graph_sha256']==graph_digest(full) for r in settled)
assert settled[1]['bound_runtime_inputs'].keys()=={'.factory/strategy/user-intent.md'}
assert settled[2]['bound_runtime_inputs'].keys()=={'.factory/strategy/user-intent.md','.factory/eval_profile.json'}
for entry in settled[1:]:
    selected=bound_segment(mapped,project,entry['start_node'],entry['restored_completed_inputs'])
    WorkflowExecutor(selected,project,agent_fn=model_stop)
    assert selected.to_dict()==entry['graph']
    for name in selected.runtime_inputs:
        actual_file=project/name; actual_bytes=actual_file.read_bytes()
        for change in ('mutated','missing'):
            if change=='mutated': actual_file.write_bytes(actual_bytes+b'changed')
            else: actual_file.unlink()
            try: bound_segment(mapped,project,entry['start_node'],entry['restored_completed_inputs'])
            except ValueError: pass
            else: raise AssertionError('Missing/mutated prior native output was admitted')
            actual_file.write_bytes(actual_bytes)
# Exercise actual native QA synthesis as a controlled diagnostic producer for
# the archive constructor. This is input/transport evidence, not successful QA.
import shlex
producer_code='from pathlib import Path; import sys; p=Path(sys.argv[1])/".factory/reviews"; p.mkdir(parents=True,exist_ok=True); (p/"health-check.md").write_text("Controlled health fixture\\n"); (p/"code-review.md").write_text("Controlled code fixture\\n")'
input_node=FnNode(id='controlled_review_inputs',command='env python3 -c '+shlex.quote(producer_code)+' {project_path}',
    writes={'.factory/reviews/health-check.md','.factory/reviews/code-review.md'})
qa_control=Workflow(name='controlled-native-qa-inputs',nodes={input_node.id:input_node,'synthesize_qa':mapped.nodes['synthesize_qa']},
    edges=[Edge(source=input_node.id,target='synthesize_qa')],start_node=input_node.id)
qa_result=asyncio.run(WorkflowExecutor(qa_control,project,agent_fn=model_stop).execute())
assert qa_result.success,qa_result.halt_reason
qa_bound={name:hashlib.sha256((project/name).read_bytes()).hexdigest() for name in qa_result.completed_files}
archive=bound_segment(mapped,project,'archivist_build',qa_bound)
WorkflowExecutor(archive,project,agent_fn=model_stop)
assert archive.runtime_inputs==frozenset({'.factory/reviews/qa-synthesized.md'})
qa_file=project/'.factory/reviews/qa-synthesized.md'; qa_bytes=qa_file.read_bytes()
for change in ('mutated','missing'):
    if change=='mutated': qa_file.write_bytes(qa_bytes+b'changed')
    else: qa_file.unlink()
    try: bound_segment(mapped,project,'archivist_build',qa_bound)
    except ValueError: pass
    else: raise AssertionError('Missing/mutated actual boundary file was admitted')
    qa_file.write_bytes(qa_bytes)
# The former route still reproduces its real constructor failure, separately.
legacy_project=project.parent/'legacy-v1'; legacy_project.mkdir()
legacy_records=[]
try:
    asyncio.run(execute_segmented(mapped,legacy_project,
        lambda graph: WorkflowExecutor(graph,legacy_project,agent_fn=model_stop),
        timeout_s=10,record=lambda index,value:legacy_records.append(value),route=LEGACY_SEGMENT_ROUTE))
except ValueError as error: assert 'unreachable from start_node' in str(error)
else: raise AssertionError('Legacy v1 constructor failure was concealed')
assert legacy_records[-1]['status']=='interrupted' and legacy_records[-1]['native_result'] is None
assert legacy_records[1]['native_result']['halted']
assert mapped.to_dict()==full
assert before==[hashlib.sha256(p.read_bytes()).hexdigest() for p in tracked]
print(json.dumps({'native_constructor_validation':'passed','bootstrap_starts':[r['start_node'] for r in settled],
                  'constructors':constructors,'model_dispatch':'controlled stop only','source_unchanged':True}))
'''
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "HOME": str(native_home),
           "CLAUDE_CONFIG_DIR": str(tmp_path / "claude"), "FACTORY_RUNNER": "claude",
           "PYTHONPATH": os.pathsep.join((source, str(adapter))),
           "PATH": str(Path(python).parent) + os.pathsep + os.environ.get("PATH", "")}
    env.pop("FACTORY_PLAYBOOKS_DIR", None)
    env.pop("FACTORY_PROFILE", None)
    result = subprocess.run([python, "-B", "-c", code, source, str(project)], cwd=project,
                            env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert '"native_constructor_validation": "passed"' in result.stdout
