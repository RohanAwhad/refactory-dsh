"""The validator: what makes architecture-in-the-state safe.

PyTorch gets structural safety for free because ``nn.Module`` owns the
architecture and ``load_state_dict(strict=True)`` only has to check that the
parameters match it. We put the architecture in the state dict, which is what
makes architecture search possible, so something has to play that role. This
module is that something.

The validator answers one question: is this state a well-formed, runnable mode?
It never changes the state. An edit that is not well-formed is rejected with a
reason, because a repaired edit would be scored as if it were the edit that was
proposed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

from .graph import Graph
from .knob import Knob, KnobError
from .resolve import ResolutionError, resolve


@dataclass(frozen=True)
class Issue:
    """One reason a state was refused."""

    code: str
    message: str
    node: str | None = None
    knob: str | None = None

    def __str__(self) -> str:
        where = f" [{self.node}]" if self.node else (f" [{self.knob}]" if self.knob else "")
        return f"{self.code}{where}: {self.message}"


@dataclass(frozen=True)
class Invariant:
    """A mode-declared rule, for the parts only the author can know.

    Metric integrity is the motivating example: the fact ``score()`` reads has to
    be produced by the graph, or the score is not about the mode any more. The
    check returns a violation message, or ``None`` when the state is acceptable.
    """

    name: str
    description: str
    check: Callable[[Graph, Mapping[str, Any]], str | None]


def _cycle_without_gate(graph: Graph) -> list[str] | None:
    """Return a cycle in the gate-free subgraph, or ``None`` if there is none."""
    open_nodes = {nid for nid, node in graph.nodes.items() if node.kind != "gate"}
    state: dict[str, int] = {}
    stack: list[str] = []

    def visit(node_id: str) -> list[str] | None:
        state[node_id] = 1
        stack.append(node_id)
        for edge in graph.out_edges(node_id):
            if edge.target not in open_nodes:
                continue
            if state.get(edge.target) == 1:
                return stack[stack.index(edge.target):] + [edge.target]
            if state.get(edge.target, 0) == 0:
                found = visit(edge.target)
                if found:
                    return found
        stack.pop()
        state[node_id] = 2
        return None

    for node_id in sorted(open_nodes):
        if state.get(node_id, 0) == 0:
            found = visit(node_id)
            if found:
                return found
    return None


def validate(
    graph: Graph,
    values: Mapping[str, Any],
    *,
    declared_knobs: Mapping[str, Knob],
    invariants: tuple[Invariant, ...] = (),
    resolve_calls: bool = True,
) -> list[Issue]:
    """Every reason ``(graph, values)`` is not a runnable, scoreable mode."""
    issues: list[Issue] = []

    # V1 start node exists.
    if not graph.start:
        issues.append(Issue("V1", "the graph declares no start node"))
    elif graph.start not in graph.nodes:
        issues.append(Issue("V1", f"start node {graph.start!r} does not exist"))

    if not graph.nodes:
        issues.append(Issue("V1", "the graph has no nodes"))
        return _sorted(issues)

    # V2 reachability.
    reachable = graph.reachable()
    for node_id in sorted(set(graph.nodes) - reachable):
        issues.append(
            Issue("V2", "node is not reachable from the start node", node=node_id)
        )

    # V3 a terminal exists.
    if not graph.terminals():
        issues.append(Issue("V3", "the graph has no terminal node"))

    # V4/V5 edge shape around every node.
    for node_id in sorted(graph.nodes):
        node = graph.nodes[node_id]
        out = graph.out_edges(node_id)
        unconditional = [e for e in out if e.condition is None]
        conditioned = [e for e in out if e.condition is not None]
        if node.kind == "gate":
            if len(conditioned) < 2:
                issues.append(
                    Issue(
                        "V5",
                        f"a gate needs at least two conditioned out-edges, has {len(conditioned)}",
                        node=node_id,
                    )
                )
            if unconditional:
                issues.append(
                    Issue(
                        "V5",
                        "a gate must not have an unconditional out-edge; "
                        "every edge out of a gate carries a verdict",
                        node=node_id,
                    )
                )
        elif len(out) > 1:
            issues.append(
                Issue(
                    "V4",
                    f"a non-gate node needs at most one out-edge, has {len(out)}",
                    node=node_id,
                )
            )
        elif conditioned:
            issues.append(
                Issue(
                    "V4",
                    "a non-gate node must not branch; only a gate's out-edges "
                    "carry verdicts",
                    node=node_id,
                )
            )

    # V6 every read is produced by some node.
    produced = {name for node in graph.nodes.values() for name in node.writes}
    for node_id in sorted(graph.nodes):
        for name in graph.nodes[node_id].reads:
            if name not in produced:
                issues.append(
                    Issue(
                        "V6",
                        f"reads {name!r}, which no node produces",
                        node=node_id,
                    )
                )

    # V7 knobs: consumed ones exist, and declared ones are consumed.
    consumers = graph.knob_consumers()
    for node_id in sorted(graph.nodes):
        for name in graph.nodes[node_id].knobs:
            if name not in declared_knobs:
                issues.append(
                    Issue(
                        "V7",
                        f"consumes undeclared knob {name!r}",
                        node=node_id,
                        knob=name,
                    )
                )
    for name in sorted(declared_knobs):
        if name not in consumers:
            issues.append(
                Issue(
                    "V7",
                    "knob is declared but no node consumes it, so moving it "
                    "cannot change the run",
                    knob=name,
                )
            )

    # V8 callables resolve.
    if resolve_calls:
        for node_id in sorted(graph.nodes):
            node = graph.nodes[node_id]
            target = node.call if node.kind == "op" else (
                node.evaluator if node.kind == "gate" else ""
            )
            if not target:
                continue
            try:
                resolve(target)
            except ResolutionError as exc:
                issues.append(Issue("V8", str(exc), node=node_id))

    # V9 parameter values are in bounds.
    for name in sorted(values):
        knob = declared_knobs.get(name)
        if knob is None:
            issues.append(Issue("V9", f"value for undeclared knob {name!r}", knob=name))
            continue
        try:
            knob.coerce(values[name])
        except KnobError as exc:
            issues.append(Issue("V9", str(exc), knob=name))
    for name in sorted(set(declared_knobs) - set(values)):
        issues.append(Issue("V9", "knob has no value", knob=name))

    # V10 every cycle passes through a gate, so the executor can bound it.
    cycle = _cycle_without_gate(graph)
    if cycle:
        issues.append(
            Issue(
                "V10",
                f"cycle {cycle} does not pass through a gate, so nothing bounds it",
                node=cycle[0],
            )
        )

    # Mode-declared invariants.
    for invariant in invariants:
        try:
            violation = invariant.check(graph, values)
        except Exception as exc:  # a broken invariant is reported, not raised
            violation = f"invariant raised {type(exc).__name__}: {exc}"
        if violation:
            issues.append(Issue(f"I:{invariant.name}", violation))

    return _sorted(issues)


def _sorted(issues: list[Issue]) -> list[Issue]:
    return sorted(issues, key=lambda i: (i.code, i.node or "", i.knob or "", i.message))


def declared_knobs(state: Mapping[str, Any]) -> dict[str, Knob]:
    """The knobs a state declares, as ``Knob`` objects."""
    out: dict[str, Knob] = {}
    for name, entry in (state.get("knobs") or {}).items():
        out[name] = Knob.from_spec(dict(entry["spec"]))
    return out


def validate_state(
    state: Mapping[str, Any],
    *,
    invariants: tuple[Invariant, ...] = (),
    resolve_calls: bool = True,
) -> list[Issue]:
    """Validate a state dict without building a mode around it.

    Everything the check needs is in the state, which is the point of putting the
    architecture there: a candidate can be judged before anything is run.
    """
    try:
        graph = Graph.from_state(state["graph"])
    except KeyError as exc:
        return [Issue("V0", f"the state has no {exc.args[0]!r}")]
    except Exception as exc:  # a malformed graph is an issue, not a crash
        return [Issue("V0", f"the graph is not readable: {exc}")]
    return validate(
        graph,
        {name: entry["value"] for name, entry in (state.get("knobs") or {}).items()},
        declared_knobs=declared_knobs(state),
        invariants=invariants,
        resolve_calls=resolve_calls,
    )


def fact_produced(*facts: str) -> Invariant:
    """An invariant requiring the graph to produce the facts ``score()`` reads.

    This is the metric-integrity rule, and it is the one that makes removing work
    a legitimate edit rather than a cheat: a candidate may drop any node it likes,
    as long as the number being maximized is still produced by the run.
    """

    wanted = tuple(facts)

    def check(graph: Graph, _values: Mapping[str, Any]) -> str | None:
        produced = {name for node in graph.nodes.values() for name in node.produces}
        missing = [name for name in wanted if name not in produced]
        if missing:
            return (
                f"the score reads {missing}, which no node produces, so the "
                "number would not describe this mode's run"
            )
        return None

    return Invariant("fact_produced", f"graph produces {list(wanted)}", check)
