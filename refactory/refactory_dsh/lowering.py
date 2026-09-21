"""Lower a mode state to refactory's IR, so DSH can run it.

The IR is the seam. A mode's state dict stays the optimizer's write surface; the
IR is what runs. Building real `factory.workflow` objects rather than emitting JSON
by hand means pydantic validates the result, so an IR that lowers is an IR that
loads.

Node mapping:

======================  ==================================================
`op`                    `FnNode` running ``python -m refactory_dsh.run <call>``
`agent`                 `AgentNode`, which DSH runs as a subagent
`gate`                  `GateNode`. ``fn`` runs its evaluator and routes by the
                        last stdout line; ``user`` and ``agent`` carry no command
                        and are answered by the person or by the driving agent
======================  ==================================================

The runner is told its own node through the spine's `DSH_WORKFLOW_NODE`, and the
knobs through the command environment, so a node command knows nothing about the
graph that invoked it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from factory.workflow.primitives import AgentNode, AgentRole, Edge, FnNode, GateNode, Workflow

from refactory_dsh.nn import Graph, Knob

#: How a node names the operation it runs. Resolved by the runtime, so no package
#: is named here and no absolute path is baked in: `--python-bin` supplies the PATH.
RUNNER = "python -m refactory_dsh.run"

#: The environment variable the knobs travel in.
KNOBS_ENV = "REFACTORY_KNOBS"

#: The graph file the science bundle loads, one per mode.
SUFFIX = ".graph.json"

#: Where the run's node declarations are left for the runner.
#:
#: `DSH_WORKFLOW_NODE` carries `{id, type, command, reads, writes}` -- the IR has no
#: representation for the facts a node produces, because the IR's nodes do not have
#: facts. So the declarations the IR cannot carry are written into the run directory,
#: and the runner reads them there. Without this the runner could not enforce the
#: produces check, and a node that lies about its facts would go unnoticed on the
#: DSH path while being caught on the walker.
DECLARATIONS = ".refactory/declarations.json"

#: The key the mode's declared memory travels under, beside the node declarations.
MEMORY_KEY = "memory"


def node_declarations(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Every node's kind and declared reads, writes and facts, keyed by node id.

    The kind travels because a reader of a finished run has the run directory and
    not the state: the citation chain has to know which nodes made a model call, or
    it would demand a citation from a deterministic op.
    """
    graph = Graph.from_state(state.get("graph") or {})
    return {
        node_id: {
            "kind": node.kind,
            "reads": sorted(node.reads),
            "writes": sorted(node.writes),
            "produces": sorted(node.produces),
        }
        for node_id, node in graph.nodes.items()
    }


def write_declarations(state: dict[str, Any], workdir: Path) -> Path:
    """Leave the declarations where the runner will look for them.

    Two things a node needs that the IR cannot carry: the facts it produces, and
    the memory its mode declares. Both travel beside the graph, keyed by node id
    for the first and under ``memory`` for the second.

    Memory has to travel, not be re-derived: the runner builds the store an op
    receives, and a store built from nothing is how a mode that keeps a cross-run
    prior works in the walker and silently does nothing on DSH.
    """
    path = Path(workdir) / DECLARATIONS
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = node_declarations(state)
    declared = state.get("memory") or []
    if declared:
        payload[MEMORY_KEY] = declared
    path.write_text(json.dumps(payload, indent=2, default=str))
    return path


def read_declarations(workdir: Path) -> dict[str, dict[str, list[str]]]:
    path = Path(workdir) / DECLARATIONS
    if not path.exists():
        return {}
    try:
        loaded = json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def read_memory_declarations(workdir: Path) -> list[Any]:
    """The mode's memory declarations, as the runner reads them back."""
    from refactory_dsh.nn import Memory

    loaded = read_declarations(workdir).get(MEMORY_KEY) or []
    out: list[Any] = []
    for entry in loaded:
        if isinstance(entry, dict):
            out.append(Memory.from_state(entry))
    return out


class LoweringError(ValueError):
    """A state cannot be expressed in the IR, and saying why is more useful than a traceback."""


