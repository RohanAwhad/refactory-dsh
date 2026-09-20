"""Adopting a run's winner: the artifact D15 names and nothing built.

`create`, `edit` and `design` produce something new, and D15 says an **adopting
loop** does too. It did not exist, so a run's winner was unreachable: the report
carries a score, `state.json`'s population carries edits, `console candidate`
carries a diff, and none of them can be run. The state is in `journal.jsonl`, and
reading a journal by hand is not an interface.

These assert the two properties that matter and the one the decision is actually
about: the adopted mode scores what the winner scored, and adopting changes nothing
it read. The second is D15's whole point -- "so the baseline stays measurable" -- and
a test that only checked a file appeared would not notice it being violated.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from refactory_dsh import registry
from refactory_dsh.adopt import ADOPTED, AdoptionError, adopt, best
from refactory_dsh.loop import Loop, LoopConfig

ROOT = Path(__file__).resolve().parent.parent.parent
MODE = "sympy_pack/exact"


def _digest(path: Path) -> str:
    """Every byte under a directory, so "unchanged" is a fact and not a guess."""
    sha = hashlib.sha256()
    for file in sorted(p for p in path.rglob("*") if p.is_file()):
        sha.update(str(file.relative_to(path)).encode())
        sha.update(file.read_bytes())
    return sha.hexdigest()


@pytest.fixture(scope="module")
def finished_run(tmp_path_factory) -> Path:
    """A real run, small enough to be quick and real enough to have a winner."""
    root = tmp_path_factory.mktemp("runs") / "run"
    loop = Loop(
        MODE,
        LoopConfig(
            epochs=2, batch_size=3, seed=0, optimizer="random", runs_root=root,
            max_evaluations=12,
        ),
        project=ROOT,
    )
    loop.run()
    return root


# ── the winner is addressable ──────────────────────────────────────────────
def test_best_returns_the_state_the_winner_scored(finished_run):
    winner = best(finished_run)
    assert winner["state"], "the winner came back without the state that scored it"
    assert winner["candidate"]
    assert winner["score"] is not None
    assert {"version", "graph", "knobs"} <= set(winner["state"]), (
        "the state is not a mode state, so it cannot be run"
    )


def test_best_reports_the_highest_score_in_the_run(finished_run):
    import json

    report = json.loads((finished_run / "report.json").read_text())
    assert best(finished_run)["score"] == report["best_score"], (
        "best() disagrees with the run's own report about which candidate won"
    )


def test_a_run_with_no_journal_refuses(tmp_path):
    with pytest.raises(AdoptionError, match="nothing recorded a state"):
        best(tmp_path / "empty")


# ── adopting produces something new, and runs ──────────────────────────────
def test_the_adopted_mode_scores_what_the_winner_scored(finished_run, tmp_path):
    """The point of adopting: the artifact is the result, not a description of it."""
    result = adopt(finished_run, as_name="adopted_one", project=tmp_path)
    _, mode_class = registry.resolve(tmp_path, result["mode"])
    scored = mode_class().evaluate(tmp_path / "run")

    assert scored.score == result["score"], (
        f"the adopted mode scores {scored.score} where the winner scored "
        f"{result['score']}; adopting did not carry the result across"
    )


def test_adopting_leaves_the_source_package_byte_identical(finished_run, tmp_path):
    """D15: a new artifact, never a mutation of its input."""
    source = ROOT / "refactory" / "refactory_packages" / "sympy_pack"
    before = _digest(source)
    adopt(finished_run, as_name="adopted_two", project=tmp_path)
    assert _digest(source) == before, (
        "adopting modified the package it adopted from, so the baseline is no "
        "longer measurable against it"
    )


def test_adopting_leaves_the_run_byte_identical(finished_run, tmp_path):
    """The other input. Adoption is a read of the run, not a step in it."""
    before = _digest(finished_run)
    adopt(finished_run, as_name="adopted_three", project=tmp_path)
    assert _digest(finished_run) == before, "adopting rewrote the run it read"


def test_the_adopted_package_records_where_it_came_from(finished_run, tmp_path):
    result = adopt(finished_run, as_name="adopted_four", project=tmp_path)
    recorded = (Path(result["package_root"]) / ADOPTED).read_text()
    assert result["candidate"] in recorded
    assert MODE in recorded
    assert str(finished_run) in recorded


def test_adopting_never_overwrites(finished_run, tmp_path):
    adopt(finished_run, as_name="adopted_five", project=tmp_path)
    with pytest.raises(AdoptionError, match="already exists"):
        adopt(finished_run, as_name="adopted_five", project=tmp_path)


def test_the_adopted_mode_is_registered_and_runnable_by_name(finished_run, tmp_path):
    """A mode nothing can find is the failure the previous system kept making."""
    result = adopt(finished_run, as_name="adopted_six", project=tmp_path)
    listed = {
        f"{package}/{mode['name']}"
        for package, modes in registry.catalogue(tmp_path).items()
        for mode in modes
    }
    assert result["mode"] in listed, (
        f"{result['mode']} was written but the registry does not list it: {sorted(listed)}"
    )
