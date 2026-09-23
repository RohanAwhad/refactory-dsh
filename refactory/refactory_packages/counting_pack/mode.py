"""The two counting modes: ``random_walk`` and ``fixed_step``.

Both count up from zero toward a target, one increment per visit to the ``step``
node, and both score how close the count the run reached lands to the target:

    start -> step -> check
               ^        |
               | retry  |
               +--------+
                        | proceed
                        v
                     finish

The two modes differ in exactly two declared facts: which step operation the
``step`` node calls, and whether the ``seed`` knob is declared. ``random_walk``
calls ``step_random`` and declares ``seed``; ``fixed_step`` calls ``step_fixed``
and declares no seed, so it has no RNG and no frozen knob.
"""

from __future__ import annotations

from refactory_dsh import nn

#: The module every call and evaluator names.
OPS = "refactory_packages.counting_pack.ops"

#: The files the pipeline reads and writes, named once so a node's declarations
#: and a port's path cannot drift apart.
STATE = "state.json"
TRACE = "trace.json"
RESULT = "result.json"


def _target_knob() -> nn.Knob:
    return nn.Knob(
        5, "int", lo=1, hi=9,
        description="the count the loop stops at; the gate proceeds once the count reaches it",
    )


def _step_knob() -> nn.Knob:
    return nn.Knob(
        2, "int", lo=1, hi=4,
        description="the step size: the largest increment one random_walk step may add, "
                    "and the exact increment every fixed_step step adds",
    )


def _seed_knob() -> nn.Knob:
    return nn.Knob(
        7, "int", frozen=True,
        description="the per-step RNG seed for random_walk; frozen so a run reproduces",
    )


def _pipeline(*, step_call: str, step_knobs: tuple[str, ...]) -> nn.Graph:
    """The one four-node pipeline both modes run.

    ``step_call`` names the mode's step operation and ``step_knobs`` the knobs its
    ``step`` node consumes; everything else in the graph is identical.
    """
    graph = nn.Graph()
    graph.add(nn.Node(
        "start", "op", call=f"{OPS}:start",
        knobs=("target",),
        writes=(STATE, TRACE),
        produces=("value", "steps"),
        notes="open the count at zero, record the target the gate stops at, and "
              "begin the empty trace; the same op serves both modes",
    ))
    graph.add(nn.Node(
        "step", "op", call=step_call,
        knobs=step_knobs,
        reads=(STATE, TRACE),
        writes=(STATE, TRACE),
        produces=("value", "steps"),
        notes="add one increment at this step index and append it to the trace",
    ))
    graph.add(nn.Node(
        "check", "gate", evaluator=f"{OPS}:check",
        reads=(STATE,),
        produces=("done",),
        max_iterations=32,
        notes="retry while the count is below the target, then proceed to the result",
    ))
    graph.add(nn.Node(
        "finish", "op", call=f"{OPS}:finish",
        reads=(STATE,),
        writes=(RESULT,),
        produces=("final_count", "steps_taken", "closeness"),
        notes="publish the count the run reached, the number of steps it took, and "
              "closeness = 1 / (1 + abs(final_count - target))",
    ))
    graph.connect("start", "step")
    graph.connect("step", "check")
    graph.connect("check", "step", condition="retry")
    graph.connect("check", "finish", condition="proceed")
    return graph


def _insertable_trace() -> nn.Node:
    """The one block a structural edit may add."""
    return nn.Node(
        "record_trace", "op", call=f"{OPS}:record_trace",
        reads=(TRACE,),
        writes=("score_trace.json",),
        produces=("score_trace_steps", "score_trace_spread"),
        score_effect="none",
        notes="fold the per-step counts into a score trace; observable, but it "
              "does not change the count and therefore cannot move the score",
    )


class RandomWalk(nn.Mode):
    """Count up to the target in reproducible random steps."""

    name = "random_walk"
    description = "count up to a target in reproducible random steps, one draw per step"

    @classmethod
    def declare(cls) -> dict[str, nn.Knob]:
        return {"target": _target_knob(), "step": _step_knob(), "seed": _seed_knob()}

    def build(self) -> nn.Graph:
        return _pipeline(
            step_call=f"{OPS}:step_random",
            step_knobs=("step", "seed"),
        )

    def score(self, run: nn.RunResult) -> float:
        """How close the count the run reached landed to the target."""
        return float(run.facts["closeness"])

    def invariants(self) -> tuple[nn.Invariant, ...]:
        """The number ``score()`` reads must be produced by the pipeline the run takes."""
        return (nn.fact_produced("final_count", "steps_taken", "closeness"),)

    def guidance(self) -> str:
        """What the traces would show only after a batch had been spent finding it out."""
        return (
            "`step` is the lever: it caps each random increment, so a larger `step` "
            "reaches the target in fewer iterations but raises the chance of "
            "overshooting and scoring below 1. `step = 1` walks one unit at a time and "
            "always lands exactly on the target. `seed` is frozen, so the run reproduces."
        )

    def preferred_edits(self) -> tuple[str, ...]:
        return ("set_knob", "insert_after")

    def insertable(self) -> tuple[nn.Node, ...]:
        return (_insertable_trace(),)


class FixedStep(nn.Mode):
    """Count up to the target in fixed steps."""

    name = "fixed_step"
    description = "count up to a target in fixed steps; no RNG, so no seed knob"

    @classmethod
    def declare(cls) -> dict[str, nn.Knob]:
        return {"target": _target_knob(), "step": _step_knob()}

    def build(self) -> nn.Graph:
        return _pipeline(step_call=f"{OPS}:step_fixed", step_knobs=("step",))

    def score(self, run: nn.RunResult) -> float:
        """How close the count the run reached landed to the target."""
        return float(run.facts["closeness"])

    def invariants(self) -> tuple[nn.Invariant, ...]:
        """The number ``score()`` reads must be produced by the pipeline the run takes."""
        return (nn.fact_produced("final_count", "steps_taken", "closeness"),)

    def guidance(self) -> str:
        """What the traces would show only after a batch had been spent finding it out."""
        return (
            "`step` is the lever: the count overshoots the target whenever `step` does "
            "not divide it, so moving `step` moves `closeness` directly. `step = 1` "
            "always lands exactly on the target. This mode has no RNG, so there is no "
            "seed knob."
        )

    def preferred_edits(self) -> tuple[str, ...]:
        return ("set_knob", "insert_after")

    def insertable(self) -> tuple[nn.Node, ...]:
        return (_insertable_trace(),)
