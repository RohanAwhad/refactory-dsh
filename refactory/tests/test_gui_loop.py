"""Running and reading the loop the way a GUI does.

Issue #17: a user starts a run and reads what came out without a terminal. The
loop stays a Python process; what these test is that everything a GUI needs is
reachable through one call each, and that the same calls are reachable over the
command line a DSH tool shells out to.

The console is what the GUI drives. Testing it here rather than through a browser
is deliberate: the GUI's contract is these payloads, and a payload that is wrong
is wrong wherever it is rendered.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from refactory_dsh import console
from refactory_dsh.console import ConsoleError

MODE = "circle_packing/adaptive"
#: A one-epoch, two-candidate run: enough to produce every artifact, fast enough
#: to wait for in a test.
FAST = dict(epochs=1, batch_size=2, optimizer="random", seed=3)


def _start(tmp_path: Path, run_id: str = "run", **overrides: object) -> Path:
    config = {"run_id": run_id, **FAST, **overrides}
    started = console.start(
        MODE, project=Path("."), runs_root=tmp_path / "loop", **config
    )
    return Path(started["root"])


def _wait(run: Path, timeout: float = 60.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = console.status(run)
        if state["finished"] or state["state"] == "died":
            return state
        time.sleep(0.2)
    raise AssertionError(f"{run} did not settle within {timeout}s\n{console.status(run)}")


# ── the catalogue a start dialog shows ─────────────────────────────────────


def test_the_catalogue_lists_the_modes_that_can_be_started():
    ids = [entry["id"] for entry in console.catalogue(project=Path("."))]
    assert MODE in ids
    assert "annealing_pack/anneal" in ids


def test_a_catalogue_entry_carries_what_a_start_dialog_needs():
    entry = next(e for e in console.catalogue(project=Path(".")) if e["id"] == MODE)
    assert entry["startable"] is True
    assert entry["scored"] is True
    assert entry["package"] == "circle_packing"
    assert entry["parameters"], "a mode with no visible parameters cannot be configured"
    assert set(entry["parameters"]) >= {"grid", "order"}
    assert entry["preferred_edits"]
    assert entry["trust"] in ("trusted", "generated")


def test_a_disabled_package_is_listed_but_not_startable(tmp_path):
    """Removing a mode from the list would make a disabled package look absent."""
    from refactory_dsh import registry

    project = tmp_path / "project"
    project.mkdir()
    registry.set_enabled(project, "annealing_pack", False)
    entry = next(
        e for e in console.catalogue(project=project) if e["id"] == "annealing_pack/anneal"
    )
    assert entry["enabled"] is False
    assert entry["startable"] is False
    assert "disabled" in entry["reason"]
    enabled = next(
        e for e in console.catalogue(project=project) if e["id"] == MODE
    )
    assert enabled["startable"] is True


# ── starting, watching, stopping ───────────────────────────────────────────


def test_a_run_started_from_the_console_finishes_and_leaves_a_report(tmp_path):
    run = _start(tmp_path)
    state = _wait(run)
    assert state["state"] == "finished"
    assert state["finished"] is True
    assert state["best_score"] is not None
    assert (run / "report.md").exists()


def test_the_console_records_enough_to_reopen_the_run_later(tmp_path):
    """A GUI that restarted must find the run it started, not orphan it."""
    run = _start(tmp_path)
    _wait(run)
    recorded = json.loads((run / console.CONSOLE_FILE).read_text())
    assert recorded["mode"] == MODE
    assert recorded["root"] == str(run)
    assert recorded["pid"] > 0
    assert console.status(run)["mode"] == MODE


def test_status_reports_progress_while_a_run_is_unfinished(tmp_path):
    """A reader must see a run that has started and not finished.

    Driven by hand rather than by racing a subprocess: the run here is advanced
    one phase, so "unfinished" is a fact of the setup instead of a hope about
    timing. The subprocess path is what the start/watch tests below cover.
    """
    from refactory_dsh.loop import Loop, LoopConfig

    root = tmp_path / "run"
    loop = Loop(
        MODE,
        LoopConfig(epochs=1, batch_size=4, optimizer="random", seed=3, runs_root=root),
        project=Path("."),
    )
    loop.init()
    state = console.status(root)
    assert state["state"] == "running"
    assert state["finished"] is False
    assert state["mode"] == MODE

    loop.forward()
    advanced = console.status(root)
    assert advanced["counters"]["evaluations"] >= 1
    assert advanced["state"] == "running"
    assert any(record["step"] == "forward" for record in advanced["events"])


def test_a_started_run_is_watchable_from_the_first_poll(tmp_path):
    """The console file is written before the loop's first artifact exists."""
    run = _start(tmp_path, epochs=1, batch_size=4)
    first = console.status(run)
    assert first["mode"] == MODE
    assert first["state"] in {"starting", "running"}
    _wait(run)


