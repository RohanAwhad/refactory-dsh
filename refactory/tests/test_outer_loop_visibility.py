"""Tests for the outer loop's visibility surface, steering, and trace identity.

These modules had no coverage, and two counting bugs lived in them: a candidate
traced under two keys was counted twice in the operator leaderboard, and a
changed trace could drop out of the report entirely. Both are regression-tested
here, because a visibility surface that miscounts is worse than none.
"""

from __future__ import annotations

import json
from pathlib import Path

from refactory_dsh import report, steering, trace
from factory.workflow.primitives import Workflow

OUTER = Path(".factory") / "outer_loop"


def _workflow(name: str = "m", grid: float = 10.0) -> Workflow:
    return Workflow.from_dict(
        {
            "name": name,
            "nodes": {
                "make": {"id": "make", "_type": "FnNode", "command": "true",
                         "reads": ["task.yaml"], "writes": ["candidate.py"]},
                "score": {"id": "score", "_type": "FnNode", "command": "true",
                          "reads": ["candidate.py"], "writes": ["score.json"]},
            },
            "edges": [{"source": "make", "target": "score", "condition": None}],
            "start_node": "make",
            "terminal": True,
            "knob_values": {"grid": grid},
            "knob_bounds": {"grid": [4, 8, 16]},
            "knob_specs": {"grid": {"kind": "threshold", "node_id": "make", "default": 10}},
        }
    )


# ── steering ───────────────────────────────────────────────────────────────


def test_steering_defaults_when_file_is_absent(tmp_path: Path) -> None:
    settings = steering.load(tmp_path)
    assert settings["optimizer"] is None
    assert steering.frozen_knobs(settings) == set()
    assert steering.operator_weights(settings) == steering.DEFAULT_OPERATOR_WEIGHTS


def test_steering_survives_an_unreadable_file(tmp_path: Path) -> None:
    (tmp_path / OUTER).mkdir(parents=True)
    (tmp_path / OUTER / "steering.json").write_text("{not json")
    # Steering must never be able to stop a run.
    assert steering.load(tmp_path) == steering.DEFAULTS


def test_operator_weights_merge_rather_than_replace(tmp_path: Path) -> None:
    (tmp_path / OUTER).mkdir(parents=True)
    (tmp_path / OUTER / "steering.json").write_text(
        json.dumps({"operator_weights": {"knob_mutate": 0.5}})
    )
    weights = steering.operator_weights(steering.load(tmp_path))
    assert weights["knob_mutate"] == 0.5
    # Unmentioned operators keep their shipped weight; steering one must not
    # silently zero the rest.
    assert weights["node_insert"] == steering.DEFAULT_OPERATOR_WEIGHTS["node_insert"]


def test_negative_and_non_numeric_weights_are_ignored(tmp_path: Path) -> None:
    (tmp_path / OUTER).mkdir(parents=True)
    (tmp_path / OUTER / "steering.json").write_text(
        json.dumps({"operator_weights": {"knob_mutate": -1, "node_insert": "lots"}})
    )
    weights = steering.operator_weights(steering.load(tmp_path))
    assert weights["knob_mutate"] == steering.DEFAULT_OPERATOR_WEIGHTS["knob_mutate"]
    assert weights["node_insert"] == steering.DEFAULT_OPERATOR_WEIGHTS["node_insert"]


# ── trace identity ─────────────────────────────────────────────────────────


def test_workflow_key_is_stable_across_a_round_trip() -> None:
    wf = _workflow()
    assert trace.workflow_key(wf) == trace.workflow_key(Workflow.from_dict(wf.to_dict()))


def test_render_carries_evidence_links_and_an_analysis_slot() -> None:
    facts = {
        "key": "abc",
        "proposal": None,
        "parent_key": None,
        "shape": {"name": "m", "nodes": 2, "edges": 1, "node_ids": ["make", "score"],
                  "knob_values": {"grid": 10}},
        "run": {"mode": "gepa", "task": "t", "budget": "1", "knobs": {}, "exit_code": 0,
                "duration_s": 1.0, "work_dir": "/tmp/w", "stderr_tail": ""},
        "outcome": None,
        "observations": ["note"],
        "artifacts": ["/tmp/w/candidate.py"],
    }
    md = trace.render(facts)
    assert "file:///tmp/w/candidate.py" in md  # every artifact is linked
    assert trace.ANALYSIS_HEADING in md  # the model-authored slot exists
    assert "Facts below are extracted mechanically" in md


def test_annotation_sweeps_a_duplicate_trace(tmp_path: Path) -> None:
    """The same candidate traced under two keys must end up counted once."""
    wf = _workflow()
    payload = {
        "key": trace.workflow_key(wf),
        "proposal": None,
        "shape": {},
        "run": {"mode": "gepa", "task": "t", "budget": "1", "knobs": {}, "exit_code": 0,
                "duration_s": 1.0, "work_dir": "/tmp/w", "stderr_tail": ""},
        "outcome": None,
        "observations": [],
        "artifacts": [],
        "workflow": wf.to_dict(),
    }
    traces = tmp_path / trace.TRACES_DIR
    traces.mkdir(parents=True)
    # a stale key and the canonical key, both describing the same candidate
    (traces / "stale00000.json").write_text(json.dumps(payload))
    (traces / "stale00000.md").write_text("stale")
    (traces / "canonical01.json").write_text(json.dumps(payload))

    (tmp_path / OUTER / "population").mkdir(parents=True)
    (tmp_path / OUTER / "population" / "population.json").write_text(
        json.dumps([{"id": "i1", "workflow_data": wf.to_dict(), "generation": 0,
                     "parent_id": None, "score": 1.0, "mutation_record": None}])
    )

    trace.annotate_from_population(tmp_path)

    remaining = sorted(p.name for p in traces.glob("*.json"))
    assert remaining == [f"{trace.workflow_key(wf)}.json"], remaining
    # the stale companion markdown goes with it
    assert not (traces / "stale00000.md").exists()


