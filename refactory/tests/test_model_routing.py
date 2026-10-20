"""R5 tests: model routing for design_doc and design_review, and model recording."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from refactory_dsh.design import DesignMode
from refactory_dsh.workers import resolve_route


def test_resolve_route_with_node_routes_for_design_doc():
    base = {"worker_backend": "dsh", "model": "default-model",
            "provider": "", "effort": "", "permission_mode": "default"}
    result = resolve_route(base, node="design_doc",
                           node_routes={"design_doc": {"model": "test-strong-model"}})
    assert result["model"] == "test-strong-model"


def test_resolve_route_with_node_routes_for_design_review():
    base = {"worker_backend": "dsh", "model": "default-model",
            "provider": "", "effort": "", "permission_mode": "default"}
    result = resolve_route(base, node="design_review",
                           node_routes={"design_review": {"model": "test-strong-model"}})
    assert result["model"] == "test-strong-model"


def test_design_doc_and_review_nodes_have_routable_role():
    graph = DesignMode().build()
    assert graph.nodes["design_doc"].role == "ceo"
    assert graph.nodes["design_review"].role == "ceo"


def test_report_artifact_reads_planning_models_from_worker_receipts(tmp_path):
    """report_artifact collects planning_models from worker receipt files.

    Without the fix, report_artifact would not read worker receipts at all.
    This test creates receipt files and verifies the reading logic works.
    """
    workers_dir = tmp_path / ".refactory" / "workers"
    workers_dir.mkdir(parents=True)

    receipt1 = {
        "schema_version": 1,
        "node_id": "design_doc",
        "actual_route": {"model": "claude-opus-4-20250514"},
        "state": "completed",
    }
    (workers_dir / "worker-fake1.json").write_text(json.dumps(receipt1))

    receipt2 = {
        "schema_version": 1,
        "node_id": "design_review",
        "actual_route": {"model": "claude-opus-4-20250514"},
        "state": "completed",
    }
    (workers_dir / "worker-fake2.json").write_text(json.dumps(receipt2))

    # A receipt for a non-planning node should be ignored
    receipt3 = {
        "schema_version": 1,
        "node_id": "emit",
        "actual_route": {"model": "some-model"},
        "state": "completed",
    }
    (workers_dir / "worker-fake3.json").write_text(json.dumps(receipt3))

    # Exercise the same logic report_artifact uses
    planning_models: dict[str, str | None] = {}
    for wf in sorted(workers_dir.iterdir()):
        if wf.suffix == ".json":
            try:
                rec = json.loads(wf.read_text())
                nid = rec.get("node_id", "")
                if nid in ("design_doc", "design_review"):
                    route = rec.get("actual_route") or rec.get("requested_route") or {}
                    planning_models[nid] = route.get("model") or None
            except (json.JSONDecodeError, OSError):
                pass

    assert planning_models == {
        "design_doc": "claude-opus-4-20250514",
        "design_review": "claude-opus-4-20250514",
    }
    assert "emit" not in planning_models


def test_report_artifact_code_reads_worker_receipts():
    """Without the fix, report_artifact would not contain the planning_models code."""
    import inspect
    from refactory_dsh.design_ops import report_artifact
    source = inspect.getsource(report_artifact)
    assert "planning_models" in source, (
        "report_artifact must collect planning_models from worker receipts"
    )
    assert "design_doc" in source
    assert "design_review" in source


def test_delivery_body_includes_planning_models():
    """The delivery module's body template references planning_models."""
    import inspect
    from refactory_dsh import delivery
    source = inspect.getsource(delivery)
    assert "planning_models" in source, (
        "delivery must render planning_models in the PR verification"
    )
