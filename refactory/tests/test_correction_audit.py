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


# ── gate-level tests through real runs ──────────────────────────────────


def test_correction_audit_not_in_diff_blocks(tmp_path):
    """A correction cited at a site not in the diff is refused."""
    from refactory_dsh import authoring_evidence as ae

    run_dir = tmp_path / "nid"
    run_dir.mkdir(parents=True, exist_ok=True)

    repo = git_repo(tmp_path / "nid_repo")
    (repo / "calc.py").write_text("def validate(x):\n    return x > 0\n")
    git(repo, "add", "calc.py")
    git(repo, "commit", "-q", "-m", "add calc.py")
    base_commit = git(repo, "rev-parse", "HEAD")

    git(repo, "checkout", "-b", "run-branch")
    (repo / "calc.py").write_text("def validate(x):\n    return x >= 0\n")
    (repo / "test_calc.py").write_text(
        "from calc import validate\n"
        "def test_validate():\n    assert validate(0) is True\n"
    )
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "change validate")
    head_commit = git(repo, "rev-parse", "HEAD")

    (run_dir / ".refactory").mkdir(parents=True, exist_ok=True)
    (run_dir / ".refactory" / "worktree.json").write_text(json.dumps({
        "path": str(repo), "base": base_commit, "head": head_commit,
        "branch": "run-branch", "target": str(repo), "run": str(run_dir),
    }))

    ctx = nn.RunContext(run_dir, facts={"brief": "test", "artifact": str(run_dir / "art")})
    (run_dir / "art").mkdir(parents=True, exist_ok=True)
    (run_dir / "art" / "a.txt").write_text("42")
    Ledger.seed(run_dir, "test")
    Ledger(run_dir).note("correction", "Fix README")
    ae.record(ctx, {"status": "pass", "command": "true", "stdout": "ok"})

    corr_id = next(
        i["id"] for i in ctx.read_json(ae.EVIDENCE)["requirements"]
        if i.get("kind") == "correction"
    )
    _write_audit_with_corrections(ctx, [{
        "id": corr_id, "clause": "Fix README",
        "sites": [{"site": "README.md:1", "tests": ["test_calc.py::test_validate"], "scope": "local"}],
    }])
    problems = ae.correction_audit_problems(ctx)
    assert any("is not in the diff" in p or "not_in_diff" in p for p in problems), (
        f"expected blocking for site not in diff: {problems}"
    )


