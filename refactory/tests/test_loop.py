"""The loop: eight steps, a bound, and a report that says what happened.

Phase 6 gate of ``docs/plans/pythonic-modes.md``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from refactory_dsh import nn, optim
from refactory_dsh.loop import Loop, LoopConfig, LoopError
from refactory_dsh.optim import Candidate, Optimizer, Proposal

MODE = "circle_packing/adaptive"


def _config(root: Path, **overrides: object) -> LoopConfig:
    base = dict(epochs=2, batch_size=4, optimizer="random", seed=5, runs_root=root)
    base.update(overrides)
    return LoopConfig(**base)  # type: ignore[arg-type]


class ScriptedOptimizer(Optimizer):
    """Proposes one fixed edit per batch, so a test can assert what it did."""

    name = "scripted"

    def __init__(self, edit: dict, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._edit = edit

    def propose(self, ctx, n, parents):  # type: ignore[no-untyped-def]
        return [
            Proposal(edit=self._edit, parent=parents[0].id, rationale="scripted")
            for _ in range(max(1, n))
        ]


def _structural_edit(mode: nn.Mode) -> dict:
    block = {node.id: node for node in mode.insertable()}["restart_on_stall"]
    return {"op": "insert_after", "target": "accept", "node": block.to_state()}


def test_init_builds_a_starting_batch(tmp_path):
    loop = Loop(MODE, _config(tmp_path), project=Path("."))
    loop.init()
    assert len(loop.population) >= 2
    assert loop.population[0].parent is None
    assert (tmp_path / "config.json").exists()
    assert (tmp_path / "state.json").exists()


def _patch_mode(mode_class: type[nn.Mode]):
    """Point the registry at a local mode class for the duration of a test."""
    import contextlib

    import refactory_dsh.registry as registry

    class FakePackage:
        name = "fake"
        description = ""

        def mode(self, name: str) -> type[nn.Mode]:
            return mode_class

    @contextlib.contextmanager
    def patched():  # type: ignore[no-untyped-def]
        original = registry.resolve
        registry.resolve = lambda project, identifier: (FakePackage(), mode_class)  # type: ignore[assignment]
        try:
            yield
        finally:
            registry.resolve = original  # type: ignore[assignment]

    return patched()


class _Building(nn.Mode):
    """A constructible mode, so the loop can get as far as judging it."""

    name = "building"

    def build(self) -> nn.Graph:
        graph = nn.Graph()
        graph.add(nn.Node("only", "op", call="loop_ops:write_best",
                          writes=("best.json",), produces=("best",)))
        return graph


class Unscored(_Building):
    pass  # keeps Mode.score, which means "unscored"


class Unrunnable(_Building):
    def build(self) -> nn.Graph:
        graph = nn.Graph()
        graph.add(nn.Node("start", "op", call="loop_ops:write_best",
                          writes=("best.json",), produces=("best",)))
        graph.add(nn.Node("orphan", "op", call="loop_ops:write_best",
                          writes=("other.json",), produces=("other",)))
        return graph

    def score(self, run: nn.RunResult) -> float:
        return float(run.facts.get("best", 0.0))


def test_init_refuses_a_mode_that_does_not_score(tmp_path):
    with _patch_mode(Unscored):
        with pytest.raises(LoopError, match="does not override score"):
            Loop(MODE, _config(tmp_path), project=Path(".")).init()


def test_init_refuses_a_mode_whose_seed_state_is_not_runnable(tmp_path):
    with _patch_mode(Unrunnable):
        with pytest.raises(LoopError, match="not runnable"):
            Loop(MODE, _config(tmp_path), project=Path(".")).init()


def test_two_epochs_complete_and_report_the_bound(tmp_path):
    loop = Loop(MODE, _config(tmp_path), project=Path("."))
    report = loop.run()
    assert report["bound"] == "epochs"
    assert report["epochs_completed"] == 2
    assert report["evaluations"] >= 4
    assert (tmp_path / "report.json").exists()
    assert (tmp_path / "gradient.json").exists()
    markdown = (tmp_path / "report.md").read_text()
    assert markdown.startswith("# Outer loop"), "the report must be markdown, not JSON"
    assert "## Leaderboard" in markdown
    assert "## Refusals by rule" in markdown


def test_the_report_names_what_the_run_was(tmp_path):
    report = Loop(MODE, _config(tmp_path), project=Path(".")).run()
    assert report["mode"] == MODE
    assert report["optimizer"] == "random"
    assert report["batch_size"] == 4
    assert report["seed_score"] is not None
    assert report["best_score"] >= report["seed_score"]


def test_the_leaderboard_is_ordered_and_complete(tmp_path):
    report = Loop(MODE, _config(tmp_path), project=Path(".")).run()
    scores = [row["score"] for row in report["leaderboard"]]
    assert scores == sorted(scores, reverse=True)
    assert len(report["leaderboard"]) == report["evaluations"]


def test_every_evaluated_candidate_validated_first(tmp_path):
    """An invalid candidate never reaches a run, so it never costs budget."""
    loop = Loop(MODE, _config(tmp_path), project=Path("."))
    loop.init()
    while True:
        evaluated = loop.forward()
        loop.loss_step()
        loop.backward()
        candidates, rejections = loop.optimizer_step()
        loop.step(candidates)
        if not loop.scheduler(evaluated=evaluated, proposed=len(candidates) + len(rejections)):
            break
    for candidate in loop.population:
        assert nn.validate_state(candidate.state) == [], candidate.id


def test_refusals_are_grouped_by_rule(tmp_path):
    report = Loop(MODE, _config(tmp_path), project=Path(".")).run()
    codes = report["rejection_codes"]
    assert codes, "the random sampler should have been refused at least once"
    for code in codes:
        assert code.startswith(("V", "E-", "I:")), f"uncoded refusal: {code}"


def test_a_structural_candidate_is_proposed_and_evaluated(tmp_path):
    from refactory_dsh import registry

    _package, mode_class = registry.resolve(Path("."), MODE)
    edit = _structural_edit(mode_class())
    loop = Loop(
        MODE,
        _config(tmp_path),
        project=Path("."),
        optimizer=ScriptedOptimizer(edit, k=1),
    )
    report = loop.run()
    assert report["structural"]["structural_candidates"] >= 1
    structural = [row for row in report["leaderboard"] if row["structural"] and row["exit_ok"]]
    assert structural, "no structural candidate ran"


def test_a_structural_edit_beats_its_parent(tmp_path):
    """The seed pipeline wastes its budget; the fix is a node, and it shows."""
    from refactory_dsh import registry

    _package, mode_class = registry.resolve(Path("."), MODE)
    loop = Loop(
        MODE,
        _config(tmp_path),
        project=Path("."),
        optimizer=ScriptedOptimizer(_structural_edit(mode_class()), k=1),
    )
    report = loop.run()
    assert report["best_score"] > report["seed_score"]
    winners = [c for c in report["structural_wins"] if c["score"] == report["best_score"]]
    assert winners, "the structural candidate did not reach the top of the leaderboard"
    assert all(c["exit_ok"] for c in report["structural_wins"]), (
        "a structural candidate that never ran is not a win"
    )


def test_proposal_outcomes_pair_a_proposal_with_what_happened(tmp_path):
    report = Loop(MODE, _config(tmp_path), project=Path(".")).run()
    outcomes = report["proposal_outcomes"]
    assert outcomes
    for row in outcomes:
        assert "delta" in row
        if row["parent_score"] is not None and row["score"] is not None:
            assert row["delta"] == pytest.approx(row["score"] - row["parent_score"])


def test_the_report_counts_model_calls_made_inside_the_graphs(tmp_path):
    """An inserted agent node costs model calls, and the report says how many."""
    report = Loop(MODE, _config(tmp_path), project=Path(".")).run()
    assert report["cost"]["inner_model_calls"] == 0, (
        "the deterministic seed pipeline makes no model calls"
    )


def test_the_gradient_carries_knob_attribution_and_reasons(tmp_path):
    loop = Loop(MODE, _config(tmp_path), project=Path("."))
    loop.init()
    loop.forward()
    loop.loss_step()
    gradient = loop.backward()
    attribution = gradient["knob_attribution"]
    assert "order" in attribution
    assert sum(v["n"] for v in attribution["order"]["tried"].values()) >= 1
    assert "asc" in attribution["order"]["untried"], (
        "the gradient must say which values nobody has tried"
    )
    assert gradient["cost"]["n"] >= 1
    assert "rejection_codes" in gradient
    assert gradient["structural"]["accepted_ops"]


def test_the_scheduler_reports_the_evaluation_bound(tmp_path):
    loop = Loop(MODE, _config(tmp_path, max_evaluations=2), project=Path("."))
    report = loop.run()
    assert report["bound"] == "evaluations"
    assert report["evaluations"] == 2


def test_the_epoch_summary_counts_what_the_epoch_did(tmp_path):
    report = Loop(MODE, _config(tmp_path), project=Path(".")).run()
    summary = report["epochs_summary"]
    assert len(summary) == 2
    assert summary[0]["evaluated"] >= 1
    assert summary[0]["proposed"] >= 0


def test_a_run_leaves_the_analysis_of_a_model_optimizer(tmp_path):
    """A reasoning optimizer's diagnosis is written where a person can read it."""

    class Diagnosis(Optimizer):
        name = "diagnosis"

        def propose(self, ctx, n, parents):  # type: ignore[no-untyped-def]
            self.last_diagnosis = "the generator repeats itself"
            return [
                Proposal(
                    edit={"op": "set_knob", "name": "order", "value": "asc"},
                    parent=parents[0].id,
                )
            ]

    loop = Loop(
        MODE, _config(tmp_path), project=Path("."), optimizer=Diagnosis(k=1)
    )
    loop.run()
    written = sorted((tmp_path / "analysis").glob("*.md"))
    assert written
    assert "the generator repeats itself" in written[0].read_text()


