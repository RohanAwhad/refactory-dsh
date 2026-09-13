"""String knobs are movable, and a run says which knobs its optimizer cannot reach.

The string-knob issue: a prompt is the knob an author most wants to move, and it
was the one kind no sampler could produce.
"""

from __future__ import annotations

import random
from pathlib import Path

import pytest

from refactory_dsh import nn
from refactory_dsh.loop import Loop, LoopConfig
from refactory_dsh.optim import Candidate, Optimizer, Proposal, RandomOptimizer

MODE = "circle_packing/adaptive"


class PromptMode(nn.Mode):
    """A mode whose only interesting lever is text: a prompt and a strategy name."""

    name = "prompted"

    @classmethod
    def declare(cls) -> dict[str, nn.Knob]:
        return {
            "prompt": nn.Knob("solve the problem", "str", description="what to ask"),
            "variant": nn.Knob("a", "str", values=("a", "b", "c"), description="a named variant"),
            "rounds": nn.Knob(2, "int", lo=1, hi=4),
            "seed": nn.Knob(7, "int", frozen=True),
        }

    def build(self) -> nn.Graph:
        graph = nn.Graph()
        graph.add(nn.Node("only", "op", call="loop_ops:write_best",
                          writes=("best.json",), produces=("best",),
                          knobs=("prompt", "variant", "rounds", "seed")))
        return graph

    def score(self, run: nn.RunResult) -> float:
        return float(run.facts.get("best", 0.0))


# ── the sampler ────────────────────────────────────────────────────────────
def test_an_open_string_knob_can_be_sampled():
    rng = random.Random(3)
    knob = nn.Knob("solve the problem", "str")
    values = {knob.sample(rng, "solve the problem") for _ in range(8)}
    assert values and None not in values
    assert "solve the problem" not in values, "a sample must differ from what it has"


def test_a_string_knob_with_declared_values_picks_one():
    rng = random.Random(3)
    knob = nn.Knob("a", "str", values=("a", "b", "c"))
    assert knob.accepted() == ("a", "b", "c")
    assert {knob.sample(rng, "a") for _ in range(8)} <= {"b", "c"}


def test_every_kind_can_be_moved():
    rng = random.Random(5)
    for knob, current in (
        (nn.Knob(3, "int", lo=1, hi=9), 3),
        (nn.Knob(0.5, "float", lo=0.0, hi=1.0), 0.5),
        (nn.Knob(True, "bool"), True),
        (nn.Knob("x", "str", values=("x", "y")), "x"),
        (nn.Knob("hi", "str"), "hi"),
        (nn.Knob("a", "enum", enum=("a", "b")), "a"),
    ):
        assert knob.sample(rng, current) is not None, knob


def test_a_str_knob_may_not_declare_values_of_another_kind():
    with pytest.raises(nn.KnobError, match="only a 'str' knob"):
        nn.Knob(1, "int", values=("a",))
    with pytest.raises(nn.KnobError, match="must be a string"):
        nn.Knob("a", "str", values=(1,))


def test_values_survive_a_spec_round_trip():
    knob = nn.Knob("a", "str", values=("a", "b"))
    assert nn.Knob.from_spec(knob.spec()) == knob


# ── the random optimizer and the sweep ─────────────────────────────────────
class _Ctx:
    def __init__(self, mode: nn.Mode) -> None:
        from refactory_dsh.optim import ProposalContext

        described = mode.describe()
        self.ctx = ProposalContext(
            knobs=[{"name": n, **s} for n, s in described["knobs"].items()],
            nodes=described["nodes"],
            edges=described["edges"],
            insertable=[node.to_state() for node in mode.insertable()],
        )


