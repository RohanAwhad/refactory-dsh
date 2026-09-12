"""Tests for pinning a mode's optimizable surface and detecting drift."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from refactory_dsh import knobs


def _mode(nodes: dict | None = None) -> dict:
    return {
        "name": "m",
        "nodes": nodes
        or {
            "make": {
                "id": "make", "_type": "LLMNode", "blocking": True,
                "reads": [], "writes": ["candidate.py"],
                "system_prompt": "write it", "temperature": 0.7,
            }
        },
        "edges": [], "start_node": "make", "terminal": True,
    }


def _write(tmp_path: Path, payload: dict) -> Path:
    path = tmp_path / "m.graph.json"
    path.write_text(json.dumps(payload))
    return path


def test_pinning_writes_a_surface_beside_the_mode(tmp_path: Path) -> None:
    mode = _write(tmp_path, _mode())
    surface = knobs.pin(mode)
    assert surface["knobs"], "expected at least one optimizable knob"
    assert mode.with_suffix(".knobs.json").exists()


def test_a_dead_knob_is_in_the_pin_but_not_among_the_knobs(tmp_path: Path) -> None:
    mode = _write(tmp_path, _mode())
    surface = knobs.pin(mode)
    assert "make.temperature" in {d["name"] for d in surface["dead"]}
    assert "make.temperature" not in {k["name"] for k in surface["knobs"]}


def test_an_unchanged_mode_reports_no_drift(tmp_path: Path) -> None:
    """Dead knobs are reported every check; drift is not reported when there is none.

    The fixture carries a dead field on purpose, so the assertion is specifically
    about drift: nothing has been lost or added since the mode was pinned.
    """
    mode = _write(tmp_path, _mode())
    knobs.pin(mode)
    findings = knobs.check(mode)[1]
    assert not any("shrunk" in f or "new since" in f for f in findings), findings


def test_a_shrinking_surface_is_a_finding_not_a_silent_shrink(tmp_path: Path) -> None:
    mode = _write(tmp_path, _mode())
    knobs.pin(mode)
    shrunk = _mode(nodes={
        "other": {"id": "other", "_type": "FnNode", "blocking": True,
                  "reads": [], "writes": [], "command": "true"}
    })
    mode.write_text(json.dumps(shrunk))
    findings = knobs.check(mode)[1]
    assert any("make.system_prompt" in f and "shrunk" in f for f in findings), findings


def test_the_pin_is_not_rewritten_by_a_check(tmp_path: Path) -> None:
    """A check that re-pinned would compare the surface against itself."""
    mode = _write(tmp_path, _mode())
    original = knobs.pin(mode)
    mode.write_text(json.dumps(_mode(nodes={
        "other": {"id": "other", "_type": "FnNode", "blocking": True,
                  "reads": [], "writes": [], "command": "true"}
    })))
    knobs.check(mode)
    assert knobs.load_pin(mode) == original


def test_a_first_check_pins_an_unpinned_mode(tmp_path: Path) -> None:
    mode = _write(tmp_path, _mode())
    assert knobs.load_pin(mode) is None
    knobs.check(mode)
    assert knobs.load_pin(mode) is not None


def test_an_empty_surface_is_refused_rather_than_searched(tmp_path: Path) -> None:
    """A search over nothing runs, reports generations, and does nothing."""
    mode = _write(tmp_path, _mode(nodes={
        "a": {"id": "a", "_type": "FnNode", "blocking": True,
              "reads": [], "writes": [], "command": "true"}
    }))
    with pytest.raises(SystemExit):
        knobs.require_surface(mode)


def test_require_surface_returns_the_surface_when_there_is_one(tmp_path: Path) -> None:
    mode = _write(tmp_path, _mode())
    surface = knobs.require_surface(mode)
    assert surface["knobs"]
