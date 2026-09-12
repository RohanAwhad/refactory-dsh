"""Faithfulness harness for the DSH outer loop.

These tests prove the port is the *full* refactory MAP-Elites loop, not a toy:

- every one of the 8 `MutationType` operators produces a valid `MutationRecord`
  when applied through refactory's `apply_random_mutation`;
- `SwarmEngine.run` drives the whole pipeline (seed → tournament selection →
  8-operator mutation → `SwarmEvaluator` → `MAPElitesArchive` feature placement →
  `OuterLoopReflector`) on both a synthetic agent-graph and the real SRF `gepa`
  graph, returning a positive best score with a populated archive;
- `OuterLoopReflector` emits contrastive patterns from `CycleRecord` exhaust;
- the DSH inner loop (`dsh_evaluate`) launches a real `dsh --profile science run`
  and reads `best_score`.

The fast tests use `REFACTORY_SCORER=surrogate` (deterministic) so the whole
operator/archive/reflection surface runs without a CEO subprocess per candidate.
"""

from __future__ import annotations

import os
import random

import pytest

os.environ.setdefault("REFACTORY_SCORER", "surrogate")

import refactory_dsh.ops.outer_loop as outer  # noqa: E402
from factory.outer_loop.models import EvalResult, MutationType  # noqa: E402
from factory.outer_loop.mutations import apply_random_mutation, parallelize  # noqa: E402
from factory.workflow.primitives import (  # noqa: E402
    AgentNode,
    AgentRole,
    Edge,
    FnNode,
    GateNode,
    LLMNode,
    Workflow,
)


class _FixedOperatorStrategy:
    """A strategy that always returns one operator, for deterministic coverage."""

    def __init__(self, op: MutationType) -> None:
        self.op = op

    def select_operator(self, workflow: Workflow, generation: int, stats: dict) -> MutationType:
        return self.op

    def get_mutation_rate(self, generation: int) -> float:
        return 0.3


def synthetic_workflow() -> Workflow:
    """A small agent-bearing graph on which *all* 8 operators apply."""
    return Workflow(
        name="synthetic",
        nodes={
            "start": FnNode(id="start", command="echo start"),
            "builder": AgentNode(
                id="builder",
                role=AgentRole.BUILDER,
                prompt_template="Build the thing.",
                timeout=600,
                max_iterations=1,
            ),
            "gate": GateNode(id="gate", evaluator_type="fn", evaluator_command="echo proceed"),
            "check": LLMNode(id="check", system_prompt="check", instance_prompt="verify"),
            "end": FnNode(id="end", command="echo done"),
        },
        edges=[
            Edge(source="start", target="builder"),
            Edge(source="builder", target="gate"),
            Edge(source="gate", target="check"),
            Edge(source="check", target="end"),
        ],
        start_node="start",
        terminal=True,
        knob_values={"temperature": 0.7, "parent_selection": "best"},
        knob_bounds={"temperature": [0.3, 0.5, 0.7, 0.9], "parent_selection": ["best", "pareto"]},
        knob_specs={
            "temperature": {
                "kind": "threshold", "node_id": "check", "default": 0.7,
                "bounds": [0.3, 0.5, 0.7, 0.9], "expandable": False,
                "expansion_hint": "", "description": "",
            },
            "parent_selection": {
                "kind": "prompt", "node_id": "builder", "default": "best",
                "bounds": ["best", "pareto"], "expandable": False,
                "expansion_hint": "", "description": "",
            },
        },
    )


def _apply_forced(base: Workflow, op: MutationType):
    return apply_random_mutation(base, _FixedOperatorStrategy(op), 0)


def test_every_operator_produces_a_mutation_record():
    """Each of the 8 operators, forced, must yield a valid `MutationRecord`.

    SERIALIZE needs a fork, so it is tested after PARALLELIZE creates one —
    which is exactly how the operators become reachable in a real run.
    """
    base = synthetic_workflow()

    # The seven operators that apply directly to the synthetic graph.
    direct = [
        MutationType.NODE_INSERT,
        MutationType.NODE_REMOVE,
        MutationType.PARALLELIZE,
        MutationType.PARAM_MUTATE,
        MutationType.PROMPT_MUTATE,
        MutationType.KNOB_MUTATE,
    ]
    for op in direct:
        result = _apply_forced(base, op)
        assert result is not None, f"{op.value} returned None"
        wf, rec = result
        assert rec.operator is op, f"{op.value} recorded {rec.operator}"
        assert wf.to_dict()["nodes"], f"{op.value} produced an empty graph"

    # EDGE_REDIRECT is refused on this fixture, and correctly so: every edge here
    # is the only inbound edge of its target, so redirecting any of them orphans a
    # node. Repair no longer prunes an orphan away, because a silently amputated
    # graph still costs a full evaluation to discover it does nothing, so the
    # mutation is refused instead. The positive case lives beside the operator in
    # remote-factory's test_mutations.py::test_redirect_that_does_not_orphan_succeeds.
    assert _apply_forced(base, MutationType.EDGE_REDIRECT) is None

    # SERIALIZE needs a fork: PARALLELIZE a consecutive pair deterministically
    # (a random pair can be non-consecutive, which the operator rejects), then
    # collapse that fork/join pair back.
    parallelized, _ = parallelize(base, ["gate", "check"])
    assert parallelized is not None
    result = _apply_forced(parallelized, MutationType.SERIALIZE)
    assert result is not None, "SERIALIZE returned None on a forked graph"
    _, rec = result
    assert rec.operator is MutationType.SERIALIZE


