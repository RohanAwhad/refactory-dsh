"""Tests for per-correction audit, evidence scope and site-revert checks.

Exercises items 1-3 of #431: per-correction audit at the gate, AST-based
scope enforcement, and mechanical site-revert testing.
"""
import json
import os
from pathlib import Path

import pytest

from refactory_dsh import authoring_evidence as evidence, nn
from refactory_dsh.steering import Ledger
from tests_support import git, git_repo


# ── helpers ──────────────────────────────────────────────────────────────


def _audit_ctx(tmp_path, *, with_base=True):
    """A minimal context with brief, evidence, and optionally a git repo."""
    root = tmp_path / "artifact"
    root.mkdir()
    (root / "answer.txt").write_text("42")
    ctx = nn.RunContext(
        tmp_path,
        facts={"brief": "Test correction audit", "artifact": str(root)},
    )
    ctx.write_json("brief.json", {"brief": ctx.facts["brief"]})
    Ledger.seed(tmp_path, ctx.facts["brief"])
    evidence.record(ctx, {"status": "pass", "command": "true", "stdout": "ok"})
    return ctx


def _write_audit_with_corrections(ctx, corrections):
    """Write an intent-audit.json with correction rows.

    *corrections* is a list of dicts with keys:
      id, clause, sites (list of {site, tests, scope}), kind (optional)
    """
    baseline = ctx.read_json(evidence.EVIDENCE, {})
    rows = []
    for item in baseline.get("requirements", []):
        row = {
            "id": item["id"],
            "requirement": item.get("text", ""),
            "status": "pass",
            "evidence": [{"path": "artifact/answer.txt", "detail": "ok"}],
        }
        if item.get("kind") in ("correction", "constraint"):
            # Find matching correction spec
            spec = next((c for c in corrections if c["id"] == item["id"]), None)
            if spec:
                clause_obj = {
                    "clause": spec.get("clause", item.get("text", "")),
                    "evidence": [{"path": "artifact/answer.txt", "detail": "ok"}],
                    "sites": spec.get("sites", []),
                }
                if "kind" in spec:
                    clause_obj["kind"] = spec["kind"]
                row["clauses"] = [clause_obj]
            else:
                row["clauses"] = [{
                    "clause": item.get("text", ""),
                    "evidence": [{"path": "artifact/answer.txt", "detail": "ok"}],
                    "sites": [],
                }]
        rows.append(row)
    ctx.write_json(evidence.AUDIT, {"requirements": rows})


# ── dm-114: exclusion message specificity ────────────────────────────────


def test_exclusion_unknown_user_change_id(tmp_path):
    ctx = _audit_ctx(tmp_path)
    Ledger(tmp_path).note("correction", "Add docstrings")
    evidence.record(ctx, {"status": "pass", "command": "true", "stdout": "ok"})
    audit = {
        "requirements": [
            *[{"id": item["id"], "requirement": item.get("text", ""), "status": "pass",
               "evidence": [{"path": "artifact/answer.txt", "detail": "ok"}],
               **({"clauses": [{"clause": item.get("text", ""),
                                "evidence": [{"path": "artifact/answer.txt", "detail": "ok"}]}]}
                  if item.get("kind") in ("correction", "constraint") else {})}
              for item in ctx.read_json(evidence.EVIDENCE)["requirements"]
              if item["id"] != next(
                  i["id"] for i in ctx.read_json(evidence.EVIDENCE)["requirements"]
                  if i.get("kind") == "correction"
              )],
            {"id": next(
                i["id"] for i in ctx.read_json(evidence.EVIDENCE)["requirements"]
                if i.get("kind") == "correction"
            ), "requirement": "Add docstrings", "status": "excluded",
             "user_change": "nonexistent-id", "user_quote": "something",
             "reason": "not needed"},
        ],
    }
    ctx.write_json(evidence.AUDIT, audit)
    problems = evidence.audit_problems(ctx)
    assert any("unknown user_change" in p for p in problems), f"expected 'unknown user_change' in {problems}"
    assert any("reviewers cannot waive requirements" in p for p in problems)


