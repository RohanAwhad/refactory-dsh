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


def test_report_artifact_includes_planning_models(tmp_path, monkeypatch):
    """report_artifact reads worker receipts and records planning_models in the report.

    Without the fix, design.json would not contain a planning_models key.
    """
    monkeypatch.setenv("REFACTORY_PROJECT", str(tmp_path))
    from tests_support import (DESIGN_DOC_ANSWER, DESIGN_REVIEW_ANSWER,
                               PITFALLS_ANSWER, RESEARCH_ANSWER,
                               VERIFICATION_ANSWER, REVIEW_ANSWERS,
                               FakeModel, render)
    answers = [
        RESEARCH_ANSWER, PITFALLS_ANSWER, "perspectives", "one plan",
        DESIGN_DOC_ANSWER, DESIGN_REVIEW_ANSWER,
        render({"tool.py": "print('built')\n"}),
        VERIFICATION_ANSWER,
        *REVIEW_ANSWERS,
    ]
    result = DesignMode().run(
        tmp_path / "run", model=FakeModel(answers),
        verdicts={"gate_strategy": "proceed"},
        task={"name": "routing_test", "brief": "a tool", "check": "python tool.py"},
    )
    assert result.exit_ok, result.error
    report = json.loads((tmp_path / "run" / "design.json").read_text())
    # Worker receipts should have been written for design_doc and design_review;
    # the report should include their model info.
    assert "planning_models" in report, (
        "report_artifact should include planning_models from worker receipts"
    )
    assert "design_doc" in report["planning_models"]


def test_report_without_node_routes_records_default_model(tmp_path, monkeypatch):
    """Without node_routes, the default model is recorded in planning_models."""
    monkeypatch.setenv("REFACTORY_PROJECT", str(tmp_path))
    from tests_support import (DESIGN_DOC_ANSWER, DESIGN_REVIEW_ANSWER,
                               PITFALLS_ANSWER, RESEARCH_ANSWER,
                               VERIFICATION_ANSWER, REVIEW_ANSWERS,
                               FakeModel, render)
    answers = [
        RESEARCH_ANSWER, PITFALLS_ANSWER, "perspectives", "one plan",
        DESIGN_DOC_ANSWER, DESIGN_REVIEW_ANSWER,
        render({"tool.py": "print('built')\n"}),
        VERIFICATION_ANSWER,
        *REVIEW_ANSWERS,
    ]
    result = DesignMode().run(
        tmp_path / "run", model=FakeModel(answers),
        verdicts={"gate_strategy": "proceed"},
        task={"name": "no_route_test", "brief": "a tool", "check": "python tool.py"},
    )
    assert result.exit_ok, result.error
    report = json.loads((tmp_path / "run" / "design.json").read_text())
    assert "planning_models" in report
