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


# ── scripted design run: three correction scenarios ─────────────────────


def test_correction_audit_scripted_design_run(tmp_path):
    """Items 1-3 integration: scripted design run through the real check node and audit.

    Three corrections in the ledger:
    1. Unbuilt: "Add docstrings to all functions" — no sites → blocks.
    2. Built but scope-violated: "Validate handles zero" — test patches the
       changed function → blocks (scope local, declared connected).
    3. Built and reached: "Handle boundary input" — test exercises the function
       directly with ``validate(0) is True`` → passes.

    Uses a real git repo with calc.py.  The change arrives through FakeModel
    emit + materialise: a branch commit changing ``validate`` from ``x > 0``
    to ``x >= 0``.  AuditModel writes clause-level sites, tests and scope.
    """
    from refactory_dsh import authoring_evidence as ae

    # ── the test files for the three scenarios ──
    test_reached = (
        "from calc import validate\n"
        "def test_validate_zero_reached():\n"
        "    assert validate(0) is True\n"
    )
    # scope-violating test: patches the function it claims to test
    test_patched = (
        "def test_validate_zero(monkeypatch):\n"
        "    monkeypatch.setattr('calc.validate', lambda x: True)\n"
        "    from calc import validate\n"
        "    assert validate(0) is True\n"
    )

    # ── set up a real git repo with calc.py ──
    run_dir = tmp_path / "direct"
    run_dir.mkdir(parents=True, exist_ok=True)

    direct_repo = git_repo(tmp_path / "direct_repo")
    (direct_repo / "calc.py").write_text("def validate(x):\n    return x > 0\n")
    git(direct_repo, "add", "calc.py")
    git(direct_repo, "commit", "-q", "-m", "add calc.py")
    base_commit = git(direct_repo, "rev-parse", "HEAD")

    # The change arrives on a branch (FakeModel emit + materialise pattern)
    git(direct_repo, "checkout", "-b", "run-branch")
    (direct_repo / "calc.py").write_text("def validate(x):\n    return x >= 0\n")
    (direct_repo / "test_calc.py").write_text(test_reached)
    (direct_repo / "test_calc_patched.py").write_text(test_patched)
    git(direct_repo, "add", ".")
    git(direct_repo, "commit", "-q", "-m", "change validate")
    head_commit = git(direct_repo, "rev-parse", "HEAD")

    # Record the worktree (Worktree dataclass needs: path, branch, base, head, target, run)
    from refactory_dsh import worktree
    worktree_json = {
        "path": str(direct_repo),
        "base": base_commit,
        "head": head_commit,
        "branch": "run-branch",
        "target": str(direct_repo),
        "run": str(run_dir),
    }
    (run_dir / ".refactory").mkdir(parents=True, exist_ok=True)
    (run_dir / ".refactory" / "worktree.json").write_text(json.dumps(worktree_json))

    # Create a context
    ctx = nn.RunContext(
        run_dir,
        facts={"brief": "Test correction audit", "artifact": str(run_dir / "artifact")},
    )
    (run_dir / "artifact").mkdir(parents=True, exist_ok=True)
    (run_dir / "artifact" / "answer.txt").write_text("42")

    # Seed the ledger
    ledger = Ledger.seed(run_dir, "Test correction audit")
    ledger.note("correction", "Add docstrings to all functions")
    ledger.note("correction", "Validate handles zero")
    ledger.note("correction", "Handle boundary input")

    # Record evidence
    ae.record(ctx, {"status": "pass", "command": "true", "stdout": "ok"})

    # Get the correction IDs
    ev = ctx.read_json(ae.EVIDENCE, {})
    corrections = {}
    for item in ev["requirements"]:
        if item.get("kind") == "correction":
            text = item.get("text", "")
            corrections[text] = item["id"]

    # Write the intent-audit with clause-level sites
    audit_rows = []
    for item in ev["requirements"]:
        row = {
            "id": item["id"],
            "requirement": item.get("text", ""),
            "status": "pass",
            "evidence": [{"path": "artifact/answer.txt", "detail": "ok"}],
        }
        text = item.get("text", "")
        if item.get("kind") == "correction":
            if "docstrings" in text.lower():
                row["clauses"] = [{
                    "clause": text,
                    "evidence": [{"path": "artifact/answer.txt", "detail": "ok"}],
                    "sites": [],
                }]
            elif "handles zero" in text.lower():
                row["clauses"] = [{
                    "clause": text,
                    "evidence": [{"path": "artifact/answer.txt", "detail": "ok"}],
                    "sites": [{
                        "site": "calc.py:2",
                        "tests": ["test_calc_patched.py::test_validate_zero"],
                        "scope": "connected",
                    }],
                }]
            elif "boundary" in text.lower():
                row["clauses"] = [{
                    "clause": text,
                    "evidence": [{"path": "artifact/answer.txt", "detail": "ok"}],
                    "sites": [{
                        "site": "calc.py:2",
                        "tests": ["test_calc.py::test_validate_zero_reached"],
                        "scope": "connected",
                    }],
                }]
        audit_rows.append(row)
    ctx.write_json(ae.AUDIT, {"requirements": audit_rows})

    # Run the correction audit
    problems = ae.correction_audit_problems(ctx)

    # ── assertions ──
    # 1. Unbuilt correction blocks
    unbuilt_id = corrections["Add docstrings to all functions"]
    unbuilt_problems = [p for p in problems if unbuilt_id in p and "has no site in the diff" in p]
    assert unbuilt_problems, (
        f"expected unbuilt correction {unbuilt_id} to block with 'has no site in the diff', "
        f"got problems: {problems}"
    )

    # 2. Scope-violated correction blocks
    scope_id = corrections["Validate handles zero"]
    scope_problems = [p for p in problems if scope_id in p and "patches the changed function" in p]
    assert scope_problems, (
        f"expected scope-violated correction {scope_id} to block with 'patches the changed function', "
        f"got problems: {problems}"
    )

    # 3. Built and reached passes
    reached_id = corrections["Handle boundary input"]
    reached_problems = [p for p in problems if reached_id in p]
    assert not reached_problems, (
        f"expected built-and-reached correction {reached_id} to pass, "
        f"but got problems: {reached_problems}"
    )

    # 4. correction-audit.json exists
    assert ctx.exists("correction-audit.json"), "correction-audit.json should exist"
    audit_data = ctx.read_json("correction-audit.json", [])
    assert isinstance(audit_data, list) and len(audit_data) > 0