def test_exclusion_quote_not_substring(tmp_path):
    ctx = _audit_ctx(tmp_path)
    Ledger(tmp_path).note("correction", "Add docstrings to all public functions")
    evidence.record(ctx, {"status": "pass", "command": "true", "stdout": "ok"})
    corr_id = next(
        i["id"] for i in ctx.read_json(evidence.EVIDENCE)["requirements"]
        if i.get("kind") == "correction"
    )
    audit = {
        "requirements": [
            *[{"id": item["id"], "requirement": item.get("text", ""), "status": "pass",
               "evidence": [{"path": "artifact/answer.txt", "detail": "ok"}],
               **({"clauses": [{"clause": item.get("text", ""),
                                "evidence": [{"path": "artifact/answer.txt", "detail": "ok"}]}]}
                  if item.get("kind") in ("correction", "constraint") else {})}
              for item in ctx.read_json(evidence.EVIDENCE)["requirements"]
              if item["id"] != corr_id],
            {"id": corr_id, "requirement": "Add docstrings", "status": "excluded",
             "user_change": "brief", "user_quote": "this text is not in the brief at all...",
             "reason": "not needed"},
        ],
    }
    ctx.write_json(evidence.AUDIT, audit)
    problems = evidence.audit_problems(ctx)
    assert any("not an exact substring" in p for p in problems), f"expected 'not an exact substring' in {problems}"
    assert any("reviewers cannot waive requirements" in p for p in problems)


def test_exclusion_missing_reason(tmp_path):
    ctx = _audit_ctx(tmp_path)
    Ledger(tmp_path).note("correction", "Add docstrings to all functions")
    evidence.record(ctx, {"status": "pass", "command": "true", "stdout": "ok"})
    brief_text = ctx.read_json(evidence.EVIDENCE)["requirements"][0].get("text", "")
    corr_id = next(
        i["id"] for i in ctx.read_json(evidence.EVIDENCE)["requirements"]
        if i.get("kind") == "correction"
    )
    audit = {
        "requirements": [
            *[{"id": item["id"], "requirement": item.get("text", ""), "status": "pass",
               "evidence": [{"path": "artifact/answer.txt", "detail": "ok"}],
               **({"clauses": [{"clause": item.get("text", ""),
                                "evidence": [{"path": "artifact/answer.txt", "detail": "ok"}]}]}
                  if item.get("kind") in ("correction", "constraint") else {})}
              for item in ctx.read_json(evidence.EVIDENCE)["requirements"]
              if item["id"] != corr_id],
            {"id": corr_id, "requirement": "Add docstrings", "status": "excluded",
             "user_change": "brief",
             "user_quote": brief_text[:20] if brief_text else "Test",
             "reason": ""},
        ],
    }
    ctx.write_json(evidence.AUDIT, audit)
    problems = evidence.audit_problems(ctx)
    assert any("missing reason" in p for p in problems), f"expected 'missing reason' in {problems}"
    assert any("reviewers cannot waive requirements" in p for p in problems)


def test_exclusion_two_conditions_failing(tmp_path):
    ctx = _audit_ctx(tmp_path)
    Ledger(tmp_path).note("correction", "Add docstrings to all functions")
    evidence.record(ctx, {"status": "pass", "command": "true", "stdout": "ok"})
    corr_id = next(
        i["id"] for i in ctx.read_json(evidence.EVIDENCE)["requirements"]
        if i.get("kind") == "correction"
    )
    audit = {
        "requirements": [
            *[{"id": item["id"], "requirement": item.get("text", ""), "status": "pass",
               "evidence": [{"path": "artifact/answer.txt", "detail": "ok"}],
               **({"clauses": [{"clause": item.get("text", ""),
                                "evidence": [{"path": "artifact/answer.txt", "detail": "ok"}]}]}
                  if item.get("kind") in ("correction", "constraint") else {})}
              for item in ctx.read_json(evidence.EVIDENCE)["requirements"]
              if item["id"] != corr_id],
            {"id": corr_id, "requirement": "Add docstrings", "status": "excluded",
             "user_change": "brief",
             "user_quote": "",
             "reason": ""},
        ],
    }
    ctx.write_json(evidence.AUDIT, audit)
    problems = evidence.audit_problems(ctx)
    matching = [p for p in problems if "reviewers cannot waive" in p]
    assert matching, f"expected a problem with 'reviewers cannot waive' in {problems}"
    msg = matching[0]
    assert "not an exact substring" in msg, f"expected 'not an exact substring' in {msg}"
    assert "missing reason" in msg, f"expected 'missing reason' in {msg}"


