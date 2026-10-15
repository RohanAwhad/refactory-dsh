"""The circle_packing package: a real mode that really runs and really scores.

Phase 4 gate of ``docs/plans/pythonic-modes.md``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from refactory_dsh import nn, registry
from refactory_packages.circle_packing import PACKAGE

MODE = "circle_packing/adaptive"

#: Measured on this port: the deterministic generator reaches 0.795-1.275
#: depending on grid and order, and jittered restarts reach about 1.2 from the
#: worst corner. The band is asserted rather than an exact number so a legitimate
#: algorithm change does not have to rewrite the test.
LOW, HIGH = 0.5, 1.6


def _mode_class() -> type[nn.Mode]:
    _package, mode_class = registry.resolve(Path("."), MODE)
    return mode_class


def _run(state: dict | None = None, workdir: Path | None = None) -> nn.RunResult:
    mode_class = _mode_class()
    mode = mode_class() if state is None else mode_class(state=state)
    return mode.evaluate(workdir or Path(_tmp()))


_TMP: list[Path] = []


def _tmp() -> Path:
    import tempfile

    directory = Path(tempfile.mkdtemp(prefix="circle-packing-test-"))
    _TMP.append(directory)
    return directory


def test_the_retry_gate_says_why_it_retries(tmp_path):
    from refactory_packages.circle_packing import ops

    ctx = nn.RunContext(tmp_path, knobs={}, facts={}, task={})
    ctx.write_json(ops.EVAL, {"id": "c1", "score": 0.0, "metrics": {}})
    verdict = ops.retry_gate(ctx)
    assert verdict == "retry" and "produced no score" in verdict.reason


def test_the_package_is_discovered():
    assert "circle_packing" in registry.discover_names()
    assert PACKAGE.ids() == [MODE]


def test_the_mode_lists_under_its_package():
    listing = registry.render_catalogue(Path("."))
    assert "circle_packing/" in listing
    assert MODE in listing


def test_the_seed_state_is_valid_with_calls_resolved():
    mode = _mode_class()()
    state = mode.state_dict()
    issues = nn.validate(
        nn.Graph.from_state(state["graph"]),
        {name: entry["value"] for name, entry in state["knobs"].items()},
        declared_knobs=mode.parameters(),
        invariants=mode.invariants(),
        resolve_calls=True,
    )
    assert issues == [], [str(i) for i in issues]


def test_the_mode_runs_and_scores():
    result = _run()
    assert result.exit_ok, result.error
    assert LOW < result.score < HIGH
    assert result.facts["best_id"]
    assert result.facts["spent"] >= 1


def test_the_mode_is_reproducible():
    assert _run().score == _run().score


def test_the_score_is_the_task_rule():
    """The score is the radii that fit, checked against the recorded metrics."""
    workdir = _tmp()
    result = _run(workdir=workdir)
    best = json.loads((workdir / "best_solution.json").read_text())
    assert result.score == pytest.approx(best["score"])
    metrics = best["metrics"]
    assert metrics["placed"] + metrics["overlaps"] + metrics["boundary_violations"] >= 1
    assert 0.0 <= metrics["coverage"] <= 1.0


def test_the_seed_pipeline_wastes_its_budget():
    """The defect the outer loop exists to find.

    ``generate`` is deterministic, so every iteration after the seed produces the
    same answer, the strict acceptance rule rejects it, and the ladder spends its
    whole budget re-evaluating one candidate. More budget changes nothing.
    """
    seed = _mode_class()().state_dict()
    small = nn.edits.apply({"op": "set_knob", "name": "budget", "value": 2}, seed)
    large = nn.edits.apply({"op": "set_knob", "name": "budget", "value": 8}, seed)
    workdir = _tmp()
    result = _run(large, workdir=workdir)
    assert result.facts["spent"] == 8
    assert _run(small).score == result.score
    rejections = json.loads((workdir / "rejection_history.json").read_text())
    assert len(rejections) >= 6, "the wasted evaluations should be recorded as rejections"


def test_a_parameter_move_improves_the_score():
    seed = _mode_class()().state_dict()
    moved = nn.edits.apply({"op": "set_knob", "name": "order", "value": "asc"}, seed)
    assert _run(moved).score > _run(seed).score


def test_two_parameter_moves_beat_either_alone():
    seed = _mode_class()().state_dict()
    once = nn.edits.apply({"op": "set_knob", "name": "order", "value": "asc"}, seed)
    both = nn.edits.apply({"op": "set_knob", "name": "grid", "value": 18}, once)
    assert _run(both).score > _run(once).score


def test_a_structural_edit_improves_the_score():
    """The fix for the defect above is a node, not a parameter."""
    mode = _mode_class()()
    seed = mode.state_dict()
    template = {node.id: node for node in mode.insertable()}["restart_on_stall"]
    edited = nn.edits.apply(
        {"op": "insert_after", "target": "accept", "node": template.to_state()}, seed
    )
    assert _run(edited).score > _run(seed).score


def test_a_structural_edit_validates_before_it_is_evaluated():
    mode = _mode_class()()
    seed = mode.state_dict()
    template = {node.id: node for node in mode.insertable()}["observe"]
    edited = nn.edits.apply(
        {"op": "insert_after", "target": "eval", "node": template.to_state()}, seed
    )
    issues = nn.validate(
        nn.Graph.from_state(edited["graph"]),
        {name: entry["value"] for name, entry in edited["knobs"].items()},
        declared_knobs=mode.parameters(),
        invariants=mode.invariants(),
        resolve_calls=True,
    )
    assert issues == [], [str(i) for i in issues]
    workdir = _tmp()
    result = _run(edited, workdir=workdir)
    assert result.exit_ok, result.error
    assert (workdir / "observations.json").exists()


def test_removing_the_metric_node_is_refused():
    """The mode's own invariant guards the number being maximized."""
    seed = _mode_class()().state_dict()
    edited = nn.edits.apply({"op": "remove_node", "id": "exit"}, seed)
    issues = nn.validate(
        nn.Graph.from_state(edited["graph"]),
        {name: entry["value"] for name, entry in edited["knobs"].items()},
        declared_knobs=_mode_class()().parameters(),
        invariants=_mode_class()().invariants(),
    )
    assert any("best_score" in issue.message for issue in issues), [str(i) for i in issues]


def test_the_mode_does_not_change_the_problem():
    """The instance is the task's, not the mode's: there is no knob for it."""
    mode = _mode_class()()
    assert "circles" not in mode.knob_names()
    workdir = _tmp()
    _run(workdir=workdir)
    task = json.loads((workdir / "task.json").read_text())
    assert task["n"] == 26
    assert task["radii"] == [round(0.05 + 0.005 * i, 6) for i in range(26)]
