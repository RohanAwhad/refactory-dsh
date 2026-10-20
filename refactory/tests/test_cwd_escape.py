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


def test_check_record_carries_cwd_escaped(tmp_path):
    """The check outcome carries cwd_escaped when the command leaves the worktree."""
    workdir = tmp_path / "wt"
    workdir.mkdir()
    result = sandbox.run_command("cd /tmp && echo ok", workdir=workdir, worktree_root=workdir)
    assert result.ok
    assert result.cwd_outside is not None
    # Simulate what design_ops / fast_ops do: put cwd_escaped in the outcome.
    outcome = {"status": "pass", "stdout": result.stdout, "stderr": result.stderr}
    if result.cwd_outside:
        outcome["cwd_escaped"] = result.cwd_outside
    assert "cwd_escaped" in outcome
    assert "/tmp" in outcome["cwd_escaped"]