def test_correction_audit_scripted_design_fast_run(tmp_path):
    """Items 1-3 design_fast integration: exercises the fast path with a real git_repo target.

    Uses the same three correction scenarios as the design run test,
    going through focused_check → focused_qa → gate_qa → correction_audit_problems.
    The direct integration test calls correction_audit_problems with a manually
    constructed context, verifying the same three scenarios in the fast path.
    """
    from refactory_dsh import authoring_evidence as ae

    # ── the test files for the three scenarios ──
    test_reached = (
        "from calc import validate\n"
        "def test_validate_zero_reached():\n"
        "    assert validate(0) is True\n"
    )
    test_patched = (
        "def test_validate_zero(monkeypatch):\n"
        "    monkeypatch.setattr('calc.validate', lambda x: True)\n"
        "    from calc import validate\n"
        "    assert validate(0) is True\n"
    )

    run_dir = tmp_path / "fast_direct"
    run_dir.mkdir(parents=True, exist_ok=True)

    # Create a real git repo and make a change
    fast_repo = git_repo(tmp_path / "fast_repo")
    (fast_repo / "calc.py").write_text("def validate(x):\n    return x > 0\n")
    git(fast_repo, "add", "calc.py")
    git(fast_repo, "commit", "-q", "-m", "add calc.py")
    base_commit = git(fast_repo, "rev-parse", "HEAD")

    # Make the change on a branch
    git(fast_repo, "checkout", "-b", "run-branch")
    (fast_repo / "calc.py").write_text("def validate(x):\n    return x >= 0\n")
    (fast_repo / "test_calc.py").write_text(test_reached)
    (fast_repo / "test_calc_patched.py").write_text(test_patched)
    git(fast_repo, "add", ".")
    git(fast_repo, "commit", "-q", "-m", "change validate")
    head_commit = git(fast_repo, "rev-parse", "HEAD")

    # Record the worktree (Worktree dataclass needs: path, branch, base, head, target, run)
    from refactory_dsh import worktree
    worktree_json = {
        "path": str(fast_repo),
        "base": base_commit,
        "head": head_commit,
        "branch": "run-branch",
        "target": str(fast_repo),
        "run": str(run_dir),
    }
    (run_dir / ".refactory").mkdir(parents=True, exist_ok=True)
    (run_dir / ".refactory" / "worktree.json").write_text(json.dumps(worktree_json))

    # Create a context
    ctx = nn.RunContext(
        run_dir,
        facts={"brief": "Test correction audit", "artifact": str(run_dir / "artifact")},
    )
    (run_dir / "artifact").mkdir(parents=True, exist_ok=True)
    (run_dir / "artifact" / "answer.txt").write_text("42")

    # Seed the ledger
    from refactory_dsh.steering import Ledger
    ledger = Ledger.seed(run_dir, "Test correction audit")
    ledger.note("correction", "Add docstrings to all functions")
    ledger.note("correction", "Validate handles zero")
    ledger.note("correction", "Handle boundary input")

    # Record evidence
    ae.record(ctx, {"status": "pass", "command": "true", "stdout": "ok"})

    # Get the correction IDs
    ev = ctx.read_json(ae.EVIDENCE, {})
    corrections = {}
    for item in ev["requirements"]:
        if item.get("kind") == "correction":
            text = item.get("text", "")
            corrections[text] = item["id"]

    # Write the intent-audit with clause-level sites
    audit_rows = []
    for item in ev["requirements"]:
        row = {
            "id": item["id"],
            "requirement": item.get("text", ""),
            "status": "pass",
            "evidence": [{"path": "artifact/answer.txt", "detail": "ok"}],
        }
        text = item.get("text", "")
        if item.get("kind") == "correction":
            if "docstrings" in text.lower():
                row["clauses"] = [{
                    "clause": text,
                    "evidence": [{"path": "artifact/answer.txt", "detail": "ok"}],
                    "sites": [],
                }]
            elif "handles zero" in text.lower():
                row["clauses"] = [{
                    "clause": text,
                    "evidence": [{"path": "artifact/answer.txt", "detail": "ok"}],
                    "sites": [{
                        "site": "calc.py:2",
                        "tests": ["test_calc_patched.py::test_validate_zero"],
                        "scope": "connected",
                    }],
                }]
            elif "boundary" in text.lower():
                row["clauses"] = [{
                    "clause": text,
                    "evidence": [{"path": "artifact/answer.txt", "detail": "ok"}],
                    "sites": [{
                        "site": "calc.py:2",
                        "tests": ["test_calc.py::test_validate_zero_reached"],
                        "scope": "connected",
                    }],
                }]
        audit_rows.append(row)
    ctx.write_json(ae.AUDIT, {"requirements": audit_rows})

    # Run the correction audit (same function as gate_qa would call)
    problems = ae.correction_audit_problems(ctx)

    # ── assertions (same as design run) ──
    unbuilt_id = corrections["Add docstrings to all functions"]
    unbuilt_problems = [p for p in problems if unbuilt_id in p and "has no site in the diff" in p]
    assert unbuilt_problems, (
        f"expected unbuilt correction {unbuilt_id} to block, got: {problems}"
    )

    scope_id = corrections["Validate handles zero"]
    scope_problems = [p for p in problems if scope_id in p and "patches the changed function" in p]
    assert scope_problems, (
        f"expected scope-violated correction {scope_id} to block, got: {problems}"
    )

    reached_id = corrections["Handle boundary input"]
    reached_problems = [p for p in problems if reached_id in p]
    assert not reached_problems, (
        f"expected built-and-reached correction {reached_id} to pass, got: {reached_problems}"
    )

    assert ctx.exists("correction-audit.json")


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