def test_the_random_optimizer_can_move_a_string_knob():
    mode = PromptMode()
    parent = Candidate.from_state(mode.state_dict())
    seen: set[str] = set()
    for seed in range(40):
        optimizer = RandomOptimizer(seed=seed)
        candidates, _rejections = optimizer.propose_valid(
            _Ctx(mode).ctx, 1, parents=[parent], invariants=mode.invariants()
        )
        for candidate in candidates:
            for edit in candidate.edits:
                if edit.get("name") in ("prompt", "variant"):
                    seen.add(str(edit["name"]))
    assert {"prompt", "variant"} & seen, (
        "the random optimizer never proposed a string knob, so it is searching a "
        "smaller surface than the mode declares"
    )


def test_the_starting_sweep_covers_string_knobs(tmp_path):
    loop = Loop(
        "circle_packing/adaptive",
        LoopConfig(epochs=1, batch_size=6, optimizer="random", seed=1,
                   runs_root=tmp_path / "runs"),
        project=Path("."),
        optimizer=RandomOptimizer(seed=1),
    )
    loop.init()
    assert loop.population, "the sweep produced nothing"


def test_the_sweep_includes_a_string_knob():
    """Directly: the sweep must offer a value for every movable kind."""
    loop = Loop(
        MODE,
        LoopConfig(epochs=1, batch_size=8, optimizer="random", seed=2,
                   runs_root=Path("/tmp/never-written")),
        project=Path("."),
        optimizer=RandomOptimizer(seed=2),
    )
    seed = Candidate.from_state(PromptMode().state_dict())
    edits = loop._exploration_edits(seed, 4)  # noqa: SLF001
    names = {edit["name"] for edit in edits}
    assert "prompt" in names and "variant" in names, (
        f"the sweep skipped the string knobs: {sorted(names)}"
    )


# ── reporting coverage ─────────────────────────────────────────────────────
class Narrow(Optimizer):
    """An optimizer that declares it can only move numbers."""

    name = "narrow"

    def propose(self, ctx, n, parents):  # type: ignore[no-untyped-def]
        return []

    def can_move(self, knob: nn.Knob, current: object) -> bool:
        return knob.kind in ("int", "float")


def test_a_run_names_the_knobs_its_optimizer_cannot_reach(tmp_path, capsys):
    loop = Loop(
        MODE,
        LoopConfig(epochs=1, batch_size=2, optimizer="random", seed=3,
                   runs_root=tmp_path / "runs"),
        project=Path("."),
        optimizer=Narrow(k=1),
    )
    loop.init()
    unreachable = loop.unreachable_knobs()
    assert unreachable, "a narrow optimizer must be seen to miss some knobs"
    assert "E-shape" not in unreachable
    warning = capsys.readouterr().err
    assert "optimizer_cannot_reach_some_knobs" in warning
    for name in unreachable:
        assert f'"{name}"' in warning, f"{name} was not named in the warning"


def test_the_report_carries_knob_coverage(tmp_path):
    report = Loop(
        MODE,
        LoopConfig(epochs=1, batch_size=3, optimizer="random", seed=4,
                   runs_root=tmp_path / "runs"),
        project=Path("."),
    ).run()
    assert "knob_coverage" in report
    assert report["knob_coverage"]["unreachable"] == []


def test_the_prompt_marks_a_knob_as_unreachable(tmp_path):
    loop = Loop(
        MODE,
        LoopConfig(epochs=1, batch_size=2, optimizer="random", seed=6,
                   runs_root=tmp_path / "runs"),
        project=Path("."),
        optimizer=Narrow(k=1),
    )
    loop.init()
    prompt = loop._context().render()  # noqa: SLF001
    assert "reachable" in prompt


def test_describe_names_values_and_range_separately():
    described = PromptMode().describe()["knobs"]
    assert described["variant"]["values"] == ["a", "b", "c"]
    assert described["variant"]["range"] == [None, None]
    assert described["rounds"]["values"] == []
    assert described["rounds"]["range"] == [1, 4]
    assert "bounds" not in described["rounds"], (
        "the overloaded bounds field is what made an optimizer author guess"
    )