# ── the report ─────────────────────────────────────────────────────────────


def _seed_run_dir(tmp_path: Path) -> None:
    (tmp_path / OUTER / "population").mkdir(parents=True)
    (tmp_path / OUTER / "archive").mkdir(parents=True)
    (tmp_path / OUTER / "state.json").write_text(
        json.dumps({"generation": 2, "score_trajectory": [0.5, 0.8],
                    "budget_consumed": 4, "total_cost_usd": 0.25})
    )
    (tmp_path / OUTER / "population" / "population.json").write_text(
        json.dumps([
            {"id": "a", "score": 0.8, "workflow_data": {}, "generation": 1, "parent_id": None},
            {"id": "b", "score": 0.6, "workflow_data": {}, "generation": 1, "parent_id": "a"},
        ])
    )
    (tmp_path / OUTER / "archive" / "grid.json").write_text(json.dumps({"1,2,3": {}}))
    (tmp_path / OUTER / "rejections.jsonl").write_text(
        json.dumps({"candidate": "m", "optimizer": "random",
                    "reasons": ["no longer produces candidate.py"]}) + "\n"
    )
    traces = tmp_path / trace.TRACES_DIR
    traces.mkdir(parents=True)
    for key, operator, verdict, delta, knob in (
        ("t1", "knob_mutate", "keep", 0.3, 8.0),
        ("t2", "parallelize", "revert", -0.2, 10.0),
    ):
        (traces / f"{key}.json").write_text(
            json.dumps({
                "key": key,
                "proposal": {"operator": operator, "after": {"prediction": "p", "confidence": 0.4},
                             "rationale": "r"},
                "shape": {"knob_values": {"grid": knob}},
                "outcome": {"score": 0.8, "delta": delta, "verdict": verdict},
                "run": {"duration_s": 5.0},
                "observations": [],
                "artifacts": [],
            })
        )


def test_report_summarises_learning_cost_and_coverage(tmp_path: Path) -> None:
    _seed_run_dir(tmp_path)
    data = report.build(tmp_path)
    assert data["generation"] == 2
    assert data["budget"]["inner_seconds_total"] == 10.0
    assert data["learning"]["best_now"] == 0.8
    assert data["coverage"]["occupied_cells"] == 1


def test_report_counts_each_operator_once(tmp_path: Path) -> None:
    _seed_run_dir(tmp_path)
    rows = {r["operator"]: r for r in report.build(tmp_path)["operator_leaderboard"]}
    assert rows["knob_mutate"]["attempts"] == 1
    assert rows["knob_mutate"]["accepted"] == 1
    assert rows["parallelize"]["attempts"] == 1
    assert rows["parallelize"]["accepted"] == 0
    # best-performing operator first, so the surface answers "what to do more of"
    assert report.build(tmp_path)["operator_leaderboard"][0]["operator"] == "knob_mutate"


def test_report_surfaces_rejections_and_predictions(tmp_path: Path) -> None:
    _seed_run_dir(tmp_path)
    data = report.build(tmp_path)
    assert data["rejected_total"] == 1
    assert data["rejection_reasons"][0]["reason"] == "no longer produces candidate.py"
    assert data["proposals"] and data["proposals"][0]["prediction"] == "p"


def test_report_shows_the_active_steering(tmp_path: Path) -> None:
    _seed_run_dir(tmp_path)
    (tmp_path / OUTER / "steering.json").write_text(
        json.dumps({"optimizer": "autoresearch", "frozen_knobs": ["grid"]})
    )
    data = report.build(tmp_path)
    assert data["steering"]["optimizer"] == "autoresearch"
    assert data["steering"]["frozen_knobs"] == ["grid"]
    assert "Steering in force" in report.render(data)


def test_leaderboard_attributes_run_failures_to_the_operator(tmp_path: Path) -> None:
    """A candidate that never ran is evidence about the edit, not about the mode.

    A structural edit can keep every declared artifact and satisfy the contract
    and still produce a graph that does not run. That is a different fact from a
    low score, so the surface has to report it separately.
    """
    _seed_run_dir(tmp_path)
    traces = tmp_path / trace.TRACES_DIR
    (traces / "t3.json").write_text(
        json.dumps({
            "key": "t3",
            "proposal": {"operator": "parallelize"},
            "shape": {"knob_values": {}},
            "outcome": {"score": 0.0, "delta": None, "verdict": "revert", "stuck": True},
            "run": {"duration_s": 1.0, "exit_code": 0},
            "observations": [],
            "artifacts": [],
        })
    )
    rows = {r["operator"]: r for r in report.build(tmp_path)["operator_leaderboard"]}
    assert rows["parallelize"]["run_failures"] == 1
    assert rows["knob_mutate"]["run_failures"] == 0
    assert "never ran" in report.render(report.build(tmp_path))


def test_a_nonzero_exit_also_counts_as_a_run_failure(tmp_path: Path) -> None:
    _seed_run_dir(tmp_path)
    (tmp_path / trace.TRACES_DIR / "t4.json").write_text(
        json.dumps({
            "key": "t4", "proposal": {"operator": "edge_redirect"},
            "shape": {"knob_values": {}},
            "outcome": {"score": 0.1, "delta": None, "verdict": "revert"},
            "run": {"duration_s": 1.0, "exit_code": 1},
            "observations": [], "artifacts": [],
        })
    )
    rows = {r["operator"]: r for r in report.build(tmp_path)["operator_leaderboard"]}
    assert rows["edge_redirect"]["run_failures"] == 1
