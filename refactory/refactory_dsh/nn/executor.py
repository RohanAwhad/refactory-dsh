"""Execute a built graph in this process.

One node at a time, one unconditional edge at a time, with every branch decided
by a gate's verdict. That model is why the validator can make promises a
free-for-all executor could not: fan-out is forbidden on a non-gate node, every
cycle passes through a gate, and a gate carries the iteration bound that stops
it.

The executor also holds the dataflow model to its word. A node declares what it
writes; an op that writes anything else is refused, because the validator's
"every read is produced by some node" promise is only true if the declarations
are.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

from .graph import Graph, Node
from .mode import RunResult
from .resolve import resolve


class ExecutionError(RuntimeError):
    """A run could not be carried out."""


class ModelClient(Protocol):
    """The one thing an agent node needs: text in, text out."""

    def complete(self, prompt: str, *, system: str = "") -> str: ...


@dataclass
class Step:
    """One visited node."""

    node: str
    kind: str
    duration_s: float
    verdict: str | None = None
    error: str | None = None


class RunContext:
    """What a node's callable sees: a work directory, the knobs, and the facts.

    Facts are the run's own numbers, and they are what ``score()`` reads. A write
    is checked against the node's declaration so the graph's dataflow statements
    stay true.
    """

    def __init__(
        self,
        workdir: Path,
        *,
        knobs: dict[str, Any] | None = None,
        facts: dict[str, Any] | None = None,
        verdicts: dict[str, str] | None = None,
        model: ModelClient | None = None,
        task: dict[str, Any] | None = None,
        memory: Any = None,
        enforce_writes: bool = True,
    ) -> None:
        self.workdir = Path(workdir)
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.knobs: dict[str, Any] = dict(knobs or {})
        self.facts: dict[str, Any] = dict(facts or {})
        #: Verdicts for gates a person or an agent answers, by node id. Empty in a
        #: headless run, which is why such a gate refuses rather than deciding.
        self.verdicts: dict[str, str] = dict(verdicts or {})
        self.model = model
        self.task: dict[str, Any] = dict(task or {})
        #: The mode's declared memory, for an op that has something to remember.
        self.memory = memory
        self.enforce_writes = enforce_writes
        self.iteration = 0
        self.node_id = ""
        self._declared: tuple[str, ...] = ()
        self._declared_facts: tuple[str, ...] = ()
        self._produced: set[str] = set()
        self.writes_made: list[str] = []

    # ── knobs ──────────────────────────────────────────────────────────────
    def knob(self, name: str, default: Any = None) -> Any:
        return self.knobs.get(name, default)

    # ── files ──────────────────────────────────────────────────────────────
    def path(self, name: str) -> Path:
        """A file inside the work directory. Escaping it is refused."""
        candidate = Path(name)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise ExecutionError(f"{name!r} is not a relative path inside the work directory")
        return self.workdir / candidate

    def _note_write(self, name: str) -> None:
        if not self.enforce_writes or not self._declared:
            return
        for declared in self._declared:
            if name == declared or name.startswith(declared.rstrip("/") + "/"):
                self.writes_made.append(name)
                return
        raise ExecutionError(
            f"node {self.node_id!r} wrote {name!r}, which it does not declare; "
            f"declared writes: {list(self._declared)}"
        )

    def write_text(self, name: str, text: str) -> Path:
        self._note_write(name)
        target = self.path(name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
        return target

    def append_text(self, name: str, text: str) -> Path:
        self._note_write(name)
        target = self.path(name)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a") as handle:
            handle.write(text)
        return target

    def read_text(self, name: str) -> str:
        return self.path(name).read_text()

    def write_json(self, name: str, payload: Any) -> Path:
        import json

        return self.write_text(name, json.dumps(payload, indent=2, default=str))

    def read_json(self, name: str, default: Any = None) -> Any:
        import json

        target = self.path(name)
        if not target.exists():
            return default
        try:
            return json.loads(target.read_text())
        except json.JSONDecodeError:
            return default

    def exists(self, name: str) -> bool:
        return self.path(name).exists()

    # ── facts ──────────────────────────────────────────────────────────────
    def set_fact(self, name: str, value: Any) -> None:
        """Set a run fact. A node may only set facts it declares in ``produces``."""
        self.facts[name] = value
        self._produced.add(name)

    def _internal_fact(self, name: str, value: Any) -> None:
        """A fact the run itself records, not one a node claims to produce."""
        self.facts[name] = value

    def _check_produces(self) -> None:
        if not self.enforce_writes:
            return
        extra = sorted(self._produced - set(self._declared_facts))
        if extra:
            raise ExecutionError(
                f"node {self.node_id!r} produced the fact(s) {extra}, which it does "
                f"not declare; declared facts: {list(self._declared_facts)}"
            )


def render(template: str, ctx: RunContext) -> str:
    """Fill ``{{name}}`` from the run's knobs and facts.

    Deliberately not :meth:`str.format`: a prompt is prose full of braces, and a
    single stray one must not be able to make an agent node unrunnable.
    """
    import re

    values = {**ctx.knobs, **ctx.facts}

    def replace(match: re.Match[str]) -> str:
        key = match.group(1).strip()
        if key not in values:
            return match.group(0)
        return str(values[key])

    return re.sub(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}", replace, template)


@dataclass
class Executor:
    """Walk a graph from its start node to a terminal."""

    graph: Graph
    ctx: RunContext
    max_steps: int = 2000
    steps: list[Step] = field(default_factory=list)
    model_calls: int = 0

    def run(self) -> RunResult:
        started = time.time()
        visits: dict[str, int] = {}
        current: str | None = self.graph.start or None
        error: str | None = None

        while current is not None:
            node = self.graph.nodes.get(current)
            if node is None:
                error = f"node {current!r} does not exist"
                break
            if len(self.steps) >= self.max_steps:
                error = f"the run exceeded {self.max_steps} steps"
                break
            # Only a gate can create a cycle, so only a gate carries an iteration
            # bound. A non-gate node may be visited again on the next pass.
            if node.kind == "gate" and visits.get(current, 0) > node.max_iterations:
                error = (
                    f"gate {current!r} returned a looping verdict {visits[current]} "
                    f"times, past its max_iterations of {node.max_iterations}"
                )
                break
            visits[current] = visits.get(current, 0) + 1

            self.ctx.iteration = len(self.steps)
            self.ctx.node_id = current
            self.ctx._declared = node.writes  # noqa: SLF001 - the run owns the context
            self.ctx._declared_facts = node.produces  # noqa: SLF001
            self.ctx._produced = set()  # noqa: SLF001
            step_started = time.time()
            verdict: str | None = None
            try:
                current, verdict = self._visit(node)
                self.ctx._check_produces()  # noqa: SLF001
            except Exception as exc:  # a failing node ends the run, visibly
                self.steps.append(
                    Step(node.id, node.kind, time.time() - step_started,
                         error=f"{type(exc).__name__}: {exc}")
                )
                error = f"{node.id}: {type(exc).__name__}: {exc}"
                current = None
                break
            self.steps.append(
                Step(node.id, node.kind, time.time() - step_started, verdict=verdict)
            )

        if error is None:
            for step in self.steps:
                if step.error:
                    error = step.error
                    break

        return RunResult(
            facts=dict(self.ctx.facts),
            iterations=len(self.steps),
            duration_s=round(time.time() - started, 4),
            exit_ok=error is None,
            error=error,
            workdir=self.ctx.workdir,
            model_calls=self.model_calls,
        )

    # ── one node ───────────────────────────────────────────────────────────
    def _visit(self, node: Node) -> tuple[str | None, str | None]:
        """Run one node and return ``(next node id, verdict)``."""
        if node.kind == "op":
            fn = resolve(node.call)
            produced = fn(self.ctx)
            if isinstance(produced, dict):
                for key, value in produced.items():
                    self.ctx.set_fact(key, value)
        elif node.kind == "agent":
            self._visit_agent(node)
        else:
            return self._visit_gate(node)
        out = self.graph.out_edges(node.id)
        return (out[0].target if out else None), None

    def _visit_agent(self, node: Node) -> None:
        if self.ctx.model is None:
            raise ExecutionError(f"agent node {node.id!r} needs a model client")
        prompt = render(node.prompt_template, self.ctx)
        self.model_calls += 1
        output = self.ctx.model.complete(prompt, system=node.notes)
        if node.writes:
            self.ctx.write_text(node.writes[0], output)
        if node.produces:
            self.ctx.set_fact(node.produces[0], output)

    def _answered_verdict(self, node: Node) -> str:
        """The verdict for a gate a person or an agent answers.

        A headless walk has nobody to ask, so it **refuses** rather than inventing an
        answer. That is the whole point of :attr:`Node.gate_kind`: a run that pauses
        for a person cannot quietly decide for them, and a run that did would be a
        result nobody can account for. The answer is supplied through
        ``RunContext.verdicts``, which an interactive run fills and a resumed run
        replays from the intent ledger rather than asking again.
        """
        verdict = (self.ctx.verdicts or {}).get(node.id, "")
        if not verdict:
            raise ExecutionError(
                f"gate {node.id!r} is answered by {node.gate_kind!r} and this run has "
                "no verdict for it. A headless run cannot answer a gate meant for a "
                "person or for an agent; pass one in RunContext.verdicts (an "
                "interactive run collects it, a resumed run replays it) or make the "
                "gate `fn`."
            )
        return str(verdict)

    def _visit_gate(self, node: Node) -> tuple[str, str]:
        if node.gate_kind != "fn":
            verdict = self._answered_verdict(node)
        else:
            fn: Callable[[RunContext], Any] = resolve(node.evaluator)
            verdict = fn(self.ctx)
        if not isinstance(verdict, str) or not verdict:
            raise ExecutionError(
                f"gate {node.id!r} returned {verdict!r}; a gate must return a verdict label"
            )
        out = self.graph.out_edges(node.id, condition=verdict)
        if not out:
            labels = sorted(e.condition or "" for e in self.graph.out_edges(node.id))
            raise ExecutionError(
                f"gate {node.id!r} returned {verdict!r}, which is not one of its "
                f"verdicts {labels}"
            )
        self.ctx._internal_fact(f"{node.id}_verdict", verdict)  # noqa: SLF001
        return out[0].target, verdict


def execute(
    graph: Graph,
    workdir: Path,
    *,
    knobs: dict[str, Any] | None = None,
    facts: dict[str, Any] | None = None,
    verdicts: dict[str, str] | None = None,
    model: ModelClient | None = None,
    task: dict[str, Any] | None = None,
    memory: Any = None,
    enforce_writes: bool = True,
    max_steps: int = 2000,
) -> RunResult:
    """Build a context and run the graph once.

    Two things come from the run directory before anything executes, because a mode
    author cannot be the thing that decides whether they happen:

    - The **intent ledger** is folded into every agent node, so a run a person
      steered carries what they said. Inert when there is no ledger.
    - A gate a person answered is **replayed** from the ledger rather than asked
      again. An explicit ``verdicts`` argument still wins, so a caller can override.
    """
    from refactory_dsh.steering import Ledger, for_run

    workdir = Path(workdir)
    graph = for_run(graph, workdir)
    ledger = Ledger(workdir)
    recorded = ledger.verdicts() if ledger.exists() else {}
    ctx = RunContext(
        workdir,
        knobs=knobs,
        facts=facts,
        verdicts={**recorded, **(verdicts or {})},
        model=model,
        task=task,
        memory=memory,
        enforce_writes=enforce_writes,
    )
    return Executor(graph, ctx, max_steps=max_steps).run()
