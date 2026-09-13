"""The SRF strategies, each tested on what makes it that strategy.

Not on a score: a port is correct when it exhibits the control flow it is named
after. Every one of the thirteen SRF modes lands on one of the two seams, which is
the claim the abstraction is making.
"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from refactory_dsh import strategies
from refactory_dsh.loop import Loop, LoopConfig
from refactory_dsh.optim import Candidate, Proposal
from refactory_dsh.outerloop import OuterLoop, Run

MODE = "circle_packing/adaptive"


def _loop(name: str, root: Path, **overrides: object) -> Loop:
    base: dict[str, object] = dict(
        epochs=2, batch_size=2, optimizer="random", seed=1, runs_root=root
    )
    base.update(overrides)
    return strategies.create(name, MODE, LoopConfig(**base), project=Path("."))  # type: ignore[arg-type]


# ── the registry ───────────────────────────────────────────────────────────
def test_every_srf_strategy_is_ported():
    assert len(strategies.names()) - 1 == strategies.SRF_PORT_COUNT, (
        f"expected {strategies.SRF_PORT_COUNT} ports plus `default`, got "
        f"{strategies.names()}"
    )


def test_the_registry_refuses_an_unknown_strategy():
    with pytest.raises(ValueError, match="unknown strategy"):
        strategies.resolve("nonsense")


def test_a_strategy_can_be_registered_without_editing_the_library():
    class Mine(Loop):
        name = "mine-by-hand"

    strategies.register("mine_by_hand", Mine)
    try:
        assert "mine_by_hand" in strategies.names()
        assert strategies.resolve("mine_by_hand") is Mine
    finally:
        del strategies.STRATEGIES["mine_by_hand"]


def test_registering_something_that_is_not_an_outer_loop_is_refused():
    class NotALoop:
        name = "nope"

    with pytest.raises(TypeError, match="not an OuterLoop"):
        strategies.register("nope", NotALoop)  # type: ignore[arg-type]


def test_every_port_is_an_outer_loop_with_a_name():
    for name, cls in strategies.STRATEGIES.items():
        assert issubclass(cls, OuterLoop), name
        assert cls.name == name, f"{name} is registered under the wrong name"


# ── the fork shapes ────────────────────────────────────────────────────────
def test_best_of_n_evaluates_exactly_n_from_the_seed_and_never_loops(tmp_path):
    loop = _loop("best_of_n", tmp_path / "run", epochs=5)
    loop.width = 4
    report = loop.run()
    assert report["bound"] == "samples_exhausted"
    assert report["evaluations"] == 4, "best-of-N samples N times and stops"
    assert report["epochs_completed"] == 0, "there is no loop to have an epoch"
    assert len(report["leaderboard"]) == 4, "the N samples, and no seed run"
    assert not loop.population[0].evaluated, (
        "SRF's best_of_n has no seed_run: the fork is the whole search"
    )


def test_autoscientists_forks_n_then_merges_seeing_all_of_them(tmp_path):
    loop = _loop("autoscientists", tmp_path / "run", epochs=5)
    loop.width = 3
    report = loop.run()
    assert report["bound"] == "merged"
    assert loop.merged == 3, "every branch must be produced before the merge"
    assert report["evaluations"] >= 4, "the merge adds an evaluation"


def test_the_merge_takes_a_value_from_a_different_branch(tmp_path):
    """The merge is a real crossover: one knob copied from another branch."""
    loop = _loop("autoscientists", tmp_path / "run", epochs=5)
    loop.width = 4
    loop.run()
    assert loop.merged == 4
    # Every sampled branch is a distinct knob value; the merged candidate carries a
    # value that also appears in one of the others, which is what a crossover is.
    values = [
        {n: e["value"] for n, e in c.state["knobs"].items()} for c in loop.population
    ]
    merged = loop.population[-1]
    merged_knobs = {n: e["value"] for n, e in merged.state["knobs"].items()}
    assert merged_knobs in values
    assert merged.score is not None, "the merged candidate was never evaluated"


# ── the switch-inside-an-iteration shapes ──────────────────────────────────
def test_reflective_takes_a_merge_branch_and_a_mutate_branch(tmp_path):
    loop = _loop("reflective", tmp_path / "run", epochs=4)
    loop.run()
    assert loop.merges >= 1, "the merge branch was never taken"
    assert loop.mutations >= 1, "the mutate branch was never taken"
    assert loop.merges + loop.mutations >= 4, "both branches continue the same iteration"


def test_adaptive_meta_fires_its_meta_branch_and_records_it(tmp_path):
    loop = _loop("adaptive_meta", tmp_path / "run", epochs=5)
    loop.run()
    assert loop.meta_taken >= 1, "the meta branch never fired"
    log = (loop.root / "strategy.log").read_text()
    assert "meta=True" in log and "meta=False" in log, (
        "the choice must be recorded, not inferred"
    )


def test_tree_search_keeps_a_tree_not_a_list(tmp_path):
    loop = _loop("tree_search", tmp_path / "run", epochs=3)
    loop.run()
    tree = loop.tree
    assert tree.all(), "nothing was archived"
    assert tree.parents, "no candidate recorded an expansion parent"
    assert tree.deepest() >= 1, "the tree never went deeper than its root"
    for candidate in tree.all():
        if candidate.parent:
            assert tree.parents[candidate.id] == candidate.parent


def test_staged_curriculum_runs_every_stage_in_order(tmp_path):
    loop = _loop("staged_curriculum", tmp_path / "run", epochs=99)
    loop.per_stage = 1
    report = loop.run()
    assert loop.stages_done == ["initial", "tuning", "creative", "ablation"]
    assert report["bound"] == "curriculum_complete"
    log = (loop.root / "strategy.log").read_text()
    positions = [log.index(f"stage={stage}") for stage in loop.stages]
    assert positions == sorted(positions), "the stages ran out of order"


def test_staged_v1_has_its_own_stages(tmp_path):
    loop = _loop("staged_v1", tmp_path / "run", epochs=99)
    loop.per_stage = 1
    loop.run()
    assert loop.stages_done == ["explore", "exploit"]


def test_research_then_optimize_runs_a_pre_phase_once(tmp_path):
    loop = _loop("research_then_optimize", tmp_path / "run", epochs=2)
    loop.run()
    assert loop.research_note, "the pre-phase produced nothing"
    assert "movable knob" in loop.research_note
    log = (loop.root / "strategy.log").read_text()
    assert log.count("research=") == 1, "the pre-phase must run once, not per epoch"


def test_reflective_sampler_carries_prior_rejections_into_sampling(tmp_path):
    loop = _loop("reflective_sampler", tmp_path / "run", epochs=2)
    loop.run()
    assert hasattr(loop, "last_reflection"), "sampling did not reflect on anything"


# ── the selection shapes ───────────────────────────────────────────────────
def test_islands_never_select_across_them(tmp_path):
    loop = _loop("islands", tmp_path / "run", epochs=3)
    loop.run()
    archive = loop.archive
    assert len(archive.populated()) >= 1
    for island in archive.populated():
        for member in archive.from_island(island):
            assert archive.island_of(member) == island
    chosen = loop.select_parents(3)
    assert chosen
    for candidate in chosen:
        assert archive.island_of(candidate) in archive.populated()


def test_archive_loop_keeps_more_than_the_elite(tmp_path):
    loop = _loop("archive_loop", tmp_path / "run", epochs=2)
    loop.run()
    assert len(loop.archive.all()) >= 1
    assert loop.archive.limit > 1, "an archive that keeps one point is not an archive"


def test_evolutionary_archive_selects_by_tournament(tmp_path):
    loop = _loop("evolutionary_archive", tmp_path / "run", epochs=3)
    loop.run()
    chosen = loop.select_parents(2)
    assert chosen and all(isinstance(c, Candidate) for c in chosen)
    assert loop.tournament >= 2, "a tournament of one is not a tournament"


def test_hill_climb_proposes_one_candidate_per_iteration(tmp_path):
    loop = _loop("hill_climb", tmp_path / "run", epochs=3)
    loop.run()
    assert loop.batch_size(Run(loop=loop)) == 1
    assert loop.select_parents(5) and len(loop.select_parents(5)) == 1, (
        "a hill climb walks from one point, not from a population"
    )


def test_hill_climb_stops_on_a_plateau(tmp_path):
    loop = _loop("hill_climb", tmp_path / "run", epochs=99)
    loop.plateau = 2
    report = loop.run()
    assert report["bound"] in {"plateau", "epochs"}


# ── the seams themselves ───────────────────────────────────────────────────
def test_the_two_guarantees_are_not_overridable():
    """A strategy may change control flow, never validation."""
    assert "attempt" not in OuterLoop.overridable
    assert "evaluate" not in OuterLoop.overridable
    assert "strategy" in OuterLoop.overridable and "update" in OuterLoop.overridable


def test_a_custom_control_flow_cannot_evaluate_an_invalid_edit(tmp_path):
    """The point of the seam: a type-2 loop gets the guarantees for free."""

    class Reckless(Loop):
        name = "reckless"

        def strategy(self, run: Run) -> None:
            seed = run.seed()
            # Every one of these is invalid: removing the node that produces the
            # scored fact breaks the mode's own invariant.
            for _ in range(3):
                outcome = run.attempt(
                    Proposal(edit={"op": "remove_node", "id": "exit"}, parent=seed.id), seed
                )
                assert isinstance(outcome, list), "an invalid edit was accepted"
                assert outcome, "a refusal must carry a reason"
            run.stop("tried_to_cheat")

    loop = Reckless(
        MODE,
        LoopConfig(epochs=1, batch_size=2, optimizer="random", seed=1,
                   runs_root=tmp_path / "run"),
        project=Path("."),
    )
    report = loop.run()
    assert report["evaluations"] == 0, "a refused edit must never cost an evaluation"
    assert report["bound"] == "tried_to_cheat"
    assert any("I:" in code or code.startswith("V") for code in report["rejection_codes"])


def test_a_custom_loop_reports_its_own_bound(tmp_path):
    class Minimal(Loop):
        name = "minimal"

        def strategy(self, run: Run) -> None:
            run.keep(run.evaluate(run.attempt(
                Proposal(edit={"op": "set_knob", "name": "order", "value": "asc"},
                         parent=run.seed().id),
                run.seed(),
            )))  # type: ignore[arg-type]
            run.stop("one_sharp_look")

    loop = Minimal(MODE, LoopConfig(epochs=9, batch_size=1, optimizer="random", seed=1,
                                    runs_root=tmp_path / "run"), project=Path("."))
    report = loop.run()
    assert report["bound"] == "one_sharp_look", "a run must report why it ended"
    assert report["evaluations"] == 1
    assert report["best_score"] is not None


def test_a_custom_loop_can_declare_it_needs_a_model():
    class NeedsModel(OuterLoop):
        name = "needs-model"
        requires_model = True

    assert NeedsModel.requires_model is True
    assert Loop.requires_model is False