def to_workflow(state: dict[str, Any], *, name: str = "", description: str = "") -> Workflow:
    """The refactory `Workflow` for a mode state."""
    graph = Graph.from_state(state.get("graph") or {})
    nodes: dict[str, Any] = {}
    for node_id in sorted(graph.nodes):
        node = graph.nodes[node_id]
        common = {
            "id": node.id,
            "reads": set(node.reads),
            "writes": set(node.writes),
            "blocking": node.blocking,
        }
        if node.kind == "op":
            nodes[node_id] = FnNode(
                **common,
                # Both fields, and both for a reason. `callable_name` is
                # refactory's own way of naming what an `FnNode` runs
                # (`factory/workflow/primitives.py`), and it is what makes the IR
                # legible to refactory's tooling. DSH's spine does not model it —
                # its `WorkflowNode` has `command` and nothing else — so a graph
                # carrying only `callable_name` would be a node that never runs.
                # Emitting both keeps the IR faithful to the language it is written
                # in and still executable by the runtime we ship.
                callable_name=node.call,
                command=f"{RUNNER} {node.call}",
                notes=node.notes,
            )
        elif node.kind == "gate":
            # Who answers it decides whether there is a command at all. `fn` runs its
            # evaluator; `user` and `agent` are handed to the driving agent by the
            # spine, which presents a user gate to the person. Emitting a command for
            # those would give the gate two answerers, and which one won would depend
            # on the runtime.
            gate: dict[str, Any] = {
                "evaluator_type": node.gate_kind,
                "max_iterations": node.max_iterations,
            }
            # The author's note is the gate's question: for a `user` gate it is what
            # the person is shown, and an empty prompt would ask them to choose
            # between labels with no statement of what they are choosing about.
            if node.notes:
                gate["gate_prompt"] = node.notes
            if node.gate_kind == "fn":
                gate["evaluator_command"] = f"{RUNNER} {node.evaluator}"
            nodes[node_id] = GateNode(**common, **gate)
        else:
            try:
                role = AgentRole(node.role)
            except ValueError as exc:
                known = ", ".join(member.value for member in AgentRole)
                raise LoweringError(
                    f"agent node {node.id!r} declares role {node.role!r}, which the IR "
                    f"does not have; known roles: {known}"
                ) from exc
            nodes[node_id] = AgentNode(
                **common,
                role=role,
                prompt_template=node.prompt_template,
                tools=list(node.tools),
                max_iterations=node.max_iterations,
            )

    edges = [
        Edge(source=edge.source, target=edge.target, condition=edge.condition)
        for edge in sorted(graph.edges, key=lambda e: e.key())
    ]
    return Workflow(
        name=name or "mode",
        nodes=nodes,
        edges=edges,
        start_node=graph.start,
        terminal=True,
        knob_values=_ir_knobs(state.get("knobs") or {}),
        knob_specs={
            knob_name: dict(entry.get("spec") or {})
            for knob_name, entry in (state.get("knobs") or {}).items()
        },
    )


def _ir_knobs(knobs: dict[str, Any]) -> dict[str, str | float]:
    """The knob values as the IR records them.

    The IR's field is ``str | float`` and pydantic is strict, so a bool or an int
    has to be projected. The authoritative transport is the JSON in the command
    environment, which keeps the types; this is the IR's own record of the run.
    """
    out: dict[str, str | float] = {}
    for knob_name, entry in knobs.items():
        value = entry.get("value")
        if isinstance(value, bool):
            out[knob_name] = 1.0 if value else 0.0
        elif isinstance(value, (int, float)):
            out[knob_name] = float(value)
        else:
            out[knob_name] = str(value)
    return out


def knob_environment(state: dict[str, Any], *, extra: dict[str, str] | None = None) -> dict[str, str]:
    """The environment a node command runs under: the knobs, and whatever else."""
    values = {name: entry.get("value") for name, entry in (state.get("knobs") or {}).items()}
    env = {KNOBS_ENV: json.dumps(values, default=str)}
    env.update(extra or {})
    return env


def validate_ir(workflow: Workflow) -> list[str]:
    """refactory's own validation of a lowered graph.

    A second opinion on purpose: the spine refuses a graph its walk cannot follow,
    and this is the language's own check. A state that passes our validator and
    fails this one is a lowering bug, not a bad candidate.
    """
    return list(workflow.validate_graph())


def write_mode_graph(
    state: dict[str, Any],
    out_dir: Path,
    *,
    name: str,
    description: str = "",
    transform: Any = None,
) -> tuple[Path, Workflow]:
    """Write ``<out_dir>/<name>.graph.json`` the way the science bundle loads it.

    ``transform`` is applied to the workflow before it is written, so a caller can
    layer something over the IR without this function knowing what the layer is. The
    default writes the mode unchanged.
    """
    workflow = to_workflow(state, name=name, description=description)
    if transform is not None:
        workflow = transform(workflow)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{name}{SUFFIX}"
    path.write_text(json.dumps(workflow.to_dict(), indent=2, default=str))
    return path, workflow


def node_manifest(state: dict[str, Any], node_id: str) -> dict[str, Any]:
    """What a node command is told about itself, mirroring `DSH_WORKFLOW_NODE`."""
    graph = Graph.from_state(state.get("graph") or {})
    node = graph.nodes[node_id]
    return {
        "id": node.id,
        "type": {"op": "FnNode", "agent": "AgentNode", "gate": "GateNode"}[node.kind],
        "command": node.call or node.evaluator,
        "reads": sorted(node.reads),
        "writes": sorted(node.writes),
    }


def knob_specs(state: dict[str, Any]) -> dict[str, Knob]:
    return {
        name: Knob.from_spec(dict(entry.get("spec") or {}))
        for name, entry in (state.get("knobs") or {}).items()
    }
