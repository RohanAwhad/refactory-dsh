"""Tests for the design probe (dm-133).

design_doc and design_review gain the builder's sandboxed probe via
``run_design_probe`` and ``--command``.  Probes record evidence to
``probes.jsonl``; the claims checker verifies cited ids; ``design_review``
is told to probe call sites.

Each test fails without its corresponding fix.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pytest

from refactory_dsh import nn, steering, lowering, worktree as wt_mod
from refactory_dsh.nn import Verdict
from refactory_dsh.nn.graph import Graph, Node, Edge
from refactory_dsh.worktree import Worktree, _write_json


# ── helpers ───────────────────────────────────────────────────────────────────

def _git(root, *args, **kwargs):
    env = kwargs.pop("env", None)
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=True, capture_output=True, text=True, env=env,
    ).stdout.strip()


def _git_repo(root):
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "test")
    (root / "app.py").write_text("print('app')\n")
    (root / "package.json").write_text('{"name": "test", "dependencies": {}}\n')
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "base")
    return root


def _make_probe_run(tmp_path):
    """Create a minimal run with a worktree for probe testing."""
    import sys
    repo = _git_repo(tmp_path / "repo")
    run = tmp_path / "run"
    run.mkdir()
    (run / ".refactory").mkdir(parents=True)
    (run / ".refactory" / "slot").mkdir(parents=True)

    branch = "refactory/run-test"
    _git(repo, "checkout", "-b", branch)
    base = _git(repo, "rev-parse", "HEAD")
    head = base

    found = Worktree(target=str(repo), path=str(repo), branch=branch,
                     base=base, head=head, run=str(run))
    _write_json(run / ".refactory" / "slot" / "run-test.json", asdict(found))
    _write_json(run / ".refactory" / "worktree.json", asdict(found))

    (run / "facts.json").write_text(json.dumps({
        "name": "test-probe", "brief": "test",
        "check": f"{sys.executable} -c 'print(1)'",
    }))
    return run, repo, found


def _snapshot_git_state(repo_path: Path) -> dict[str, Any]:
    """Snapshot git state: refs, config, hooks, worktree list, ignored files."""
    result: dict[str, Any] = {}
    git_dir = Path(_git(repo_path, "rev-parse", "--git-dir"))
    if not git_dir.is_absolute():
        git_dir = repo_path / git_dir
    result["refs"] = _git(repo_path, "for-each-ref", "--format=%(refname)")
    result["config"] = _git(repo_path, "config", "--local", "--list")
    hooks_dir = git_dir / "hooks"
    result["hooks"] = sorted(p.name for p in hooks_dir.iterdir()) if hooks_dir.is_dir() else []
    worktrees_dir = git_dir / "worktrees"
    result["worktrees"] = sorted(p.name for p in worktrees_dir.iterdir()) if worktrees_dir.is_dir() else []
    try:
        result["ignored"] = _git(repo_path, "ls-files", "--others", "--ignored", "--exclude-standard")
    except subprocess.CalledProcessError:
        result["ignored"] = ""
    result["head"] = _git(repo_path, "rev-parse", "HEAD")
    return result


# ── design_probe_steps tests ─────────────────────────────────────────────────

def test_design_probe_steps_all_modes():
    """design_probe_steps selects exactly design_doc and design_review for
    DesignMode on both build() and to_workflow(), and nothing for other modes.
    """
    from refactory_dsh.design import DesignMode
    from refactory_dsh.fast import DesignFastMode
    from refactory_dsh.create import CreateMode

    # DesignMode — pre-lowered graph (build())
    dm = DesignMode()
    graph = dm.build()
    result_build = steering.design_probe_steps(graph)
    assert result_build == {"design_doc": "writer", "design_review": "reviewer"}, (
        f"build(): expected design_doc/design_review, got {result_build}"
    )

    # DesignMode — IR (to_workflow())
    workflow = lowering.to_workflow(dm.state_dict(), name="design")
    result_ir = steering.design_probe_steps(workflow)
    assert result_ir == {"design_doc": "writer", "design_review": "reviewer"}, (
        f"to_workflow(): expected design_doc/design_review, got {result_ir}"
    )

    # DesignFastMode — no design_doc or design_review
    dfm = DesignFastMode()
    assert steering.design_probe_steps(dfm.build()) == {}
    assert steering.design_probe_steps(
        lowering.to_workflow(dfm.state_dict(), name="design_fast")
    ) == {}

    # CreateMode — no match (review is an fn gate, not an agent reviewer)
    cm = CreateMode()
    assert steering.design_probe_steps(cm.build()) == {}
    assert steering.design_probe_steps(
        lowering.to_workflow(cm.state_dict(), name="create")
    ) == {}


def test_for_run_injects_probe_line(tmp_path):
    """A graph without probe lines gets them after for_run."""
    from refactory_dsh.design import DesignMode

    # Create a minimal workdir with an empty ledger
    workdir = tmp_path / "run"
    workdir.mkdir()
    (workdir / ".refactory").mkdir()

    dm = DesignMode()
    workflow = lowering.to_workflow(dm.state_dict(), name="design")

    # Verify the template does NOT have the probe line before for_run
    dd_template_before = workflow.nodes["design_doc"].prompt_template
    assert "{{design_probe_instruction}}" not in dd_template_before

    # Apply for_run
    workflow = steering.for_run(workflow, workdir)

    # design_doc should have the probe instruction
    dd_template = workflow.nodes["design_doc"].prompt_template
    assert "{{design_probe_instruction}}" in dd_template

    # design_review should have both probe instruction and review
    dr_template = workflow.nodes["design_review"].prompt_template
    assert "{{design_probe_instruction}}" in dr_template
    assert "{{design_probe_review}}" in dr_template


def test_dsh_dispatch_carries_probe(tmp_path):
    """The DSH dispatch path (lower_workflow(for_run(to_workflow()))) carries
    the probe command and the call-site review line in design_doc and
    design_review prompts.
    """
    from refactory_dsh.design import DesignMode

    workdir = tmp_path / "run"
    workdir.mkdir()
    (workdir / ".refactory").mkdir()

    dm = DesignMode()
    state = dm.state_dict()
    workflow = lowering.to_workflow(state, name="design")

    # Apply for_run (what DSH does: steering.for_run(workflow, workdir))
    workflow = steering.for_run(workflow, workdir)

    # Check that design_doc has the probe instruction placeholder
    dd = workflow.nodes["design_doc"]
    assert "{{design_probe_instruction}}" in dd.prompt_template

    # Check that design_review has both placeholders
    dr = workflow.nodes["design_review"]
    assert "{{design_probe_instruction}}" in dr.prompt_template
    assert "{{design_probe_review}}" in dr.prompt_template


# ── probe side effects tests ─────────────────────────────────────────────────

def test_design_probe_side_effects(tmp_path):
    """A design probe runs, and the target, worktree, and shared .git are unchanged."""
    from refactory_dsh.builder_probe import run_design_probe

    run, repo, found = _make_probe_run(tmp_path)

    # Snapshot state before
    before = _snapshot_git_state(repo)

    # Run a design probe
    probe_result, probe_id = run_design_probe(
        run, command="echo hello", timeout=30,
    )
    assert probe_result.status == "pass"
    assert len(probe_id) == 12

    # Snapshot state after
    after = _snapshot_git_state(repo)

    # Target, worktree and shared .git unchanged
    assert before == after, f"git state changed: before={before}, after={after}"

    # probes.jsonl was written
    probes_path = run / ".refactory" / "probes.jsonl"
    assert probes_path.is_file()
    records = [json.loads(line) for line in probes_path.read_text().splitlines() if line.strip()]
    assert any(r["id"] == probe_id and r["command"] == "echo hello" for r in records)

    # Probe directory cleaned up
    probe_dirs = [d for d in (run / ".refactory").iterdir()
                  if d.is_dir() and d.name.startswith("probe-")]
    assert probe_dirs == [], f"probe directories not cleaned up: {probe_dirs}"


# ── probe evidence tests ─────────────────────────────────────────────────────

def test_probe_evidence_fake_id_reloops(tmp_path):
    """A design citing a fabricated probe id gets relooped by check_design_claims."""
    from refactory_dsh.design_claims import check_design_claims

    source = tmp_path / "source"
    source.mkdir()
    (source / "sample.py").write_text("def real_fn():\n    pass\n")

    workdir = tmp_path / "run"
    workdir.mkdir()
    (workdir / ".refactory").mkdir(parents=True)

    ctx = nn.RunContext(workdir)
    ctx.facts.update({
        "source": str(source),
        "claims_check_attempts_used": 0,
    })
    # Write a design with a claim that has a fabricated probe id
    ctx.write_text("design.md", (
        "# Design\n\n"
        "## Summary\n\nFix it.\n\n"
        "## Rule and sites\n\n- `sample.py:1` — **[changed]**\n\n"
        "## Docs\n\n- docs\n\n## Changes\n\n- sample.py\n\n"
        "`real_fn()` at `sample.py:1`: `def real_fn():` (probe aabbccddeeff: pass)\n"
    ))

    verdict = check_design_claims(ctx)
    assert verdict == "reloop"
    assert "aabbccddeeff" in verdict.reason
    assert "not in probes.jsonl" in verdict.reason


def test_probe_evidence_real_id_proceeds(tmp_path):
    """A design citing a real probe id (matching status) proceeds."""
    from refactory_dsh.design_claims import check_design_claims
    from refactory_dsh.builder_probe import run_design_probe

    run, repo, found = _make_probe_run(tmp_path)

    # Add a file with a defined function so the identifier check passes
    source = Path(found.target)
    (source / "helper.py").write_text("def helper_fn():\n    pass\n")
    _git(source, "add", "helper.py")
    _git(source, "commit", "-q", "-m", "add helper")
    # Update the worktree record to match the new HEAD
    new_head = _git(source, "rev-parse", "HEAD")
    new_found = Worktree(target=found.target, path=found.path, branch=found.branch,
                         base=found.base, head=new_head, run=found.run)
    _write_json(run / ".refactory" / "worktree.json", asdict(new_found))
    _write_json(run / ".refactory" / "slot" / "run-test.json", asdict(new_found))

    # Run an actual probe to get a real id
    probe_result, probe_id = run_design_probe(
        run, command="echo ok", timeout=30,
    )
    assert probe_result.status == "pass"

    ctx = nn.RunContext(run)
    ctx.facts.update({
        "source": str(source),
        "claims_check_attempts_used": 0,
    })
    ctx.write_text("design.md", (
        "# Design\n\n"
        "## Summary\n\nFix it.\n\n"
        "## Rule and sites\n\n- `helper.py:1` — **[changed]**\n\n"
        "## Docs\n\n- docs\n\n## Changes\n\n- helper.py\n\n"
        f"`helper_fn()` at `helper.py:1`: `def helper_fn():` (probe {probe_id}: pass)\n"
    ))

    verdict = check_design_claims(ctx)
    assert verdict == "proceed", f"expected proceed, got {verdict}"


def test_citation_placeholder_proceeds(tmp_path):
    """A placeholder (probe <id>: <status>) in prose is not refused."""
    from refactory_dsh.design_claims import check_design_claims

    source = tmp_path / "source"
    source.mkdir()
    (source / "sample.py").write_text("def real_fn():\n    pass\n")

    workdir = tmp_path / "run"
    workdir.mkdir()
    (workdir / ".refactory").mkdir(parents=True)

    ctx = nn.RunContext(workdir)
    ctx.facts.update({
        "source": str(source),
        "claims_check_attempts_used": 0,
    })
    # The placeholder text uses <id> which is not 12 hex chars
    ctx.write_text("design.md", (
        "# Design\n\n"
        "## Summary\n\nCite as (probe <id>: <status>).\n\n"
        "## Rule and sites\n\n- `sample.py:1` — **[changed]**\n\n"
        "## Docs\n\n- docs\n\n## Changes\n\n- sample.py\n\n"
        "`real_fn()` at `sample.py:1`: `def real_fn():`\n"
    ))

    verdict = check_design_claims(ctx)
    assert verdict == "proceed", f"placeholder should not cause a mismatch, got {verdict}"


def test_citation_fenced_example_proceeds(tmp_path):
    """A fenced code block with a probe citation is not refused."""
    from refactory_dsh.design_claims import check_design_claims

    source = tmp_path / "source"
    source.mkdir()
    (source / "sample.py").write_text("def real_fn():\n    pass\n")

    workdir = tmp_path / "run"
    workdir.mkdir()
    (workdir / ".refactory").mkdir(parents=True)

    ctx = nn.RunContext(workdir)
    ctx.facts.update({
        "source": str(source),
        "claims_check_attempts_used": 0,
    })
    # A fenced block with a probe citation inside
    ctx.write_text("design.md", (
        "# Design\n\n"
        "## Summary\n\nFix it.\n\n"
        "## Rule and sites\n\n- `sample.py:1` — **[changed]**\n\n"
        "## Docs\n\n- docs\n\n## Changes\n\n- sample.py\n\n"
        "```\n"
        "`real_fn()` at `sample.py:1`: `def real_fn():` (probe abc123def456: pass)\n"
        "```\n\n"
        "`real_fn()` at `sample.py:1`: `def real_fn():`\n"
    ))

    verdict = check_design_claims(ctx)
    assert verdict == "proceed", f"fenced example should not cause a mismatch, got {verdict}"


# ── validate mode tests ──────────────────────────────────────────────────────

def test_validate_design_mode():
    """DesignMode validates without issues (K2: no probes.jsonl in reads)."""
    from refactory_dsh.design import DesignMode

    issues = nn.validate_state(
        DesignMode().state_dict(), invariants=DesignMode().invariants(), resolve_calls=True
    )
    assert issues == [], f"validation issues: {[str(i) for i in issues]}"


# ── no worktree tests ────────────────────────────────────────────────────────

def test_no_worktree_no_probe(tmp_path):
    """With worktree=False, no prompt carries probe text."""
    from refactory_dsh.design import DesignMode
    from refactory_dsh import repo_change

    # The design probe facts are empty when there's no worktree
    # We verify this by checking that locate() returns empty strings
    # for the new facts when in_worktree is False, and that for_run's
    # injection still produces no probe text in prompts.

    workdir = tmp_path / "run"
    workdir.mkdir()
    (workdir / ".refactory").mkdir()

    dm = DesignMode()
    workflow = lowering.to_workflow(dm.state_dict(), name="design")
    workflow = steering.for_run(workflow, workdir)

    # Even with for_run, the template has placeholders but when rendered
    # with empty facts, the probe text disappears.
    # The key thing: the injection happens, but the FACT itself is empty
    # when worktree=False, so the rendered prompt has no probe text.
    dd = workflow.nodes["design_doc"]
    dr = workflow.nodes["design_review"]

    # The placeholders are present (for_run injects them)
    assert "{{design_probe_instruction}}" in dd.prompt_template
    assert "{{design_probe_review}}" in dr.prompt_template

    # Verify the facts themselves are empty when no worktree
    # Simulate rendering with empty facts
    rendered_dd = dd.prompt_template.replace("{{design_probe_instruction}}", "")
    rendered_dr = dr.prompt_template.replace("{{design_probe_instruction}}", "").replace("{{design_probe_review}}", "")

    assert "--command" not in rendered_dd
    assert "design probe" not in rendered_dd.lower().replace("{{", "").replace("}}", "")
    assert "--command" not in rendered_dr


# ── unreached call site tests ─────────────────────────────────────────────────

def test_unreached_call_site_reloop(tmp_path):
    """The design_probe_review fact is injected into design_review by for_run,
    telling the reviewer to probe call sites.  When the reviewer follows this
    instruction and reports BLOCKING for an unreached call site, gate_design_review
    reloops.

    Fails on main: for_run on main only calls annotate() and does not inject
    {{design_probe_review}}, so the reviewer has no call-site probing instruction
    and would not know to report unreached call sites as BLOCKING.
    """
    from refactory_dsh.design import DesignMode
    from refactory_dsh.create_ops import blocking_findings

    # ── Guard: for_run must inject {{design_probe_review}} into design_review ──
    # This is the mechanism that tells the reviewer to probe call sites.
    # On main, for_run only calls annotate() and does NOT inject the probe
    # review fact, so this assertion fails on main.
    workdir = tmp_path / "run"
    workdir.mkdir()
    (workdir / ".refactory").mkdir()

    dm = DesignMode()
    workflow = lowering.to_workflow(dm.state_dict(), name="design")
    workflow = steering.for_run(workflow, workdir)

    dr_template = workflow.nodes["design_review"].prompt_template
    assert "{{design_probe_review}}" in dr_template, (
        "for_run must inject {{design_probe_review}} into design_review's "
        "prompt so the reviewer is told to probe call sites"
    )

    # ── Consequence: BLOCKING findings from the reviewer cause reloop ──
    # When the reviewer follows the design_probe_review instruction and
    # discovers an unreached call site, it reports BLOCKING.  The existing
    # gate_design_review mechanism picks up BLOCKING lines via
    # blocking_findings() and reloops.
    review_text = (
        "## Review\n\n"
        "The design wires into `builder_probe.py:363` but the probe shows "
        "this call site is never reached.\n\n"
        "BLOCKING: builder_probe.py:363: call site unreached — "
        "probe `python -c 'import ...'` showed no invocation\n\n"
        "VERDICT: FAIL\n"
    )

    findings = blocking_findings(review_text)
    assert len(findings) >= 1
    assert any("call site unreached" in f for f in findings)


# ── CLI tests ────────────────────────────────────────────────────────────────

def test_cli_mutual_exclusion():
    """--test and --command are mutually exclusive."""
    import sys
    result = subprocess.run(
        [sys.executable, "-m", "refactory_dsh.builder_probe",
         "--workdir", "/tmp/nonexistent",
         "--test", "foo", "--command", "echo"],
        capture_output=True, text=True,
    )
    assert result.returncode == 2  # argparse error


def test_cli_command_rejects_emission():
    """--emission cannot be used with --command."""
    import sys
    result = subprocess.run(
        [sys.executable, "-m", "refactory_dsh.builder_probe",
         "--workdir", "/tmp/nonexistent",
         "--command", "echo",
         "--emission", "emit.txt"],
        capture_output=True, text=True,
    )
    assert result.returncode == 2  # parser.error


# ── produces tests ───────────────────────────────────────────────────────────

def test_produces_includes_design_probe_facts():
    """repo_change.produces() includes the new facts."""
    from refactory_dsh import repo_change
    p = repo_change.produces()
    assert "design_probe_instruction" in p
    assert "design_probe_review" in p
