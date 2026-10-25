"""The run directory is versioned, and a run can be resumed.

The durability issue: `state_dict` had a version and a strict loader; the run
artifacts had neither, so a report from an older build read as if it were current.
And a run that died lost everything, because the loop was one process.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from refactory_dsh import history
from refactory_dsh.history import RunVersionError
from refactory_dsh.loop import Loop, LoopConfig

MODE = "circle_packing/adaptive"


def _config(root: Path, **overrides: object) -> LoopConfig:
    base = dict(epochs=3, batch_size=4, optimizer="random", seed=5, runs_root=root)
    base.update(overrides)
    return LoopConfig(**base)  # type: ignore[arg-type]


def _loop(root: Path, **overrides: object) -> Loop:
    return Loop(MODE, _config(root, **overrides), project=Path("."))


@pytest.fixture(scope="module")
def _uninterrupted(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, dict]:
    root = tmp_path_factory.mktemp("durability") / "run"
    return root, _loop(root).run()


@pytest.fixture
def uninterrupted(_uninterrupted: tuple[Path, dict]) -> tuple[Path, dict]:
    """A finished run that was never interrupted: its directory, and a copy of its report.

    The run is deterministic (seed 5), so the tests that only read a finished run, and
    every resume test's uninterrupted run to compare against, share this one.
    """
    root, report = _uninterrupted
    return root, copy.deepcopy(report)


# ── versioning ─────────────────────────────────────────────────────────────
def test_a_run_records_its_layout_version(uninterrupted):
    root, _report = uninterrupted
    payload = json.loads((root / history.RUN_FILE).read_text())
    assert payload["version"] == history.RUN_VERSION
    assert payload["mode"] == MODE


def test_a_run_with_a_different_version_is_refused(tmp_path):
    root = tmp_path / "run"
    _loop(root).run()
    payload = json.loads((root / history.RUN_FILE).read_text())
    payload["version"] = history.RUN_VERSION + 1
    (root / history.RUN_FILE).write_text(json.dumps(payload))
    with pytest.raises(RunVersionError, match="not"):
        history.read_run(root)


def test_a_run_predating_versioning_is_refused_rather_than_guessed(tmp_path):
    root = tmp_path / "run"
    _loop(root).run()
    (root / history.RUN_FILE).unlink()
    with pytest.raises(RunVersionError, match="before runs were versioned"):
        history.read_run(root)


def test_an_unreadable_sibling_is_skipped_as_a_prior(tmp_path):
    old = tmp_path / "an-old-run"
    _loop(old).run()
    (old / history.RUN_FILE).unlink()
    _loop(tmp_path / "a-new-run").run()
    found = history.find_runs(tmp_path)
    assert [a.path.name for a in found] == ["a-new-run"], (
        "an unreadable run must not be offered as a prior"
    )


# ── resume ─────────────────────────────────────────────────────────────────
def _identical(a: dict, b: dict) -> bool:
    return (
        a["best_score"] == b["best_score"]
        and a["evaluations"] == b["evaluations"]
        and a["epochs_completed"] == b["epochs_completed"]
        and [row["id"] for row in a["leaderboard"]]
        == [row["id"] for row in b["leaderboard"]]
    )


def test_a_fresh_run_leaves_a_journal(uninterrupted):
    root, _report = uninterrupted
    events = [json.loads(line)["event"] for line in (root / "journal.jsonl").read_text().splitlines()]
    assert "start" in events and "candidate" in events and "score" in events
    assert "epoch_open" in events and "epoch_end" in events


def test_resuming_between_epochs_matches_an_uninterrupted_run(tmp_path, uninterrupted):
    root = tmp_path / "run"
    interrupted = _loop(root)
    interrupted.init()
    interrupted.epoch_step()
    resumed = _loop(root, resume=True).run()
    _fresh_root, fresh = uninterrupted
    assert _identical(resumed, fresh)


def test_resuming_from_the_middle_of_an_epoch_matches_an_uninterrupted_run(tmp_path, uninterrupted):
    """A crash is not tidy: the epoch was opened and half its work was done."""
    root = tmp_path / "run"
    interrupted = _loop(root)
    interrupted.init()
    interrupted.epoch_step()
    interrupted.epoch += 1
    interrupted._journal("epoch_open", epoch=interrupted.epoch)  # noqa: SLF001
    interrupted._epoch_open = True  # noqa: SLF001
    interrupted.forward()
    interrupted.loss_step()
    resumed = _loop(root, resume=True).run()
    _fresh_root, fresh = uninterrupted
    assert _identical(resumed, fresh)


def test_a_run_killed_before_any_epoch_still_matches(tmp_path, uninterrupted):
    root = tmp_path / "run"
    _loop(root).init()
    resumed = _loop(root, resume=True).run()
    _fresh_root, fresh = uninterrupted
    assert _identical(resumed, fresh)


def test_resume_does_not_re_evaluate_a_candidate_already_scored(tmp_path):
    root = tmp_path / "run"
    interrupted = _loop(root)
    interrupted.init()
    interrupted.epoch_step()
    scored = {c.id for c in interrupted.population if c.evaluated}

    resumed = _loop(root, resume=True)
    resumed.init()
    assert resumed.evaluations == len(scored), (
        "replay must restore the evaluation count, not spend it again"
    )
    for candidate in resumed.population:
        if candidate.id in scored:
            assert candidate.evaluated and candidate.score is not None


def test_resume_is_refused_when_there_is_nothing_to_resume_from(tmp_path):
    from refactory_dsh.loop import LoopError

    with pytest.raises(LoopError, match="nothing to resume"):
        _loop(tmp_path / "empty", resume=True).init()


def test_a_torn_last_line_is_ignored(tmp_path, uninterrupted):
    """What a crash mid-write looks like, and why the log is append-only."""
    root = tmp_path / "run"
    interrupted = _loop(root)
    interrupted.init()
    interrupted.epoch_step()
    journal = root / "journal.jsonl"
    with journal.open("a") as handle:
        handle.write('{"event": "candidate", "id": "half-writ')
    resumed = _loop(root, resume=True).run()
    _fresh_root, fresh = uninterrupted
    assert _identical(resumed, fresh)


def test_a_resumed_run_says_it_was_resumed(tmp_path):
    root = tmp_path / "run"
    interrupted = _loop(root)
    interrupted.init()
    interrupted.epoch_step()
    _loop(root, resume=True).run()
    payload = json.loads((root / history.RUN_FILE).read_text())
    assert payload["resumed"] is True


def test_the_epoch_is_not_counted_twice_across_a_resume(tmp_path):
    root = tmp_path / "run"
    interrupted = _loop(root)
    interrupted.init()
    interrupted.epoch_step()
    resumed = _loop(root, resume=True)
    resumed.init()
    assert resumed.epoch == 1, "the completed epoch must be replayed, not re-opened"
    assert resumed._epoch_open is False  # noqa: SLF001
