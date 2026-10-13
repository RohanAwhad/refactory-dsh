"""Explicit command configuration for the pinned reference's native executor.

This is a distinct comparator route, not a patched reference checkout. The
executor's public Workflow accepts shell commands, but its inline-Python fast
path greedily swallows a trailing quoted project argument. Only the two pinned
commands with that form select its ordinary shell path through ``env``.
"""
from __future__ import annotations

import hashlib
import asyncio
import json
from pathlib import Path
import shlex
import time
from typing import Any

LEGACY_ROUTE = "native-python-v1"
SHELL_ROUTE = "native-shell-prefix-v1"
LEGACY_SEGMENT_ROUTE = "native-shell-segments-v1"
SEGMENT_ROUTE = "native-shell-segments-v2"
ROUTES = (LEGACY_ROUTE, SHELL_ROUTE, LEGACY_SEGMENT_ROUTE, SEGMENT_ROUTE)
SOURCE_COMMIT = "3ab6f8f7ca792c874f2654574d7f7fd6dd18b896"
SOURCE_GRAPH_SHA256 = "64bbad14710d4584f6bcf5094e59556622d75ddcab0cbd24f1c9eaa79b212951"
COMMANDS = {
    "init_user_intent": 'python3 -c "from factory.workflow.contributed.create_v2.intent_init import main; main()" "{project_path}"',
    "synthesize_qa": 'python3 -c "from factory.workflow.contributed.create_v2.qa_synthesis import main; main()" "{project_path}"',
}


