"""Controlling a running search: stop, pause, resume, from another process.

Words reach a search through the intent ledger; control is a small file the loop
polls at a generation boundary, because a panel or a terminal has to be able to
hold or end a run without killing it and without writing prose.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from refactory_dsh.control import Control, ControlError
from refactory_dsh.loop import Loop, LoopConfig

MODE = "circle_packing/adaptive"


def _config(root: Path, **overrides: object) -> LoopConfig:
    base = dict(epochs=2, batch_size=2, optimizer="random", seed=5, runs_root=root,
                control_poll_s=0.01)
    base.update(overrides)
    return LoopConfig(**base)  # type: ignore[arg-type]


def _progress(root: Path) -> list[dict]:
    path = root / "progress.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_a_request_round_trips_and_absent_means_run(tmp_path):
    control = Control(tmp_path)
    assert control.read() is None
    control.request("pause")
    assert control.read() == "pause"
    control.request("resume")
    assert control.read() == "resume"
    control.clear()
    assert control.read() is None


def test_an_unknown_action_is_refused_by_name(tmp_path):
    with pytest.raises(ControlError) as refused:
        Control(tmp_path).request("explode")
    assert "explode" in str(refused.value)


def test_an_unreadable_request_reads_as_none(tmp_path):
    """A half-written or hand-edited file must not be read as an instruction."""
    (tmp_path / "control.json").write_text("{not json")
    assert Control(tmp_path).read() is None


def test_a_stop_ends_the_run_with_the_reason(tmp_path):
    Control(tmp_path).request("stop")
    report = Loop(MODE, _config(tmp_path), project=Path(".")).run()
    assert "a person asked the run to stop" in str(report["bound"])


def test_a_pause_holds_the_run_until_it_is_resumed(tmp_path):
    control = Control(tmp_path)
    control.request("pause")

    def release() -> None:
        time.sleep(0.1)
        control.request("resume")

    thread = threading.Thread(target=release, daemon=True)
    thread.start()
    report = Loop(MODE, _config(tmp_path), project=Path(".")).run()
    thread.join(timeout=5)
    assert report["evaluations"] > 0, "the run continued after the resume"
    steps = [record["step"] for record in _progress(tmp_path)]
    assert "paused" in steps


def test_a_pause_then_a_stop_ends_the_run(tmp_path):
    control = Control(tmp_path)
    control.request("pause")

    def stop_it() -> None:
        time.sleep(0.1)
        control.request("stop")

    thread = threading.Thread(target=stop_it, daemon=True)
    thread.start()
    report = Loop(MODE, _config(tmp_path), project=Path(".")).run()
    thread.join(timeout=5)
    assert "a person asked the run to stop" in str(report["bound"])