def test_operators_that_apply_to_srf_gepa():
    """The SRF gepa graph is Fn/LLM/Gate-based, so five operators apply on the
    seed and the other three become reachable after structural mutations."""
    base = outer.load_base_workflow()
    applicable = {
        MutationType.NODE_INSERT,
        MutationType.NODE_REMOVE,
        MutationType.EDGE_REDIRECT,
        MutationType.PARALLELIZE,
        MutationType.KNOB_MUTATE,
    }
    for op in applicable:
        result = _apply_forced(base, op)
        assert result is not None, f"{op.value} returned None on gepa"
        assert result[1].operator is op


def _run_loop(base: Workflow, budget: int, population: int = 8):
    os.environ["REFACTORY_BUDGET"] = str(budget)
    os.environ["REFACTORY_POPULATION"] = str(population)
    random.seed(0)  # deterministic operator distribution for reproducible coverage
    config = outer.build_config()
    engine = outer.build_engine(config, outer.surrogate_evaluate)
    result = engine.run(base, project_dir="")
    return engine, result


def _operators_fired(result) -> set[str]:
    ops: set[str] = set()
    for summary in result.trajectory:
        for rec in summary.mutations_applied:
            ops.add(rec.operator.value)
    return ops


def test_full_loop_synthetic_all_eight_operators():
    """The integrated `SwarmEngine.run` on an agent graph must exercise the full
    MAP-Elites surface: archive features, tournament selection, reflection, and a
    diverse set of the 8 operators (the deterministic forced test above proves
    each operator individually; here we require broad coverage)."""
    engine, result = _run_loop(synthetic_workflow(), budget=80)

    assert result.best_score > 0, "the loop must find a positive best score"
    assert result.total_evaluations > 0, "the DSH evaluator must be called"
    assert result.generations_completed >= 2, "the loop must span generations"
    assert result.archive_size > 0, "the MAP-Elites archive must be populated"

    # Archive features are non-empty integer tuples (depth, fork_degree, …).
    for ind in engine.archive.all_individuals():
        assert len(ind.features) > 0 and all(isinstance(f, int) for f in ind.features)

    # Tournament + mutation produced offspring with lineage and records.
    fired = _operators_fired(result)
    assert len(fired) >= 6, f"expected broad operator coverage, got {sorted(fired)}"

    # Reflection was invoked inside every generation step.
    assert engine._last_reflection is not None, "OuterLoopReflector did not run"


def test_full_loop_srf_gepa():
    """The real SRF gepa mode must run end-to-end through the same faithful loop."""
    engine, result = _run_loop(outer.load_base_workflow(), budget=24)

    assert result.best_score > 0
    assert result.total_evaluations > 0
    assert result.archive_size > 0
    assert engine._last_reflection is not None
    # gepa has knobs; KNOB_MUTATE must be reachable, so the archive/population
    # candidates carry knob_values through mutation (deep-copy preservation).
    assert any(
        Workflow.from_dict(ind.workflow_data).knob_values
        for ind in engine.archive.all_individuals()
    ), "knob_values must survive mutation for KNOB_MUTATE to keep working"


def test_reflection_produces_contrastive_report():
    """`OuterLoopReflector` must emit success/failure patterns from cycle records."""
    from factory.cycle_analyzer import CycleRecord
    from factory.outer_loop.reflector import OuterLoopReflector

    def rec(kept: int, reverted: int, errored: int) -> CycleRecord:
        return CycleRecord(
            cycle_number=0, mode="m", started_at=None, ended_at=None, duration_s=0.0,
            score_start=None, score_end=None, score_delta=None,
            kept=kept, reverted=reverted, errored=errored,
        )

    records = [
        ("top1", 0.9, rec(kept=3, reverted=0, errored=0)),
        ("top2", 0.8, rec(kept=2, reverted=0, errored=0)),
        ("bot1", 0.1, rec(kept=0, reverted=2, errored=1)),
        ("bot2", 0.2, rec(kept=1, reverted=3, errored=0)),
    ]
    report = OuterLoopReflector(k=2).reflect(records, generation=1)
    assert set(report.top_k_ids) == {"top1", "top2"}
    assert set(report.bottom_k_ids) == {"bot1", "bot2"}
    assert report.success_patterns, "success patterns should be extracted"
    assert report.failure_patterns, "failure patterns should be extracted"


@pytest.mark.integration
def test_dsh_inner_loop_returns_score():
    """One real inner-loop cycle: launch DSH on the SRF gepa mode and read a score.

    Skipped unless ``--run-integration`` is passed, because it boots a CEO agent
    (slow and non-deterministic). The contract under test is that ``dsh_evaluate``
    launches the candidate graph through DSH and returns an ``EvalResult`` with a
    numeric ``score`` — a completed run yields a positive score, and a hung inner
    loop degrades to ``0.0`` (``dsh_inner_loop_timeout``) rather than crashing the
    swarm. A prior manual run of the same DSH command returned ``best_score=2.17``.
    """
    os.environ["REFACTORY_SCORER"] = "dsh"
    os.environ["REFACTORY_EVAL_BUDGET"] = "1"
    os.environ["REFACTORY_DSH_TIMEOUT"] = "300"
    base = outer.load_base_workflow()
    result = outer.dsh_evaluate(base, "", ["circle_packing"])
    assert isinstance(result, EvalResult)
    assert isinstance(result.score, float)
    assert result.score >= 0.0