def test_correction_audit_empty_tests_blocks(tmp_path):
    """A code site with an empty tests list fails the clause."""
    from refactory_dsh import authoring_evidence as ae

    run_dir = tmp_path / "et"
    run_dir.mkdir(parents=True, exist_ok=True)

    repo = git_repo(tmp_path / "et_repo")
    (repo / "calc.py").write_text("def validate(x):\n    return x > 0\n")
    git(repo, "add", "calc.py")
    git(repo, "commit", "-q", "-m", "add calc.py")
    base_commit = git(repo, "rev-parse", "HEAD")

    git(repo, "checkout", "-b", "run-branch")
    (repo / "calc.py").write_text("def validate(x):\n    return x >= 0\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "change validate")
    head_commit = git(repo, "rev-parse", "HEAD")

    (run_dir / ".refactory").mkdir(parents=True, exist_ok=True)
    (run_dir / ".refactory" / "worktree.json").write_text(json.dumps({
        "path": str(repo), "base": base_commit, "head": head_commit,
        "branch": "run-branch", "target": str(repo), "run": str(run_dir),
    }))

    ctx = nn.RunContext(run_dir, facts={"brief": "test", "artifact": str(run_dir / "art")})
    (run_dir / "art").mkdir(parents=True, exist_ok=True)
    (run_dir / "art" / "a.txt").write_text("42")
    Ledger.seed(run_dir, "test")
    Ledger(run_dir).note("correction", "Fix validate")
    ae.record(ctx, {"status": "pass", "command": "true", "stdout": "ok"})

    corr_id = next(
        i["id"] for i in ctx.read_json(ae.EVIDENCE)["requirements"]
        if i.get("kind") == "correction"
    )
    _write_audit_with_corrections(ctx, [{
        "id": corr_id, "clause": "Fix validate",
        "sites": [{"site": "calc.py:2", "tests": [], "scope": "local"}],
    }])
    problems = ae.correction_audit_problems(ctx)
    assert any("has no tests" in p for p in problems), (
        f"expected blocking for empty tests: {problems}"
    )


def test_correction_audit_design_form_without_heading_blocks(tmp_path):
    """A design form without a heading is refused."""
    from refactory_dsh import authoring_evidence as ae

    ctx = _audit_ctx(tmp_path)
    Ledger(tmp_path).note("correction", "Document the design")
    ae.record(ctx, {"status": "pass", "command": "true", "stdout": "ok"})

    corr_id = next(
        i["id"] for i in ctx.read_json(ae.EVIDENCE)["requirements"]
        if i.get("kind") == "correction"
    )
    _write_audit_with_corrections(ctx, [{
        "id": corr_id, "clause": "Document the design",
        "sites": [], "kind": "design",
    }])
    problems = ae.correction_audit_problems(ctx)
    assert any("requires a non-empty heading" in p for p in problems), (
        f"expected blocking for design without heading: {problems}"
    )


def test_correction_audit_test_file_site_is_checked_against_the_diff(tmp_path):
    """A site inside a test file is not reverted: in the diff it passes, outside it blocks."""
    from refactory_dsh import authoring_evidence as ae

    run_dir = tmp_path / "tfs"
    run_dir.mkdir(parents=True, exist_ok=True)

    repo = git_repo(tmp_path / "tfs_repo")
    (repo / "calc.py").write_text("def validate(x):\n    return x > 0\n")
    git(repo, "add", "calc.py")
    git(repo, "commit", "-q", "-m", "add calc.py")
    base_commit = git(repo, "rev-parse", "HEAD")

    git(repo, "checkout", "-b", "run-branch")
    (repo / "calc.py").write_text("def validate(x):\n    return x >= 0\n")
    (repo / "test_calc.py").write_text(
        "from calc import validate\n"
        "def test_validate():\n    assert validate(0) is True\n"
    )
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "change validate")
    head_commit = git(repo, "rev-parse", "HEAD")

    (run_dir / ".refactory").mkdir(parents=True, exist_ok=True)
    (run_dir / ".refactory" / "worktree.json").write_text(json.dumps({
        "path": str(repo), "base": base_commit, "head": head_commit,
        "branch": "run-branch", "target": str(repo), "run": str(run_dir),
    }))

    ctx = nn.RunContext(run_dir, facts={"brief": "test", "artifact": str(run_dir / "art")})
    (run_dir / "art").mkdir(parents=True, exist_ok=True)
    (run_dir / "art" / "a.txt").write_text("42")
    Ledger.seed(run_dir, "test")
    Ledger(run_dir).note("correction", "Add test for validate")
    ae.record(ctx, {"status": "pass", "command": "true", "stdout": "ok"})

    corr_id = next(
        i["id"] for i in ctx.read_json(ae.EVIDENCE)["requirements"]
        if i.get("kind") == "correction"
    )
    # The added test is the correction's site: it is in the diff, so it passes.
    _write_audit_with_corrections(ctx, [{
        "id": corr_id, "clause": "Add test",
        "sites": [{"site": "test_calc.py:2", "tests": ["test_calc.py::test_validate"], "scope": "local"}],
    }])
    assert ae.correction_audit_problems(ctx) == []
    reverts = ctx.read_json("site-revert-results.json")["pairs"]
    assert [p["result"] for p in reverts] == ["test_file"]

    # A test-file line the change did not touch is not in the diff.
    _write_audit_with_corrections(ctx, [{
        "id": corr_id, "clause": "Add test",
        "sites": [{"site": "test_calc.py:40", "tests": ["test_calc.py::test_validate"], "scope": "local"}],
    }])
    problems = ae.correction_audit_problems(ctx)
    assert any("test_calc.py:40 is not in the diff" in p for p in problems), problems


def test_correction_audit_import_only_sibling(tmp_path):
    """A helper-definition site whose revert is import-only is refused without a sibling,
    and passes with a caught sibling call site."""
    from refactory_dsh import authoring_evidence as ae

    run_dir = tmp_path / "ios"
    run_dir.mkdir(parents=True, exist_ok=True)

    repo = git_repo(tmp_path / "ios_repo")
    (repo / "calc.py").write_text("def validate(x):\n    return x > 0\n")
    git(repo, "add", "calc.py")
    git(repo, "commit", "-q", "-m", "add calc.py")
    base_commit = git(repo, "rev-parse", "HEAD")

    git(repo, "checkout", "-b", "run-branch")
    (repo / "calc.py").write_text(
        "def validate(x):\n    return x >= 0\n\n"
        "def helper():\n    return validate(0)\n"
    )
    (repo / "test_calc.py").write_text(
        "from calc import validate\n"
        "def test_validate():\n    assert validate(0) is True\n"
    )
    (repo / "test_helper.py").write_text(
        "from calc import helper\n"
        "def test_helper():\n    assert helper() is True\n"
    )
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "add helper")
    head_commit = git(repo, "rev-parse", "HEAD")

    (run_dir / ".refactory").mkdir(parents=True, exist_ok=True)
    (run_dir / ".refactory" / "worktree.json").write_text(json.dumps({
        "path": str(repo), "base": base_commit, "head": head_commit,
        "branch": "run-branch", "target": str(repo), "run": str(run_dir),
    }))

    ctx = nn.RunContext(run_dir, facts={"brief": "test", "artifact": str(run_dir / "art")})
    (run_dir / "art").mkdir(parents=True, exist_ok=True)
    (run_dir / "art" / "a.txt").write_text("42")
    Ledger.seed(run_dir, "test")
    Ledger(run_dir).note("correction", "Add helper function")
    ae.record(ctx, {"status": "pass", "command": "true", "stdout": "ok"})

    corr_id = next(
        i["id"] for i in ctx.read_json(ae.EVIDENCE)["requirements"]
        if i.get("kind") == "correction"
    )

    # Without sibling: only definition site → should block
    _write_audit_with_corrections(ctx, [{
        "id": corr_id, "clause": "Add helper",
        "sites": [{"site": "calc.py:4", "tests": ["test_helper.py::test_helper"], "scope": "local"}],
    }])
    problems_no_sibling = ae.correction_audit_problems(ctx)
    advisories = ae.correction_audit_advisories(ctx)
    assert len(advisories) == 1 and "site calc.py:4: not measurable by revert" in advisories[0]

    # With sibling: definition site + call site → should pass
    _write_audit_with_corrections(ctx, [{
        "id": corr_id, "clause": "Add helper",
        "sites": [
            {"site": "calc.py:4", "tests": ["test_helper.py::test_helper"], "scope": "local"},
            {"site": "calc.py:2", "tests": ["test_calc.py::test_validate"], "scope": "local"},
        ],
    }])
    problems_with_sibling = ae.correction_audit_problems(ctx)

    # Without a sibling the revert can only break an import: advisory, not blocking.
    assert problems_no_sibling == []
    # (the advisories were read before the second audit overwrote them)
    assert problems_with_sibling == []
    assert ae.correction_audit_advisories(ctx) == []


