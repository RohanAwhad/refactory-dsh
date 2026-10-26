"""`best`, `adopt` and `package smoke` say which score they print.

In the README's walkthrough `adopt` printed 0.875 and `package smoke` printed 0.955 for
the same mode with nothing to say why: the first is the search's score, with its cost
penalty for size taken off, the second the mode's own.
"""
from __future__ import annotations

from pathlib import Path

from refactory_dsh import console, registry
from refactory_dsh.adopt import adopt
from refactory_dsh.loop import Loop, LoopConfig


def test_adopt_best_and_smoke_label_their_scores(tmp_path):
    run = tmp_path / "first-search"
    config = LoopConfig(optimizer="random", edit_preset="knob_only", evaluator="walker",
                        epochs=2, batch_size=4, max_evaluations=8, seed=5, runs_root=run)
    report = Loop("circle_packing/adaptive", config, project=Path(".")).run()
    best = report["best"]
    assert best["score"] < best["raw_score"], "the README's winner pays a cost penalty"
    note = (f"{format(round(best['score'], 6), 'g')} with the cost penalty "
            f"({format(report['scoring']['parsimony'], 'g')} x {best['nodes']} nodes); "
            f"raw {format(round(best['raw_score'], 6), 'g')}")

    adopted = adopt(run, as_name="labelled_tuned", project=tmp_path)
    assert (adopted["score"], adopted["raw_score"]) == (best["score"], best["raw_score"])
    assert adopted["score_note"] == note
    shown = console._best_payload(run, keep_state=False)
    assert (shown["score_note"], shown["raw_score"]) == (note, best["raw_score"])

    smoked = registry.smoke(tmp_path, adopted["mode"])
    assert smoked["score_note"] == (f"{format(round(smoked['score'], 6), 'g')} raw: the mode's "
                                    "own score, without a search's cost penalty")


def test_a_score_with_no_penalty_or_no_raw_score_does_not_claim_one():
    from refactory_dsh.adopt import _score_basis

    old = [{"event": "score", "id": "a", "score": 0.5}]
    assert _score_basis(old, "a", 0.5) == {
        "score_note": "0.5 as the run recorded it; no raw score to compare"}
    free = [{"event": "score", "id": "a", "score": 0.9, "raw_score": 0.9, "parsimony": 0.0,
             "nodes": 8}]
    assert _score_basis(free, "a", 0.9) == {
        "raw_score": 0.9, "score_note": "0.9 raw; the search took no cost penalty"}