def graph_digest(graph: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(graph, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def configure(workflow: Any, route: str = LEGACY_ROUTE) -> tuple[Any, dict[str, Any]]:
    """Return a separate native Workflow and a complete, reproducible mapping.

    The project placeholder remains unquoted here: the native executor already
    applies shlex.quote at substitution. Python code is shell-quoted as one
    exact argument. Neither source files nor the caller's graph are modified.
    """
    if route not in ROUTES:
        raise ValueError(f"Unsupported remote reference route: {route}")
    original = workflow.to_dict()
    original_digest = graph_digest(original)
    mapped = workflow.model_copy(deep=True)
    commands = []
    if route in (SHELL_ROUTE, LEGACY_SEGMENT_ROUTE, SEGMENT_ROUTE):
        if original_digest != SOURCE_GRAPH_SHA256:
            raise ValueError("Remote shell route requires the exact pinned create-v2 graph")
        for node_id, expected in COMMANDS.items():
            node = mapped.nodes[node_id]
            if type(node).__name__ != "FnNode" or node.command != expected:
                raise ValueError(f"Pinned inline-Python command changed: {node_id}")
            argv = shlex.split(expected)
            if argv[:2] != ["python3", "-c"] or len(argv) != 4 or argv[-1] != "{project_path}":
                raise ValueError(f"Unsupported inline-Python command form: {node_id}")
            effective = "env " + shlex.join(argv[:3]) + " {project_path}"
            mapped.nodes[node_id] = node.model_copy(update={"command": effective})
            commands.append({"node_id": node_id, "original": expected, "effective": effective,
                             "python_code_sha256": hashlib.sha256(argv[2].encode()).hexdigest()})
    effective_graph = mapped.to_dict()
    return mapped, {
        "schema_version": 1, "route": route, "source_commit": SOURCE_COMMIT,
        "entrypoint": "WorkflowRegistry.get_workflow/WorkflowExecutor.execute",
        "execution_correction": route != LEGACY_ROUTE,
        "mapping": "Only the two pinned FnNode inline-Python commands select the native shell fallback; all other fields remain source-defined.",
        "original_graph": original, "original_graph_sha256": original_digest,
        "effective_graph": effective_graph, "effective_graph_sha256": graph_digest(effective_graph),
        "commands": commands,
        "controller": ({"public_start_node_copies": True,
            "public_reachable_subgraphs": route == SEGMENT_ROUTE, "maximum_segments": 4,
            "halt_boundaries": HALT_BOUNDARIES,
            "completed_files_restoration": "Prior actual native result outputs only, verified unchanged hashes",
            "precheck_nonzero_exit": "Stop; diagnostic archive unavailable for the pinned rc1 failure ABI"}
            if route in (LEGACY_SEGMENT_ROUTE, SEGMENT_ROUTE) else None),
        "comparison_scope": "Distinct execution configuration; historical native-Python failure retained. No causal model comparison established.",
    }


HALT_BOUNDARIES = {
    "gate_has_factory": "discover",
    "gate_factory_md_exists": "create_factory_md",
    "gate_precheck": "archivist_build",
}


def halt_continuation(workflow: Any, result: Any) -> dict[str, Any] | None:
    """Select only a declared boundary with an actual native semantic HALT.

    Command failures, AI/user halts and unfamiliar parser messages stop. The
    bootstrap source emits literal HALT, which its native parser reports as
    unparseable; its exact raw token is preserved in the native reason.
    """
    if not result.halted:
        return None
    gates = [event for event in result.events if event.get("type") == "gate.verdict"]
    if not gates:
        return None
    event = gates[-1]
    gate = event.get("node_id")
    if gate not in HALT_BOUNDARIES or event.get("verdict_type") != "halt":
        return None
    node = workflow.nodes.get(gate)
    reason = event.get("reason")
    if (node is None or node.evaluator_type != "fn" or reason != result.halt_reason
            or result.node_outputs.get(gate) != "halt"):
        return None
    canonical = (f"gate '{gate}' returned unparseable verdict "
                 '(expected pass | fail | revert | reloop | {"passed": bool}): HALT')
    if reason != canonical:
        # The native parser's other structured business-negative outcome.
        # Nonzero command exits are deliberately not accepted by this rule.
        if gate != "gate_precheck" or not isinstance(reason, str) or not reason.startswith("precheck failed: [") or not reason.endswith("]"):
            return None
    target = HALT_BOUNDARIES[gate]
    edges = [edge for edge in workflow.edges if edge.source == gate and edge.condition == "halt"]
    if len(edges) != 1 or edges[0].target != target:
        return None
    return {"gate_id": gate, "verdict": "halt", "target": target,
            "raw_native_verdict": event, "terminal_failure": gate == "gate_precheck"}


def _completed_inputs(project: Path, paths: set[str]) -> dict[str, str]:
    observed = {}
    for name in sorted(paths):
        path = project / name
        if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(project.resolve()):
            raise ValueError(f"Native completed output cannot be restored: {name}")
        observed[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return observed


def reachable_segment(workflow: Any, start: str, *, runtime_inputs: set[str] | frozenset[str] = frozenset()) -> Any:
    """Public native subgraph with exact retained fields and edges.

    Native validation counts ForkNode targets and JoinNode source→join links as
    implicit edges. Include the same reachability semantics; remove only nodes
    unreachable from the continuation entry, without pruning optional branches
    or feedback loops. Preserve metadata and explicitly declare supplied boundary
    inputs; no retained node's read/write declaration changes.
    """
    original = workflow.to_dict()
    nodes = original["nodes"]
    if start not in nodes:
        raise ValueError(f"Unknown remote continuation entry: {start}")
    adjacency = {node_id: set() for node_id in nodes}
    for edge in original["edges"]:
        adjacency[edge["source"]].add(edge["target"])
    for node_id, node in nodes.items():
        if node["_type"] == "ForkNode":
            adjacency[node_id].update(node.get("targets", []))
        if node["_type"] == "JoinNode":
            for source in node.get("sources", []):
                adjacency[source].add(node_id)
    reachable = set()
    pending = [start]
    while pending:
        current = pending.pop()
        if current in reachable:
            continue
        reachable.add(current)
        pending.extend(adjacency[current] - reachable)
    subgraph = workflow.subgraph(reachable, name=original["name"], start_node=start)
    # subgraph() deliberately resets workflow metadata; preserve the author's
    # terminal/knob/task/capability/runtime-input declarations in the full copy.
    retained_reads = {path for node_id in reachable for path in nodes[node_id]["reads"]}
    supplied = set(original.get("runtime_inputs", [])) | (set(runtime_inputs) & retained_reads)
    selected = workflow.model_copy(update={"nodes": subgraph.nodes, "edges": subgraph.edges,
                                          "start_node": start, "runtime_inputs": frozenset(supplied)}, deep=True)
    actual = selected.to_dict()
    expected = {**original, "start_node": start,
                "nodes": {key: value for key, value in nodes.items() if key in reachable},
                "edges": [edge for edge in original["edges"]
                          if edge["source"] in reachable and edge["target"] in reachable]}
    if supplied:
        expected["runtime_inputs"] = sorted(supplied)
    if actual != expected:
        raise ValueError("Native reachable subgraph changed retained source declarations")
    return selected


def bound_segment(workflow: Any, project: Path, start: str, completed: dict[str, str]) -> Any:
    """Build the native continuation only from unchanged supplied output bytes."""
    if _completed_inputs(project, set(completed)) != completed:
        raise ValueError("Native prior segment output changed before continuation")
    return reachable_segment(workflow, start, runtime_inputs=set(completed))


async def execute_segmented(workflow: Any, project: Path, executor_factory: Any, *,
                            timeout_s: float, record: Any,
                            route: str = SEGMENT_ROUTE) -> dict[str, Any]:
    """Native executions joined only at three declared HALT edges.

    Each start-node copy uses the public Pydantic Workflow API. The native
    completed-files set is restored solely from hash-bound prior native outputs;
    no unproduced file or invented verdict is admitted. This correction is
    explicitly separate from the source's single execute() route.
    """
    if route not in (LEGACY_SEGMENT_ROUTE, SEGMENT_ROUTE):
        raise ValueError("Unsupported remote segment controller route")
    if timeout_s <= 0:
        raise ValueError("Remote controller timeout must be positive")
    expected = workflow.to_dict()
    # The same pinned command mapping must still be the entire effective graph.
    # Restore the two source commands to verify everything else before execution.
    restored = json.loads(json.dumps(expected))
    for node_id, command in COMMANDS.items():
        effective = "env " + shlex.join(shlex.split(command)[:3]) + " {project_path}"
        if expected["nodes"][node_id]["command"] != effective:
            raise ValueError("Segment controller requires the exact declared shell command mapping")
        restored["nodes"][node_id]["command"] = command
    if graph_digest(restored) != SOURCE_GRAPH_SHA256:
        raise ValueError("Segment controller requires the unchanged pinned graph and declared edges")
    deadline = time.monotonic() + timeout_s
    start = workflow.start_node
    completed: dict[str, str] = {}
    events = []
    segments = []
    seen = set()
    failure = ""
    final = None
    for index in range(4):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Remote segmented controller deadline expired")
        if graph_digest(workflow.to_dict()) != graph_digest(expected):
            raise ValueError("Remote graph changed during segmented execution")
        if _completed_inputs(project, set(completed)) != completed:
            raise ValueError("Native prior segment output changed before continuation")
        segment_graph = (bound_segment(workflow, project, start, completed) if route == SEGMENT_ROUTE else
                         workflow.model_copy(update={"start_node": start}, deep=True))
        segment = {"index": index, "start_node": start, "graph": segment_graph.to_dict(),
                   "graph_sha256": graph_digest(segment_graph.to_dict()),
                   "full_graph_sha256": graph_digest(expected),
                   "original_source_graph_sha256": SOURCE_GRAPH_SHA256,
                   "excluded_unreachable_nodes": sorted(set(expected["nodes"]) - set(segment_graph.nodes)),
                   "bound_runtime_inputs": {name: completed[name] for name in
                       segment_graph.to_dict().get("runtime_inputs", []) if name in completed},
                   "route": route,
                   "restored_completed_inputs": completed, "remaining_timeout_s": remaining,
                   "status": "running"}
        record(index, segment)
        try:
            executor = executor_factory(segment_graph)
            # This is public native state, populated only by prior native results.
            executor.completed_files.update(completed)
            if _completed_inputs(project, set(completed)) != completed:
                raise ValueError("Native prior segment output changed during continuation preparation")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Remote segmented controller deadline expired during preparation")
            final = await asyncio.wait_for(executor.execute(), timeout=remaining)
        except BaseException as error:
            record(index, {**segment, "status": "interrupted", "error": str(error),
                           "error_type": type(error).__name__, "native_result": None})
            raise
        # Keep every original ExecutionResult field and all native events. Sets
        # become sorted arrays solely for JSON transport; no verdict is rewritten.
        native = {key: sorted(value) if isinstance(value, set) else value
                  for key, value in vars(final).items()}
        continuation = halt_continuation(workflow, final)
        segment = {**segment, "status": "settled", "native_result": native,
                   "continuation": continuation}
        record(index, segment)
        segments.append(segment)
        events.extend(final.events)
        if continuation is None:
            break
        boundary = continuation["gate_id"]
        if boundary in seen:
            raise ValueError("Remote boundary repeated; no implicit controller loop is permitted")
        seen.add(boundary)
        if continuation["terminal_failure"]:
            failure = final.halt_reason
        completed = _completed_inputs(project, final.completed_files)
        start = continuation["target"]
    else:
        raise ValueError("Remote segment limit exhausted before a native terminal result")
    assert final is not None
    return {"success": final.success and not failure, "halted": final.halted or bool(failure),
            "error": failure or final.halt_reason, "nodes_executed": sum(s["native_result"]["nodes_executed"] for s in segments),
            "duration_ms": sum(s["native_result"]["duration_ms"] for s in segments),
            "events": events, "segments": segments,
            "execution_route": route,
            "diagnostic_archive_after_precheck_failure": bool(failure),
            "diagnostic_archive_native_success": final.success if failure else None}