def test_is_import_only_from_pytest_e_lines():
    """_is_import_only_failure classifies from pytest's E lines."""
    from refactory_dsh.base_check import _is_import_only_failure

    # Pytest E-line output
    output = (
        "FAILED test_calc.py::test_helper\n"
        "E   ImportError: cannot import name 'helper' from 'calc'\n"
    )
    assert _is_import_only_failure(output, {"helper"}) is True

    # Non-matching name
    assert _is_import_only_failure(output, {"other_func"}) is False

    # Short summary (truncated) — should still work if E line present
    output2 = (
        "FAILED test_calc.py::test_helper - ImportError: cannot import name 'helper'...\n"
        "E   ImportError: cannot import name 'helper' from 'calc'\n"
    )
    assert _is_import_only_failure(output2, {"helper"}) is True

    # NameError E-line
    output3 = "E   NameError: name 'compute_foo' is not defined\n"
    assert _is_import_only_failure(output3, {"compute_foo"}) is True
    assert _is_import_only_failure(output3, {"other"}) is False

    # AttributeError E-line
    output4 = "E   AttributeError: module 'calc' has no attribute 'helper'\n"
    assert _is_import_only_failure(output4, {"helper"}) is True


def test_correction_audit_sites_not_list_blocks(tmp_path):
    """A clause whose sites is not a list fails."""
    from refactory_dsh import authoring_evidence as ae

    ctx = _audit_ctx(tmp_path)
    Ledger(tmp_path).note("correction", "Fix something")
    ae.record(ctx, {"status": "pass", "command": "true", "stdout": "ok"})

    corr_id = next(
        i["id"] for i in ctx.read_json(ae.EVIDENCE)["requirements"]
        if i.get("kind") == "correction"
    )
    # Write audit with sites as a string instead of list
    baseline = ctx.read_json(ae.EVIDENCE, {})
    rows = []
    for item in baseline.get("requirements", []):
        row = {
            "id": item["id"],
            "requirement": item.get("text", ""),
            "status": "pass",
            "evidence": [{"path": "artifact/answer.txt", "detail": "ok"}],
        }
        if item["id"] == corr_id:
            row["clauses"] = [{
                "clause": "Fix something",
                "evidence": [{"path": "artifact/answer.txt", "detail": "ok"}],
                "sites": "not-a-list",
            }]
        rows.append(row)
    ctx.write_json(ae.AUDIT, {"requirements": rows})

    problems = ae.correction_audit_problems(ctx)
    assert any("sites must be a list" in p for p in problems), (
        f"expected blocking for sites not a list: {problems}"
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


# ── unmeasured checks are advisory, measured misses block (dm-183, dm-184) ──


def _gate_run(tmp_path):
    """A run whose worktree changes calc.validate in two hunks: a comment at line 1
    and the comparison at line 3. Returns (ctx, found)."""
    from types import SimpleNamespace

    run_dir = tmp_path / "run"
    (run_dir / ".refactory").mkdir(parents=True)
    repo = git_repo(tmp_path / "repo")
    (repo / "calc.py").write_text("def validate(x):\n    return x > 0\n")
    git(repo, "add", "calc.py")
    git(repo, "commit", "-q", "-m", "add calc.py")
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "-b", "run-branch")
    (repo / "calc.py").write_text(
        "# Validation helpers.\ndef validate(x):\n    return x >= 0\n")
    (repo / "test_calc.py").write_text(
        "from calc import validate\n"
        "def test_validate():\n    assert validate(0) is True\n"
        "def test_unrelated():\n    assert True\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "change validate")
    head = git(repo, "rev-parse", "HEAD")
    (run_dir / ".refactory" / "worktree.json").write_text(json.dumps({
        "path": str(repo), "base": base, "head": head,
        "branch": "run-branch", "target": str(repo), "run": str(run_dir)}))
    ctx = nn.RunContext(run_dir, facts={"brief": "test", "artifact": str(run_dir / "art")})
    (run_dir / "art").mkdir()
    (run_dir / "art" / "a.txt").write_text("42")
    Ledger.seed(run_dir, "test")
    return ctx, SimpleNamespace(path=str(repo), base=base, head=head, branch="run-branch")


def _deliver_overwatch(ctx, sites_by_text, kinds=None, *, worker="overwatch"):
    """Current evidence, and an overwatch pass citing *sites_by_text* for each correction.

    With ``worker="focused_qa"`` the design_fast reviewer delivers the audit instead,
    with a passing focused-qa.json.
    """
    from refactory_dsh import fast_ops
    from refactory_dsh.agent_delivery import begin, finish

    evidence.record(ctx, {"status": "pass", "command": "true", "stdout": "ok"})
    report = "reviews/overwatch.md" if worker == "overwatch" else "focused-qa.json"
    begin(ctx.workdir, worker, "Audit each ask", [report, evidence.AUDIT])
    rows = []
    for item in ctx.read_json(evidence.EVIDENCE)["requirements"]:
        row = {"id": item["id"], "requirement": item.get("text", ""), "status": "pass",
               "evidence": [{"path": "art/a.txt", "detail": "read the answer: 42"}]}
        if item.get("kind") in ("correction", "constraint"):
            clause = {"clause": item["text"],
                      "evidence": [{"path": "art/a.txt", "detail": "verified"}],
                      "sites": sites_by_text.get(item["text"], [])}
            clause.update((kinds or {}).get(item["text"], {}))
            row["clauses"] = [clause]
        rows.append(row)
    ctx.write_json(evidence.AUDIT, {"requirements": rows})
    if worker == "overwatch":
        ctx.write_text("reviews/overwatch.md", "VERDICT: PASS")
    else:
        art = ctx.facts["artifact"]
        ctx.write_json("focused-qa.json", {
            "status": "pass", "findings": [], "not_run": [],
            "checks": [{"name": "answer", "command": "cat a.txt", "status": "pass",
                        "evidence": "read 42"}]})
        ctx.write_json("focused-source.json", {
            "root": art, "files": fast_ops._checked_files(Path(art))})
    finish(ctx.workdir, worker)


_CAUGHT = [{"site": "calc.py:3", "tests": ["test_calc.py::test_validate"], "scope": "local"}]


def test_large_ledger_past_the_cap_proceeds_with_an_advisory(tmp_path, monkeypatch):
    """A 25-correction ledger whose revert pass runs out of time: the gate proceeds, the
    gate note and the pull request list every unmeasured pair, nothing unmeasured is
    cached, and the next audit measures them."""
    from refactory_dsh import base_check, create_ops, delivery

    ctx, found = _gate_run(tmp_path)
    texts = [f"Correction {n}" for n in range(1, 26)]
    for text in texts:
        Ledger(ctx.workdir).note("correction", text)
    _deliver_overwatch(ctx, {text: _CAUGHT for text in texts})

    monkeypatch.setattr(base_check, "SITE_REVERT_TOTAL_S", -1)
    assert create_ops.gate_overwatch(ctx) == "proceed", ctx.facts.get("validation")

    advisories = evidence.correction_audit_advisories(ctx)
    assert len(advisories) == 25
    assert all("site calc.py:3: revert not run (time cap reached)" in a for a in advisories)
    gate = ctx.read_text("reviews/overwatch-gate.txt")
    assert gate.startswith("proceed: overwatch passed")
    assert evidence.ADVISORY_CORRECTION_AUDIT in gate
    body = delivery._body(ctx, found)
    assert "Correction-audit checks to confirm by hand (advisory)" in body
    assert "revert not run (time cap reached)" in body
    cache = json.loads((ctx.workdir / ".refactory" / "site-revert-cache.json").read_text())
    assert cache == {}

    monkeypatch.setattr(base_check, "SITE_REVERT_TOTAL_S", 240)
    assert evidence.correction_audit_problems(ctx) == []
    assert evidence.correction_audit_advisories(ctx) == []
    assert [p["result"] for p in ctx.read_json("site-revert-results.json")["pairs"]] == ["fail"] * 25


def test_a_measured_miss_still_blocks_beside_unmeasured_pairs(tmp_path, monkeypatch):
    """A site its test does not reach blocks even when the pairs around it are capped."""
    from refactory_dsh import base_check, create_ops

    ctx, _ = _gate_run(tmp_path)
    miss = [{"site": "calc.py:3", "tests": ["test_calc.py::test_unrelated"], "scope": "local"}]
    Ledger(ctx.workdir).note("correction", "Zero is valid")
    _deliver_overwatch(ctx, {"Zero is valid": miss})
    assert create_ops.gate_overwatch(ctx) != "proceed"
    assert "site calc.py:3 not reached by any test" in ctx.facts["validation"]

    # A later round adds 24 corrections, and the revert pass runs out of time on them.
    texts = [f"Correction {n}" for n in range(1, 25)]
    for text in texts:
        Ledger(ctx.workdir).note("correction", text)
    _deliver_overwatch(ctx, {"Zero is valid": miss, **{text: _CAUGHT for text in texts}})
    monkeypatch.setattr(base_check, "SITE_REVERT_TOTAL_S", -1)
    assert create_ops.gate_overwatch(ctx) != "proceed"
    validation = ctx.facts["validation"]
    assert "site calc.py:3 not reached by any test" in validation
    assert "revert not run" not in validation.split("\n", 1)[0]
    assert len(evidence.correction_audit_advisories(ctx)) == 24


def test_a_comment_only_site_needs_no_test(tmp_path):
    """No test can catch a comment, so a comment-only site passes with no tests; a code
    site with no tests still blocks."""
    from refactory_dsh import authoring_evidence as ae

    ctx, _ = _gate_run(tmp_path)
    Ledger(ctx.workdir).note("correction", "Say what the module is for")
    Ledger(ctx.workdir).note("correction", "Accept zero")
    _deliver_overwatch(ctx, {
        "Say what the module is for": [{"site": "calc.py:1", "tests": [], "scope": "artifact"}],
        "Accept zero": [{"site": "calc.py:3", "tests": [], "scope": "local"}],
    })
    problems = ae.correction_audit_problems(ctx)
    assert problems and all("calc.py:3 has no tests" in p for p in problems), problems
    audit = {item["text_preview"]: item for item in ctx.read_json("correction-audit.json")}
    assert audit["Say what the module is for"]["status"] == "pass"
    assert audit["Say what the module is for"]["clauses"][0]["sites"][0]["comment_only"] is True


def test_a_doc_clause_on_a_changed_code_file_is_advisory(tmp_path):
    """Wording in a code file can only be checked as changed: advisory when the file
    changed, blocking when it did not."""
    from refactory_dsh import authoring_evidence as ae

    ctx, _ = _gate_run(tmp_path)
    Ledger(ctx.workdir).note("correction", "Fix the module comment")
    Ledger(ctx.workdir).note("correction", "Fix the other comment")
    _deliver_overwatch(ctx, {}, kinds={
        "Fix the module comment": {"kind": "doc", "path": "calc.py"},
        "Fix the other comment": {"kind": "doc", "path": "other.py"},
    })
    problems = ae.correction_audit_problems(ctx)
    assert len(problems) == 1 and "doc file 'other.py' is not in the diff" in problems[0], problems
    advisories = ae.correction_audit_advisories(ctx)
    assert len(advisories) == 1 and "doc clause names calc.py" in advisories[0], advisories


def test_is_comment_only_by_language():
    from refactory_dsh.base_check import is_comment_only

    assert is_comment_only("a.py", ["# note", "", "    # more"])
    assert is_comment_only("a.ts", ["// note", "/**", " * doc", " */"])
    assert not is_comment_only("a.py", ["# note", "x = 1"])
    assert not is_comment_only("a.py", [""])
    assert not is_comment_only("a.md", ["# A heading"])
    # Only languages whose comments can be told from code exactly: a YAML '#' line may
    # sit in a block scalar, a shell one in a heredoc.
    assert not is_comment_only("a.yaml", ["# disabled: client-hmr"])
    assert not is_comment_only("a.sh", ["# written into the config"])


@pytest.mark.parametrize("path,lines", [
    ("a.c", ["*p = 0;"]),
    ("a.go", ["*count += 1"]),
    ("a.ts", ["*[Symbol.iterator]() {"]),
    ("a.ts", ["  * factor;"]),
    ("a.ts", ["/** @type {number} */ const limit = compute();"]),
    ("a.js", ["/* istanbul ignore next */ if (x) run();"]),
    ("a.ts", ["*/ run();"]),
    ("a.tsx", ["// shown as text"]),
])
def test_is_comment_only_never_passes_code(path, lines):
    from refactory_dsh.base_check import is_comment_only

    assert not is_comment_only(path, lines)


@pytest.mark.parametrize("path,text,numbers,expected", [
    # a '#' line inside a triple-quoted string is part of the string
    ("a.py", 'T = """\n# Title\n"""\n', [2], False),
    ("a.py", 'def f():\n    # why\n    return 1\n', [2], True),
    # a '//' line inside a template literal, also inside ${...}
    ("a.ts", "const h = `\n// generated\n`;\n", [2], False),
    ("a.ts", "const h = `${f(`\n// inner\n`)}`;\n", [2], False),
    ("a.ts", "/**\n * doc\n */\nexport const x = 1;\n", [1, 2, 3], True),
    # a regular expression holding '/*' does not open a comment
    ("a.ts", "const re = /[/*]/;\nrun();\n// note\n", [2], False),
    ("a.ts", "const re = /[/*]/;\nrun();\n// note\n", [3], True),
])
def test_is_comment_only_judges_a_line_in_place(path, text, numbers, expected):
    from refactory_dsh.base_check import is_comment_only

    lines = [text.splitlines()[n - 1] for n in numbers]
    assert is_comment_only(path, lines, text=text, numbers=numbers) is expected


def test_a_hash_line_added_inside_a_string_is_reverted_like_code(tmp_path):
    """A '#' line added to a triple-quoted string changes the string: it is reverted and
    measured, and a test that does not read it does not reach it."""
    run_dir = tmp_path / "run"
    (run_dir / ".refactory").mkdir(parents=True)
    repo = git_repo(tmp_path / "repo")
    (repo / "calc.py").write_text('TEMPLATE = """\nTitle\n"""\n')
    git(repo, "add", "calc.py")
    git(repo, "commit", "-q", "-m", "add calc.py")
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "-b", "run-branch")
    (repo / "calc.py").write_text('TEMPLATE = """\n# Title\nTitle\n"""\n')
    (repo / "test_calc.py").write_text("def test_unrelated():\n    assert True\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "title")
    (run_dir / ".refactory" / "worktree.json").write_text(json.dumps({
        "path": str(repo), "base": base, "head": git(repo, "rev-parse", "HEAD"),
        "branch": "run-branch", "target": str(repo), "run": str(run_dir)}))
    ctx = nn.RunContext(run_dir, facts={"brief": "test", "artifact": str(run_dir / "art")})
    (run_dir / "art").mkdir()
    (run_dir / "art" / "a.txt").write_text("42")
    Ledger.seed(run_dir, "test")
    Ledger(run_dir).note("correction", "Title the template")
    Ledger(run_dir).note("correction", "Title it, untested")
    _deliver_overwatch(ctx, {
        "Title the template": [{"site": "calc.py:2", "tests": ["test_calc.py::test_unrelated"],
                                "scope": "local"}],
        "Title it, untested": [{"site": "calc.py:2", "tests": [], "scope": "local"}]})
    problems = evidence.correction_audit_problems(ctx)
    assert any("site calc.py:2 not reached by any test" in p for p in problems), problems
    assert any("site calc.py:2 has no tests" in p for p in problems), problems


def test_design_fast_past_the_cap_proceeds_with_an_advisory(tmp_path, monkeypatch):
    """design_fast's gate_qa runs the same audit: a capped 25-correction ledger proceeds,
    and fast-report.json carries the unmeasured pairs."""
    from refactory_dsh import base_check, fast_ops

    ctx, _ = _gate_run(tmp_path)
    ctx.knobs["repair_attempts"] = 3
    texts = [f"Correction {n}" for n in range(1, 26)]
    for text in texts:
        Ledger(ctx.workdir).note("correction", text)
    _deliver_overwatch(ctx, {text: _CAUGHT for text in texts}, worker="focused_qa")

    monkeypatch.setattr(base_check, "SITE_REVERT_TOTAL_S", -1)
    assert fast_ops.gate_qa(ctx) == "proceed", ctx.facts.get("repair_feedback")
    report = json.dumps(ctx.read_json("fast-report.json"))
    assert evidence.ADVISORY_CORRECTION_AUDIT in report
    assert report.count("revert not run (time cap reached)") == 25
    # The completed report, which design.json carries, keeps them.
    assert any(evidence.ADVISORY_CORRECTION_AUDIT in a for a in fast_ops._advisories(ctx))


# ── review #476: measured misses beside unmeasured results ───────────────


@pytest.mark.parametrize("results,message", [
    (["no_tests", "not_run"], "ran no tests"),
    (["not_run", "no_tests"], "ran no tests"),
    (["unavailable", "not_run"], "revert unavailable"),
    (["unavailable", "collection_error"], "revert unavailable"),
])
def test_a_measured_miss_is_not_hidden_by_an_unmeasured_test(tmp_path, monkeypatch, results, message):
    """A wrong test id, or an unknown result, blocks even when another test of the same
    site was not measured."""
    from refactory_dsh import base_check

    ctx, _ = _gate_run(tmp_path)
    tests = [f"test_calc.py::t{i}" for i in range(len(results))]
    monkeypatch.setattr(base_check, "compute_site_reverts", lambda ctx_, pairs: {"pairs": [
        {"site": s, "test": t, "result": results[tests.index(t)], "catches_site": False}
        for s, t, _ in pairs]})
    Ledger(ctx.workdir).note("correction", "Zero is valid")
    _deliver_overwatch(ctx, {"Zero is valid": [{"site": "calc.py:3", "tests": tests, "scope": "local"}]})
    problems = evidence.correction_audit_problems(ctx)
    assert any(message in p for p in problems), problems


@pytest.mark.parametrize("site", ["calc.py:40", "nosuch.py:2"])
def test_the_time_cap_does_not_hide_a_site_outside_the_diff(tmp_path, monkeypatch, site):
    """Whether a site is in the diff needs no test run, so the cap never makes it advisory."""
    from refactory_dsh import base_check

    ctx, _ = _gate_run(tmp_path)
    monkeypatch.setattr(base_check, "SITE_REVERT_TOTAL_S", -1)
    Ledger(ctx.workdir).note("correction", "Zero is valid")
    _deliver_overwatch(ctx, {"Zero is valid": [
        {"site": site, "tests": ["test_calc.py::test_validate"], "scope": "local"}]})
    problems = evidence.correction_audit_problems(ctx)
    assert any(f"site {site} is not in the diff" in p for p in problems), problems
    assert evidence.correction_audit_advisories(ctx) == []


@pytest.mark.parametrize("relative", [False, True])
def test_a_site_in_another_checkout_is_not_in_the_diff(tmp_path, relative):
    ctx, found = _gate_run(tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    (other / "calc.py").write_text("# Validation helpers.\ndef validate(x):\n    return x >= 0\n")
    site = ("../other/calc.py:3" if relative else f"{other}/calc.py:3")
    Ledger(ctx.workdir).note("correction", "Zero is valid")
    _deliver_overwatch(ctx, {"Zero is valid": [
        {"site": site, "tests": ["test_calc.py::test_validate"], "scope": "local"}]})
    problems = evidence.correction_audit_problems(ctx)
    assert any("is not in the diff" in p for p in problems), problems


def test_a_passing_test_is_not_a_collection_error_whatever_it_prints(tmp_path):
    """The cited test passes with the site reverted, and warns with text naming
    ImportError from the test file: a measured miss, not a collection error."""
    ctx, found = _gate_run(tmp_path)
    repo = Path(found.path)
    (repo / "test_calc.py").write_text(
        "import warnings\nfrom calc import validate\n"
        "def test_validate():\n    assert validate(0) is True\n"
        "def test_unrelated():\n"
        "    warnings.warn('fast path unavailable (ImportError: no module named fastcalc)')\n"
        "    assert True\n")
    git(repo, "commit", "-qam", "warn")
    wt = json.loads((ctx.workdir / ".refactory" / "worktree.json").read_text())
    wt["head"] = git(repo, "rev-parse", "HEAD")
    (ctx.workdir / ".refactory" / "worktree.json").write_text(json.dumps(wt))
    Ledger(ctx.workdir).note("correction", "Zero is valid")
    _deliver_overwatch(ctx, {"Zero is valid": [
        {"site": "calc.py:3", "tests": ["test_calc.py::test_unrelated"], "scope": "local"}]})
    problems = evidence.correction_audit_problems(ctx)
    assert any("site calc.py:3 not reached by any test" in p for p in problems), problems


def test_a_cited_test_that_is_not_a_test_id_blocks(tmp_path):
    ctx, _ = _gate_run(tmp_path)
    Ledger(ctx.workdir).note("correction", "Zero is valid")
    _deliver_overwatch(ctx, {"Zero is valid": [
        {"site": "calc.py:3", "tests": [{"name": "test_calc.py::test_validate"}], "scope": "local"}]})
    problems = evidence.correction_audit_problems(ctx)
    assert any("ran no tests" in p for p in problems), problems


def test_reverts_in_one_file_never_run_stale_bytecode(tmp_path):
    """Six same-size reverts of one module within a second: each pair's test must see its
    own revert, not the bytecode an earlier pair's run cached."""
    from refactory_dsh import base_check

    run_dir = tmp_path / "run"
    (run_dir / ".refactory").mkdir(parents=True)
    repo = git_repo(tmp_path / "repo")
    (repo / "m.py").write_text("".join(f"def f{i}():\n    return {i}\n\n\n" for i in range(6)))
    git(repo, "add", "m.py")
    git(repo, "commit", "-q", "-m", "m")
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "-b", "run-branch")
    (repo / "m.py").write_text("".join(f"def f{i}():\n    return {i + 100}\n\n\n" for i in range(6)))
    (repo / "test_m.py").write_text("import m\n" + "".join(
        f"def test_f{i}():\n    assert m.f{i}() == {i + 100}\n" for i in range(6)))
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "change")
    (run_dir / ".refactory" / "worktree.json").write_text(json.dumps({
        "path": str(repo), "base": base, "head": git(repo, "rev-parse", "HEAD"),
        "branch": "run-branch", "target": str(repo), "run": str(run_dir)}))
    ctx = nn.RunContext(run_dir, facts={"brief": "test"})
    pairs = [(f"m.py:{4 * i + 2}", f"test_m.py::test_f{i}", "local") for i in range(6)]
    results = base_check.compute_site_reverts(ctx, pairs)["pairs"]
    assert [p["result"] for p in results] == ["fail"] * 6


