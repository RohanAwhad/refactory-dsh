"""Spine node-runner tests for route_refine_design.

Runs route_refine_design through refactory_dsh.run main (REFACTORY_WORKDIR /
DSH_WORKFLOW_NODE, as tests/test_build_loop_stop.py does).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from refactory_dsh.design import DesignMode
from refactory_dsh.design_ops import REFINE_SCOPE_REASON
from refactory_dsh.run import main


def _run_node_main(run, target, node_id, node_type, monkeypatch):
    import io

    monkeypatch.setenv("REFACTORY_WORKDIR", str(run))
    monkeypatch.setenv("DSH_WORKFLOW_NODE", json.dumps({"id": node_id, "type": node_type}))
    err, out = io.StringIO(), io.StringIO()
    old_err, old_out = sys.stderr, sys.stdout
    sys.stderr, sys.stdout = err, out
    try:
        code = main([target, "--node", node_id])
    finally:
        sys.stderr, sys.stdout = old_err, old_out
    return code, out.getvalue(), err.getvalue()


def test_spine_route_refine_design_light(tmp_path, monkeypatch):
    """route_refine_design on the light path exits 0 with the light verdict,
    writes every sentinel file and fact it declares, and design_doc's declared
    reads exist on disk."""
    from refactory_dsh import lowering

    mode = DesignMode()
    state = mode.state_dict()
    run = tmp_path / "run"
    lowering.write_declarations(state, run)

    # Write facts: a refine run with light knob.
    (run / "facts.json").write_text(json.dumps({
        "research_scope_reason": REFINE_SCOPE_REASON,
        "delivery_plan": {
            "refine": {"body": "PR description text", "into": "main"},
        },
    }))
    monkeypatch.setenv("REFACTORY_KNOBS", json.dumps({"refine_design": "light"}))

    # Pre-create files that prepare would have written (design_doc reads them).
    (run / "brief.json").write_text(json.dumps({"name": "test", "brief": "test", "check": "true"}))
    (run / "reviews").mkdir(parents=True, exist_ok=True)
    (run / "reviews" / "design-review-feedback.txt").write_text("not run\n")

    target = "refactory_dsh.design_ops:route_refine_design"
    code, stdout, stderr = _run_node_main(run, target, "route_refine_design", "gate", monkeypatch)

    assert code == 0, f"exit {code}: {stderr}"
    # The verdict is the last non-empty line of stdout.
    lines = [l.strip() for l in stdout.strip().splitlines() if l.strip()]
    assert lines[-1] == "light", f"expected verdict 'light', got lines: {lines}"

    # Sentinel files exist.
    assert (run / "research-plan.json").exists()
    assert (run / "pitfalls.json").exists()
    assert (run / "pitfalls.md").exists()
    assert (run / "strategy-plan.json").exists()
    assert (run / "strategy.json").exists()
    assert (run / "research-review.txt").exists()
    assert (run / "pitfalls-review.txt").exists()
    assert (run / "research").is_dir()
    assert (run / "strategy").is_dir()

    # Facts were written.
    facts = json.loads((run / "facts.json").read_text())
    assert facts.get("research") == "(light refine: research was skipped)"
    assert facts.get("strategy_plan") is not None

    # design_doc's declared reads exist.
    decl = lowering.read_declarations(run)
    design_doc_decl = decl.get("design_doc", {})
    for read_file in design_doc_decl.get("reads", []):
        path = run / read_file
        assert path.exists() or path.is_dir(), \
            f"design_doc declared read {read_file!r} is missing after route_refine_design"


def test_spine_route_refine_design_proceed_on_non_refine(tmp_path, monkeypatch):
    """route_refine_design on a non-refine run returns proceed and writes nothing."""
    from refactory_dsh import lowering

    mode = DesignMode()
    state = mode.state_dict()
    run = tmp_path / "run"
    lowering.write_declarations(state, run)

    # Write facts: a non-refine run.
    (run / "facts.json").write_text(json.dumps({
        "research_scope_reason": "",
    }))
    monkeypatch.setenv("REFACTORY_KNOBS", json.dumps({"refine_design": "light"}))

    target = "refactory_dsh.design_ops:route_refine_design"
    code, stdout, stderr = _run_node_main(run, target, "route_refine_design", "gate", monkeypatch)

    assert code == 0, f"exit {code}: {stderr}"
    lines = [l.strip() for l in stdout.strip().splitlines() if l.strip()]
    assert lines[-1] == "proceed", f"expected verdict 'proceed', got lines: {lines}"

    # No sentinel files were written (the downstream nodes write them).
    assert not (run / "research-plan.json").exists()
    assert not (run / "pitfalls.json").exists()
    assert not (run / "strategy.json").exists()