# ── correction_audit_problems: no-base rule ──────────────────────────────


def test_correction_audit_no_base_does_not_block(tmp_path):
    """Modes without a base skip diff/revert checks (create, edit)."""
    ctx = _audit_ctx(tmp_path, with_base=False)
    Ledger(tmp_path).note("correction", "Fix the thing")
    evidence.record(ctx, {"status": "pass", "command": "true", "stdout": "ok"})
    corr_id = next(
        i["id"] for i in ctx.read_json(evidence.EVIDENCE)["requirements"]
        if i.get("kind") == "correction"
    )
    _write_audit_with_corrections(ctx, [{
        "id": corr_id, "clause": "Fix the thing", "sites": [],
    }])
    problems = evidence.correction_audit_problems(ctx)
    # Without a base, "no site" does not block
    assert not any("has no site" in p for p in problems), (
        f"should not block without a base: {problems}"
    )


# ── correction_audit_problems: unbuilt correction blocks ─────────────────


def test_correction_audit_unbuilt_blocks(tmp_path, monkeypatch):
    """A correction with no site in the diff blocks."""
    # Create a git repo so worktree.recorded returns something
    repo = tmp_path / "repo"
    git_repo(repo)
    ctx = _audit_ctx(tmp_path)
    # Make worktree.recorded return a valid worktree
    from refactory_dsh import worktree
    from dataclasses import dataclass

    @dataclass
    class FakeWorktree:
        path: str
        base: str
        head: str
        branch: str
        target: str

    fake_wt = FakeWorktree(
        path=str(repo), base="HEAD~1" if False else "HEAD",
        head="HEAD", branch="main", target=str(repo),
    )
    monkeypatch.setattr(worktree, "recorded", lambda p: fake_wt)

    Ledger(tmp_path).note("correction", "Add docstrings to all functions")
    evidence.record(ctx, {"status": "pass", "command": "true", "stdout": "ok"})
    corr_id = next(
        i["id"] for i in ctx.read_json(evidence.EVIDENCE)["requirements"]
        if i.get("kind") == "correction"
    )
    _write_audit_with_corrections(ctx, [{
        "id": corr_id, "clause": "Add docstrings", "sites": [],
    }])
    problems = evidence.correction_audit_problems(ctx)
    assert any("has no site in the diff" in p for p in problems), (
        f"expected blocking for unbuilt correction: {problems}"
    )


# ── _parse_collection_errors: vitest patterns ───────────────────────────


def test_parse_collection_errors_vitest_patterns():
    from refactory_dsh.base_check import _parse_collection_errors

    output = (
        "Failed to collect tests/foo.spec.ts\n"
        "Error: Cannot find module 'bar' from tests/bar.spec.ts\n"
    )
    files, has_unlocatable = _parse_collection_errors(output)
    assert "tests/foo.spec.ts" in files
    assert "tests/bar.spec.ts" in files


def test_parse_collection_errors_export_named():
    from refactory_dsh.base_check import _parse_collection_errors

    output = (
        "tests/helper.spec.ts: does not provide an export named 'parseConfig'\n"
    )
    files, has_unlocatable = _parse_collection_errors(output)
    # The importing file should be found from surrounding output
    assert "tests/helper.spec.ts" in files or has_unlocatable


def test_parse_collection_errors_failed_to_load_url():
    from refactory_dsh.base_check import _parse_collection_errors

    output = (
        "Failed to load url ./src/config (resolved id: /app/src/config) in tests/config.spec.ts\n"
    )
    files, has_unlocatable = _parse_collection_errors(output)
    assert "tests/config.spec.ts" in files