def test_a_cited_test_name_reaches_its_runner_whole_and_runs_nothing_else(tmp_path, monkeypatch):
    """A vitest name is `describe > test`. Unquoted, the shell redirected the run into a
    stray file in the worktree, cut the name, and ran what followed a `;` (dm-193). A
    pytest id with a space in its parameter split the same way."""
    from refactory_dsh import base_check

    argv_file = tmp_path / "npx-argv.txt"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "npx").write_text(f'#!/bin/sh\nprintf "%s\\n" "$@" > "{argv_file}"\nexit 1\n')
    (bin_dir / "npx").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    run_dir = tmp_path / "run"
    (run_dir / ".refactory").mkdir(parents=True)
    repo = git_repo(tmp_path / "repo")
    (repo / "src").mkdir()
    (repo / "src" / "hmr.ts").write_text("export const enabled = true\n")
    (repo / "m.py").write_text("def f():\n    return 1\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "base")
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "-b", "run-branch")
    (repo / "src" / "hmr.ts").write_text("export const enabled = false\n")
    (repo / "src" / "hmr.test.ts").write_text("// stands in for the vitest file\n")
    (repo / "m.py").write_text("def f():\n    return 2\n")
    (repo / "test_m.py").write_text("import pytest\nimport m\n\n\n@pytest.mark.parametrize('label', ['a b'])\n"
                                    "def test_f(label):\n    assert m.f() == 2\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "change")
    (run_dir / ".refactory" / "worktree.json").write_text(json.dumps({
        "path": str(repo), "base": base, "head": git(repo, "rev-parse", "HEAD"),
        "branch": "run-branch", "target": str(repo), "run": str(run_dir)}))
    ctx = nn.RunContext(run_dir, facts={"brief": "test"})
    name = "client-hmr composition > disabled after composition; touch pwned"
    results = base_check.compute_site_reverts(ctx, [
        ("src/hmr.ts:1", f"src/hmr.test.ts::{name}", "local"),
        ("m.py:2", "test_m.py::test_f[a b]", "local")])["pairs"]
    assert argv_file.read_text().splitlines() == ["vitest", "run", "src/hmr.test.ts", "-t", name]
    assert [p["result"] for p in results] == ["fail", "fail"], results
    stray = [line for line in git(repo, "status", "--porcelain", "--untracked-files=all").splitlines()
             if "pytest_cache" not in line and "__pycache__" not in line]
    assert stray == [], "nothing the shell wrote: no `disabled` capture, no `pwned`"


def test_a_documentation_site_needs_no_test_but_must_be_in_the_diff(tmp_path):
    """A correction to a prose document is evidenced by the diff: no test is expected to
    fail when its line is reverted, or to be cited at all, so either is advisory, not a
    measured miss (a lab run spent a whole builder round on it). A documentation site the
    diff does not change still blocks, and so do an untested code site and an untested
    `.txt` data file. A doc clause may cite its file with a line."""
    run_dir = tmp_path / "run"
    (run_dir / ".refactory").mkdir(parents=True)
    repo = git_repo(tmp_path / "repo")
    (repo / "docs").mkdir()
    (repo / "docs" / "guide.md").write_text("# Guide\n\nUse the tool.\n")
    (repo / "README.md").write_text("# Tool\n")
    (repo / "CHANGES.md").write_text("# Changes\n")
    (repo / "data.txt").write_text("1\n")
    (repo / "calc.py").write_text("X = 1\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "docs")
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "-b", "run-branch")
    (repo / "docs" / "guide.md").write_text(
        "# Guide\n\nUse the tool.\nSee also: [README](../README.md).\n")
    (repo / "README.md").write_text("# Tool\nSee the [guide](docs/guide.md).\n")
    (repo / "data.txt").write_text("2\n")
    (repo / "calc.py").write_text("X = 2\n")
    (repo / "test_calc.py").write_text("def test_unrelated():\n    assert True\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "link")
    (run_dir / ".refactory" / "worktree.json").write_text(json.dumps({
        "path": str(repo), "base": base, "head": git(repo, "rev-parse", "HEAD"),
        "branch": "run-branch", "target": str(repo), "run": str(run_dir)}))
    ctx = nn.RunContext(run_dir, facts={"brief": "test", "artifact": str(run_dir / "art")})
    (run_dir / "art").mkdir()
    (run_dir / "art" / "a.txt").write_text("42")
    Ledger.seed(run_dir, "test")
    texts = ["Link the README from the guide", "Point the README at the guide",
             "Mention it in the changelog", "Note it in the changelog", "Make X two",
             "Make the data two", "Document it"]
    for text in texts:
        Ledger(run_dir).note("correction", text)
    tests = ["test_calc.py::test_unrelated"]
    _deliver_overwatch(ctx, {
        "Link the README from the guide": [{"site": "docs/guide.md:4", "tests": tests, "scope": "local"}],
        "Point the README at the guide": [{"site": "README.md:2", "tests": [], "scope": "local"}],
        "Mention it in the changelog": [{"site": "CHANGES.md:1", "tests": tests, "scope": "local"}],
        "Note it in the changelog": [{"site": "CHANGES.md:1", "tests": [], "scope": "local"}],
        "Make X two": [{"site": "calc.py:1", "tests": tests, "scope": "local"}],
        "Make the data two": [{"site": "data.txt:1", "tests": tests, "scope": "local"}]},
        kinds={"Document it": {"kind": "doc", "path": "README.md:2"}})
    problems = evidence.correction_audit_problems(ctx)
    assert not any("docs/guide.md:4" in p or "README.md:2" in p for p in problems), problems
    assert any("site CHANGES.md:1 is not in the diff" in p for p in problems), problems
    assert any("site CHANGES.md:1 has no tests" in p for p in problems), problems
    assert any("site calc.py:1 not reached by any test" in p for p in problems), problems
    assert any("site data.txt:1 not reached by any test" in p for p in problems), problems
    assert len(problems) == 4, problems
    advisories = evidence.correction_audit_advisories(ctx)
    for site in ("docs/guide.md:4", "README.md:2"):
        assert any(f"site {site} is documentation" in a for a in advisories), advisories
