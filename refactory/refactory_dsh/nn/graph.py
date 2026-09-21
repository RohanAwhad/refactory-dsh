"""The structure of a mode: nodes, edges, and their canonical serialization.

The graph is data. An optimizer edits this data, never Python source, which is
what makes a candidate revertible, diffable and exactly validatable.

Serialization is canonical: nodes are sorted by id and edges by
``(source, target, condition)``. Canonical form is what makes
``build() -> state -> build()`` an identity rather than an approximation.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Iterable

NODE_KINDS = ("op", "agent", "gate")

#: Who answers a gate: the evaluated function, a person, or the running agent.
GATE_KINDS = ("fn", "user", "agent")

#: Who answers a gate. ``fn`` runs the evaluator and routes by its verdict; ``user``
#: is a pause the person answers while using the mode; ``agent`` is one the running
#: agent answers. Only ``fn`` can be answered with nobody present, which is why the
#: other two are refused by a headless run that has not been given a verdict.

_NODE_FIELDS = frozenset(
    {
        "id",
        "kind",
        "call",
        "evaluator",
        "gate_kind",
        "knobs",
        "reads",
        "writes",
        "produces",
        "prompt_template",
        "role",
        "tools",
        "max_iterations",
        "notes",
        "blocking",
        "score_effect",
    }
)


class GraphError(ValueError):
    """A graph could not be built or read from its serialized form."""


@dataclass
class Node:
    """One step of a mode.

    ``op`` runs a resolved Python callable, ``agent`` makes one model call, and
    ``gate`` routes by a verdict. ``gate_kind`` says who gives it: ``fn`` runs
    ``evaluator``, while ``user`` and ``agent`` are answered by a person or by the
    running agent, so they carry no evaluator at all.

    ``writes`` are the files the node produces and ``produces`` the run facts it
    sets. Both are declared rather than discovered, because the validator's
    statement that "every read is produced by some node" is only as true as the
    declarations, and the executor enforces them.
    """

    id: str
    kind: str
    call: str = ""
    evaluator: str = ""
    #: Who answers this gate: ``fn`` (the default), ``user`` or ``agent``.
    gate_kind: str = "fn"
    knobs: tuple[str, ...] = ()
    reads: tuple[str, ...] = ()
    writes: tuple[str, ...] = ()
    produces: tuple[str, ...] = ()
    prompt_template: str = ""
    role: str = "ceo"
    tools: tuple[str, ...] = ()
    max_iterations: int = 1
    notes: str = ""
    blocking: bool = True
    #: Whether inserting this node can change the score. ``affects`` is the
    #: default; ``none`` says the node is advisory, which is worth stating because
    #: a block that cannot change the number is otherwise indistinguishable from
    #: one that can, and an optimizer given both as equals will try the useless one.
    score_effect: str = "affects"

    def __post_init__(self) -> None:
        if self.kind not in NODE_KINDS:
            raise GraphError(f"node {self.id!r}: unknown kind {self.kind!r}")
        if not self.id:
            raise GraphError("a node needs an id")
        if self.kind == "op" and not self.call:
            raise GraphError(f"op node {self.id!r} declares no call")
        if self.kind == "gate" and self.gate_kind not in GATE_KINDS:
            raise GraphError(
                f"gate node {self.id!r}: unknown gate_kind {self.gate_kind!r}; "
                f"known: {list(GATE_KINDS)}"
            )
        if self.kind == "gate" and self.gate_kind == "fn" and not self.evaluator:
            raise GraphError(f"gate node {self.id!r} declares no evaluator")
        if self.kind == "gate" and self.gate_kind != "fn" and self.evaluator:
            # A `user` or `agent` gate is answered by a person or by the running
            # agent. An evaluator would be a second answerer, and which one won
            # would depend on the runtime, so it is refused rather than ignored.
            raise GraphError(
                f"gate node {self.id!r} is answered by {self.gate_kind!r} and "
                "declares an evaluator too, so two things would answer it"
            )
        if self.kind == "agent" and not self.prompt_template:
            raise GraphError(f"agent node {self.id!r} declares no prompt_template")
        if self.max_iterations < 1:
            raise GraphError(f"node {self.id!r}: max_iterations must be >= 1")
        if self.score_effect not in ("affects", "none"):
            raise GraphError(
                f"node {self.id!r}: unknown score_effect {self.score_effect!r}; "
                "known: affects, none"
            )

    def to_state(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "call": self.call,
            "evaluator": self.evaluator,
            # Only when it is not the default. A new field that always serialized
            # would change every mode's state dict, and therefore every candidate
            # id the loop has ever recorded -- which breaks resume and moves a
            # search for reasons that have nothing to do with the search.
            **({"gate_kind": self.gate_kind} if self.gate_kind != "fn" else {}),
            "knobs": list(self.knobs),
            "reads": list(self.reads),
            "writes": list(self.writes),
            "produces": list(self.produces),
            "prompt_template": self.prompt_template,
            "role": self.role,
            "tools": list(self.tools),
            "max_iterations": self.max_iterations,
            "notes": self.notes,
            "blocking": self.blocking,
            "score_effect": self.score_effect,
        }

    @classmethod
    def from_state(cls, state: dict[str, Any]) -> "Node":
        unknown = sorted(set(state) - _NODE_FIELDS)
        if unknown:
            raise GraphError(f"unknown node field(s): {unknown}")
        if "id" not in state or "kind" not in state:
            raise GraphError("a node state needs 'id' and 'kind'")
        return cls(
            id=str(state["id"]),
            kind=str(state["kind"]),
            call=str(state.get("call", "")),
            gate_kind=str(state.get("gate_kind", "fn")),
            evaluator=str(state.get("evaluator", "")),
            knobs=tuple(state.get("knobs") or ()),
            reads=tuple(state.get("reads") or ()),
            writes=tuple(state.get("writes") or ()),
            produces=tuple(state.get("produces") or ()),
            prompt_template=str(state.get("prompt_template", "")),
            role=str(state.get("role", "ceo")),
            tools=tuple(state.get("tools") or ()),
            max_iterations=int(state.get("max_iterations", 1)),
            notes=str(state.get("notes", "")),
            blocking=bool(state.get("blocking", True)),
            score_effect=str(state.get("score_effect", "affects")),
        )

    def with_field(self, field_name: str, value: Any) -> "Node":
        """A copy with one field replaced, refusing an unknown field."""
        if field_name not in _NODE_FIELDS:
            raise GraphError(f"node {self.id!r} has no field {field_name!r}")
        if field_name in ("knobs", "reads", "writes", "produces", "tools") and isinstance(value, list):
            value = tuple(value)
        return replace(self, **{field_name: value})


@dataclass(frozen=True)
class Edge:
    """One connection between two nodes, and the verdict that selects it.

    ``condition`` is the gate verdict this edge answers: ``None`` on the out-edge
    of a non-gate node, and a label on each out-edge of a gate. It is a label
    rather than an enum because a gate names its own outcomes.
    """

    source: str
    target: str
    condition: str | None = None

    def to_state(self) -> dict[str, Any]:
        return {"source": self.source, "target": self.target, "condition": self.condition}

    @classmethod
    def from_state(cls, state: dict[str, Any]) -> "Edge":
        return cls(
            source=str(state["source"]),
            target=str(state["target"]),
            condition=state.get("condition"),
        )

    def key(self) -> tuple[str, str, str]:
        return (self.source, self.target, self.condition or "")


class Graph:
    """A mutable graph. Edits mutate it; serialization canonicalizes it."""

    def __init__(self, *, auto_start: bool = True) -> None:
        self.nodes: dict[str, Node] = {}
        self.edges: list[Edge] = []
        self.start: str = ""
        # Authoring convenience only. A graph parsed from a state never guesses
        # its start node: guessing is a repair, and a repair hides the edit that
        # removed the real one.
        self._auto_start = auto_start

    # ── construction ───────────────────────────────────────────────────────
    def add(self, node: Node) -> Node:
        if node.id in self.nodes:
            raise GraphError(f"node {node.id!r} already exists")
        self.nodes[node.id] = node
        if self._auto_start and not self.start:
            self.start = node.id
        return node

    def connect(self, source: str, target: str, condition: str | None = None) -> Edge:
        for node_id in (source, target):
            if node_id not in self.nodes:
                raise GraphError(f"cannot connect unknown node {node_id!r}")
        edge = Edge(source, target, condition)
        if any(existing.key() == edge.key() for existing in self.edges):
            raise GraphError(f"edge {edge.key()} already exists")
        self.edges.append(edge)
        return edge

    def copy(self) -> "Graph":
        return Graph.from_state(self.to_state())

    # ── queries ────────────────────────────────────────────────────────────
    def out_edges(self, node_id: str, condition: str | None = None) -> list[Edge]:
        return [
            e
            for e in self.edges
            if e.source == node_id and (condition is None or e.condition == condition)
        ]

    def in_edges(self, node_id: str) -> list[Edge]:
        return [e for e in self.edges if e.target == node_id]

    def terminals(self) -> list[str]:
        return sorted(n for n in self.nodes if not self.out_edges(n))

    def reachable(self) -> set[str]:
        seen: set[str] = set()
        stack = [self.start] if self.start in self.nodes else []
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            stack.extend(e.target for e in self.out_edges(current))
        return seen

    def knob_consumers(self) -> dict[str, list[str]]:
        """Which nodes consume each knob, for the dead-knob check."""
        consumers: dict[str, list[str]] = {}
        for node in self.nodes.values():
            for name in node.knobs:
                consumers.setdefault(name, []).append(node.id)
        for ids in consumers.values():
            ids.sort()
        return consumers

    # ── serialization ──────────────────────────────────────────────────────
    def to_state(self) -> dict[str, Any]:
        return {
            "start": self.start,
            "nodes": {nid: self.nodes[nid].to_state() for nid in sorted(self.nodes)},
            "edges": [
                e.to_state()
                for e in sorted(self.edges, key=lambda edge: edge.key())
            ],
        }

    @classmethod
    def from_state(cls, state: dict[str, Any]) -> "Graph":
        graph = cls(auto_start=False)
        if "start" not in state:
            raise GraphError("a graph state needs a 'start'")
        raw_nodes = state.get("nodes") or {}
        if not isinstance(raw_nodes, dict):
            raise GraphError("'nodes' must be an object keyed by node id")
        for node_id in sorted(raw_nodes):
            node_state = dict(raw_nodes[node_id])
            node_state.setdefault("id", node_id)
            if node_state["id"] != node_id:
                raise GraphError(
                    f"node key {node_id!r} disagrees with its id {node_state['id']!r}"
                )
            graph.nodes[node_id] = Node.from_state(node_state)
        graph.start = str(state["start"])
        if graph.start and graph.start not in graph.nodes:
            raise GraphError(f"start node {graph.start!r} does not exist")
        see: set[tuple[str, str, str]] = set()
        for raw in state.get("edges") or ():
            edge = Edge.from_state(raw)
            for node_id in (edge.source, edge.target):
                if node_id not in graph.nodes:
                    raise GraphError(f"edge references unknown node {node_id!r}")
            if edge.key() in see:
                raise GraphError(f"duplicate edge {edge.key()}")
            see.add(edge.key())
            graph.edges.append(edge)
        return graph

    @classmethod
    def from_nodes(cls, nodes: Iterable[Node], edges: Iterable[Edge], start: str) -> "Graph":
        graph = cls()
        for node in nodes:
            graph.add(node)
        for edge in edges:
            graph.connect(edge.source, edge.target, edge.condition)
        graph.start = start
        return graph
