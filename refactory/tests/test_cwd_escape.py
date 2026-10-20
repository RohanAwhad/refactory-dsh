"""cwd-escape detection: the EXIT-trap epilogue (#351 I3).

The check's shell command may ``cd`` out of the run's worktree; when it does, the
sentinel file records the final directory and ``SandboxResult.cwd_outside`` is set.
The sentinel never reaches stdout or stderr.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from refactory_dsh import sandbox


def test_cd_into_a_worktree_subdir_is_not_flagged(tmp_path):
    """``cd refactory && pytest`` stays inside the worktree."""
    workdir = tmp_path / "wt"
    workdir.mkdir()
    sub = workdir / "sub"
    sub.mkdir()
    result = sandbox.run_command(f"cd {sub} && true", workdir=workdir, worktree_root=workdir)
    assert result.ok
    assert result.cwd_outside is None
    assert "__refactory" not in result.stdout
    assert "__refactory" not in result.stderr


def test_cd_to_tmp_is_flagged(tmp_path):
    """``cd /tmp && true # note`` leaves the worktree and is flagged."""
    workdir = tmp_path / "wt"
    workdir.mkdir()
    result = sandbox.run_command("cd /tmp && true # note", workdir=workdir, worktree_root=workdir)
    assert result.ok
    assert result.cwd_outside is not None
    assert "/tmp" in result.cwd_outside
    # Sentinel must not leak into stdout or stderr.
    assert "__refactory" not in result.stdout
    assert "__refactory" not in result.stderr


def test_exit_preserves_status(tmp_path):
    """``exit 3`` keeps status 3; the trap runs after exit."""
    workdir = tmp_path / "wt"
    workdir.mkdir()
    result = sandbox.run_command("exit 3", workdir=workdir, worktree_root=workdir)
    assert not result.ok
    assert result.returncode == 3
    # No false escape: the trap fires, but PWD is still in the worktree.
    assert result.cwd_outside is None


def test_sentinel_never_reaches_output(tmp_path):
    """Nothing sentinel-related appears in stdout or stderr."""
    workdir = tmp_path / "wt"
    workdir.mkdir()
    result = sandbox.run_command("echo hello >&2; echo world", workdir=workdir, worktree_root=workdir)
    assert result.ok
    assert result.stdout.strip() == "world"
    assert result.stderr.strip() == "hello"
    assert "__refactory" not in result.stdout
    assert "__refactory" not in result.stderr
    assert result.cwd_outside is None


def test_no_worktree_root_means_no_detection(tmp_path):
    """Without worktree_root, cwd_outside is always None."""
    workdir = tmp_path / "wt"
    workdir.mkdir()
    result = sandbox.run_command("cd /tmp && true", workdir=workdir)
    assert result.ok
    assert result.cwd_outside is None


def test_prefix_boundary_not_fooled(tmp_path):
    """/a/wt2 is not treated as inside /a/wt (trailing / prevents prefix fooling)."""
    wt = tmp_path / "wt"
    wt.mkdir()
    wt2 = tmp_path / "wt2"
    wt2.mkdir()
    result = sandbox.run_command(f"cd {wt2} && true", workdir=wt, worktree_root=wt)
    assert result.ok
    assert result.cwd_outside is not None
    assert str(wt2) in result.cwd_outside


def test_check_record_carries_cwd_escaped(tmp_path, monkeypatch):
    """Drives the real ``check_artifact`` and ``focused_check`` in a real worktree.

    A command that ``cd``s to ``/tmp`` must produce ``cwd_escaped`` in the
    recorded runtime evidence (Part A — ``design_ops.check_artifact``) and in
    ``focused-check.json`` (Part B — ``fast_ops.focused_check``).

    This test fails if ``apply_cwd_escaped`` is removed from ``check_artifact``
    or ``focused_check``, or if ``repo_change`` stops passing ``worktree_root``
    to ``sandbox.run_command``.
    """
    import json as _json
    from refactory_dsh import design_ops, fast_ops, worktree
    from tests_support import git_repo

    repo = git_repo(tmp_path / "repo")

    class Ctx:
        """Minimal context for check_artifact / focused_check."""
        def __init__(self, workdir: Path):
            self.workdir = Path(workdir)
            self.facts: dict = {}
            self.budget = None
            self.model = None
            self._knobs: dict = {}

        def knob(self, name, default=None):
            return self._knobs.get(name, default)

        def set_fact(self, name, value):
            self.facts[name] = value

        def write_text(self, name, content):
            p = self.workdir / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(str(content))

        def write_json(self, name, payload):
            p = self.workdir / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(_json.dumps(payload, indent=2))

        def read_json(self, name, default=None):
            p = self.workdir / name
            if p.exists():
                return _json.loads(p.read_text())
            return default

        def read_text(self, name):
            p = self.workdir / name
            if p.exists():
                return p.read_text()
            return ""

        def exists(self, name):
            return (self.workdir / name).exists()

        @property
        def iteration(self):
            return 1

    # Mock evidence.record to capture outcome dicts without its complex
    # dependencies (identity, receipts, etc.) and base_check to skip the
    # test-guardedness computation that needs a second worktree checkout.
    captured: list[dict] = []
    monkeypatch.setattr(
        "refactory_dsh.authoring_evidence.record",
        lambda ctx, outcome: (captured.append(dict(outcome)), outcome)[-1],
    )
    monkeypatch.setattr(
        "refactory_dsh.base_check.compute_test_guardedness",
        lambda *a, **kw: None,
    )

    # ── Part A: design_ops.check_artifact ─────────────────────────────
    run_a = tmp_path / "run_a"
    run_a.mkdir()
    found_a = worktree.create(repo, "HEAD", "cwdesca001", run_a)

    ctx_a = Ctx(run_a)
    ctx_a.facts.update({
        "check": "cd /tmp && true",
        "commit": found_a.head,
        "artifact": found_a.path,
        "name": "test_cwd_check",
    })

    verdict = design_ops.check_artifact(ctx_a)
    assert verdict == "proceed"
    assert captured, "evidence.record was not called by check_artifact"
    assert "cwd_escaped" in captured[-1], \
        f"cwd_escaped missing from recorded evidence: {sorted(captured[-1])}"
    assert "/tmp" in captured[-1]["cwd_escaped"]

    # ── Part B: fast_ops.focused_check ────────────────────────────────
    run_b = tmp_path / "run_b"
    run_b.mkdir()
    found_b = worktree.create(repo, "HEAD", "cwdescb002", run_b)

    ctx_b = Ctx(run_b)
    ctx_b.facts.update({
        "check": "cd /tmp && true",
        "commit": found_b.head,
        "artifact": found_b.path,
        "name": "test_cwd_check",
    })

    fast_ops.focused_check(ctx_b)
    payload = ctx_b.read_json("focused-check.json")
    assert payload is not None, "focused-check.json was not written"
    assert "cwd_escaped" in payload, \
        f"cwd_escaped missing from focused-check.json: {sorted(payload)}"
    assert "/tmp" in payload["cwd_escaped"]