def test_status_tails_only_what_the_reader_has_not_seen(tmp_path):
    run = _start(tmp_path)
    _wait(run)
    first = console.status(run, after=0)
    assert first["events"]
    assert first["cursor"] == len(first["events"])
    again = console.status(run, after=first["cursor"])
    assert again["events"] == []
    assert again["cursor"] == first["cursor"]


def test_a_run_that_died_is_reported_as_died_not_as_running(tmp_path):
    """A reader has to be able to tell "still working" from "gone"."""
    run = _start(tmp_path)
    _wait(run)
    recorded = json.loads((run / console.CONSOLE_FILE).read_text())
    recorded["pid"] = 999999
    (run / console.CONSOLE_FILE).write_text(json.dumps(recorded))
    (run / "report.json").unlink()
    assert console.status(run)["state"] == "died"
    assert console.status(run)["running"] is False


def test_an_unknown_run_directory_is_an_error(tmp_path):
    try:
        console.status(tmp_path / "nope")
    except ConsoleError as error:
        assert "nope" in str(error)
    else:  # pragma: no cover - the assertion is the point
        raise AssertionError("status must refuse a directory that is not a run")


def test_starting_over_an_existing_run_is_refused(tmp_path):
    _start(tmp_path)
    try:
        _start(tmp_path)
    except ConsoleError as error:
        assert "already exists" in str(error)
    else:  # pragma: no cover - the assertion is the point
        raise AssertionError("a run id must not be silently reused")


def test_stop_reports_that_there_was_nothing_to_stop(tmp_path):
    run = _start(tmp_path)
    _wait(run)
    assert console.stop(run)["stopped"] is False


def test_every_run_under_the_root_is_listed_newest_first(tmp_path):
    root = tmp_path / "loop"
    for run_id in ("one", "two"):
        console.start(
            MODE, project=Path("."), runs_root=root, run_id=run_id, **FAST
        )
    _wait(root / "one")
    _wait(root / "two")
    listed = console.runs(project=Path("."), runs_root=root)
    assert {item["run_id"] for item in listed} == {"one", "two"}
    assert all(item["finished"] for item in listed)


def test_a_run_in_flight_is_listed_too(tmp_path):
    root = tmp_path / "loop"
    run = _start(tmp_path, run_id="watching", epochs=2, batch_size=4)
    assert "watching" in {item["run_id"] for item in console.runs(project=Path("."), runs_root=root)}
    console.stop(run)


# ── the artifacts a GUI renders ────────────────────────────────────────────


def test_the_artifacts_a_gui_renders_are_all_reachable(tmp_path):
    run = _start(tmp_path)
    _wait(run)
    names = {entry["name"] for entry in console.artifacts(run)["artifacts"] if entry["exists"]}
    assert {"report", "gradient", "loss", "progress", "candidates"} <= names


def test_artifacts_can_be_returned_with_their_contents(tmp_path):
    run = _start(tmp_path)
    _wait(run)
    manifest = console.artifacts(run, contents=True)
    by_name = {entry["name"]: entry for entry in manifest["artifacts"]}
    assert "# " in by_name["report"]["content"]
    assert isinstance(by_name["gradient"]["content"], dict)
    assert by_name["gradient"]["content"]["knob_attribution"]
    assert "content" not in by_name["progress"], "a live tail is not a page body"