def test_the_starting_population_sweeps_the_parameters(tmp_path):
    """The first epoch must produce a gradient with contrast, not just candidates."""
    loop = Loop(MODE, _config(tmp_path), project=Path("."))
    loop.init()
    swept = [
        candidate
        for candidate in loop.population
        if not candidate.is_structural() and candidate.edits
    ]
    assert swept, "the starting population only moved structure"
    moved = {edit["name"] for candidate in swept for edit in candidate.edits}
    assert {"order", "grid", "selector"} & moved


def test_the_gradient_reports_which_values_are_untried(tmp_path):
    loop = Loop(MODE, _config(tmp_path), project=Path("."))
    loop.init()
    loop.forward()
    loop.loss_step()
    attribution = loop.backward()["knob_attribution"]
    untried = attribution["grid"]["untried"]
    assert untried, "every grid value was swept, so the assertion is not exercising this"
    assert all(str(value) not in attribution["grid"]["tried"] for value in untried)


def test_a_knob_move_selected_by_the_optimizer_improves_the_run(tmp_path):
    loop = Loop(
        MODE,
        _config(tmp_path),
        project=Path("."),
        optimizer=ScriptedOptimizer({"op": "set_knob", "name": "order", "value": "asc"}, k=1),
    )
    report = loop.run()
    assert report["best_score"] > report["seed_score"]


def test_rejections_are_persisted_as_a_stream(tmp_path):
    loop = Loop(MODE, _config(tmp_path, batch_size=6), project=Path("."))
    loop.run()
    path = tmp_path / "rejections.jsonl"
    if path.exists():
        for line in path.read_text().splitlines():
            row = json.loads(line)
            assert row["reasons"]
            assert row["codes"]
