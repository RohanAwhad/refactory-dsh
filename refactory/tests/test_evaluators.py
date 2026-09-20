"""The evaluator seam: the loop's loss, and what makes the two agree.

The audit that produced this file: two of the loop's three extension points had a
base class and a registry, and the third -- how a mode is run to get the score the
loop learns from -- was a string compared against a literal inside `_evaluate`. It
worked for exactly two and neither could be tested against a shared contract.

These assert the seam rather than the two implementations, because the point of a
seam is what can be plugged into it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from refactory_dsh import evaluators
from refactory_dsh.evaluators import Evaluator, RunResult, WalkerEvaluator
from refactory_dsh.loop import Loop, LoopConfig

ROOT = Path(__file__).resolve().parent.parent.parent
MODE = "circle_packing/adaptive"


def _config(tmp_path: Path, **overrides: object) -> LoopConfig:
    # `random` rather than the default: these test how a mode is *run*, and the
    # model-backed proposer would need a client for reasons that have nothing to
    # do with the evaluator.
    base = {
        "epochs": 1,
        "batch_size": 1,
        "seed": 5,
        "optimizer": "random",
        "runs_root": tmp_path / "run",
    }
    base.update(overrides)
    return LoopConfig(**base)  # type: ignore[arg-type]


# ── the registry ───────────────────────────────────────────────────────────
def test_the_two_that_ship_are_registered_by_name():
    assert set(evaluators.names()) >= {"walker", "dsh"}


def test_an_unknown_evaluator_is_refused_loudly(tmp_path):
    """A silent fallback would make the report lie about how candidates were run."""
    with pytest.raises(evaluators.EvaluatorError, match="unknown evaluator"):
        evaluators.create("nonsense", config=_config(tmp_path))


def test_the_loop_refuses_an_unknown_evaluator(tmp_path):
    with pytest.raises(Exception, match="unknown evaluator"):
        Loop(MODE, _config(tmp_path, evaluator="nonsense"), project=ROOT)


def test_each_evaluator_says_whether_it_leaves_this_process():
    """The loop should not have to match on a name to know which one is slow."""
    walker = evaluators.create("walker", config=_config(Path("/tmp")))
    dsh = evaluators.create("dsh", config=_config(Path("/tmp")))
    assert walker.out_of_process is False
    assert dsh.out_of_process is True


# ── the seam actually takes a new implementation ───────────────────────────
def test_a_third_evaluator_needs_no_edit_to_the_loop(tmp_path):
    """The property the abstraction exists for.

    A new evaluator is registered and used without touching `loop.py`. If this
    ever needs a change to the loop, the seam has stopped being a seam.
    """

    class Fixed(Evaluator):
        name = "fixed"
        seen: list[str] = []

        @classmethod
        def from_settings(cls, *, config, model=None, project=None):
            return cls()

        def evaluate(self, mode, state, workdir):
            Fixed.seen.append(mode.name)
            return RunResult(score=0.5, facts={"canned": True}, exit_ok=True)

    evaluators.register("fixed", Fixed)
    try:
        config = _config(tmp_path, evaluator="fixed", epochs=1, batch_size=1)
        loop = Loop(MODE, config, project=ROOT)
        assert isinstance(loop.evaluator, Fixed), "the loop built the wrong evaluator"
        report = loop.run()
        assert Fixed.seen, "the registered evaluator was never called"
        assert report["best_score"] == 0.5, (
            "the score did not come from the evaluator, so the loop is not reading "
            f"the seam: {report}"
        )
    finally:
        evaluators.REGISTRY.pop("fixed", None)


def test_the_loop_does_not_branch_on_the_evaluator_name():
    """The branch was the defect. A seam that is still an `if` is not a seam."""
    import inspect

    source = inspect.getsource(Loop._evaluate)
    assert "evaluator ==" not in source, (
        "`_evaluate` branches on the evaluator name again; the choice belongs in "
        "the registry"
    )
    assert "evaluator.evaluate(" in source, "`_evaluate` does not call the seam"


# ── the two must agree ─────────────────────────────────────────────────────
def test_the_walker_scores_a_mode(tmp_path):
    """The fast one, on its own, with no loop around it."""
    from refactory_dsh import registry

    _, mode_class = registry.resolve(ROOT, MODE)
    mode = mode_class()
    walker = WalkerEvaluator(model=None, project=ROOT)
    result = walker.evaluate(mode, mode.state_dict(), tmp_path / "run")
    assert result.exit_ok, result.error
    assert result.score > 0.0
    assert result.duration_s >= 0.0


@pytest.mark.integration
def test_both_evaluators_agree_on_the_same_mode(tmp_path):
    """The equivalence property, asserted on the seam rather than inside the loop.

    Both are asked the same question — run this mode, tell me what it scored — and
    they must give the same answer, or the loop is searching a fiction.
    """
    from refactory_dsh import registry

    _, mode_class = registry.resolve(ROOT, MODE)
    mode = mode_class()
    state = mode.state_dict()

    walker = WalkerEvaluator(model=None, project=ROOT)
    dsh = evaluators.create("dsh", config=_config(tmp_path))
    fast = walker.evaluate(mode, state, tmp_path / "fast")
    shipped = dsh.evaluate(mode, state, tmp_path / "shipped")

    assert shipped.exit_ok, shipped.error
    assert fast.score == shipped.score, (
        f"the two evaluators disagree about the same mode: walker {fast.score}, "
        f"dsh {shipped.score}"
    )