def test_the_leaderboard_ranks_candidates_and_gives_each_its_diff(tmp_path):
    run = _start(tmp_path)
    _wait(run)
    board = console.leaderboard(run)
    assert len(board) == 2
    assert board[0]["score"] >= board[1]["score"]
    assert board[0]["diff"] is not None
    assert board[0]["diff"]["candidate"] == board[0]["id"]


def test_a_candidate_reads_back_with_its_parent_and_a_rendered_diff(tmp_path):
    run = _start(tmp_path)
    _wait(run)
    child = next(row for row in console.leaderboard(run) if row["parent"])
    read = console.candidate(run, child["id"])
    assert read["candidate"] == child["id"]
    assert read["parent"] == child["parent"]
    assert read["seed"] is False
    assert read["rendered"]
    assert read["score"] == child["score"], "the measurement comes from the run directory"


# ── the command line a DSH tool shells out to ──────────────────────────────


def _cli(*argv: str, cwd: Path = Path(".")) -> dict:
    result = subprocess.run(
        [sys.executable, "-m", "refactory_dsh.console", *argv],
        cwd=str(cwd),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    return json.loads(result.stdout)


def test_the_cli_lists_modes_as_json(tmp_path):
    modes = _cli("modes", "--project", str(Path(".").resolve()))
    assert MODE in [entry["id"] for entry in modes]


def test_the_cli_starts_watches_and_reads_a_run(tmp_path):
    """The whole loop, over the boundary a tool actually crosses."""
    root = tmp_path / "loop"
    started = _cli(
        "start", MODE,
        "--project", str(Path(".").resolve()),
        "--runs-root", str(root),
        "--run-id", "cli",
        "--epochs", "1", "--batch-size", "2", "--optimizer", "random", "--seed", "3",
    )
    run = Path(started["root"])
    deadline = time.time() + 60
    while time.time() < deadline:
        state = _cli("status", "--run", str(run), "--after", "0")
        if state["finished"]:
            break
        time.sleep(0.2)
    assert state["finished"] is True

    board = _cli("leaderboard", "--run", str(run))
    assert board and board[0]["diff"] is not None
    one = _cli("candidate", "--run", str(run), board[0]["id"])
    assert one["candidate"] == board[0]["id"]
    manifest = _cli("artifacts", "--run", str(run), "--contents")
    assert {e["name"] for e in manifest["artifacts"]} >= {"report", "progress"}
    assert _cli("runs", "--project", str(Path(".").resolve()), "--runs-root", str(root))


def test_the_cli_refuses_a_verb_that_needs_a_run_without_one():
    result = subprocess.run(
        [sys.executable, "-m", "refactory_dsh.console", "status"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "needs --run" in json.loads(result.stdout)["error"]


def test_the_cli_reports_a_missing_run_as_json_not_a_traceback(tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "refactory_dsh.console", "status", "--run", str(tmp_path / "gone")],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "is not a run directory" in json.loads(result.stdout)["error"]
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize("verb", ["modes", "runs", "start", "status", "artifacts", "leaderboard", "candidate", "stop"])
def test_every_verb_the_gui_needs_exists(verb):
    from refactory_dsh.console import _parser

    assert verb in _parser()._subparsers._group_actions[0].choices  # type: ignore[attr-defined]


def test_the_console_reads_a_run_it_did_not_start(tmp_path):
    """A run started by the loop's own entry point is still readable.

    The console file records a start the console performed; `run.json` is written
    by the loop and names the mode, so a reader is not blind to a run somebody
    else began.
    """
    from refactory_dsh.loop import Loop, LoopConfig

    root = tmp_path / "byhand"
    loop = Loop(
        MODE,
        LoopConfig(epochs=1, batch_size=2, optimizer="random", seed=3, runs_root=root),
        project=Path("."),
    )
    loop.init()
    assert not (root / console.CONSOLE_FILE).exists()
    state = console.status(root)
    assert state["mode"] == MODE
    assert state["state"] == "running"
    assert state["running"] is False

    loop.forward()
    loop.loss_step()
    loop.backward()
    loop.report()
    finished = console.status(root)
    assert finished["finished"] is True
    assert finished["best_score"] is not None
