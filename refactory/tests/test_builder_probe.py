"""Tests for builder_probe: sandboxed probe with isolation."""

from __future__ import annotations

import json
import os
import signal
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from refactory_dsh.builder_probe import (
    ProbeResult,
    _pid_alive,
    _strip_worktree_path,
    sweep_dead_probes,
    _emission_matches_last,
)


def test_strip_worktree_path():
    """The run worktree's .venv/bin and node_modules/.bin are stripped from PATH."""
    wt = "/home/user/.refactory/worktrees/run-abc/run-123"
    env = {"PATH": f"{wt}/.venv/bin:{wt}/node_modules/.bin:/usr/bin:/usr/local/bin"}
    result = _strip_worktree_path(env, wt)
    assert f"{wt}/.venv/bin" not in result["PATH"]
    assert f"{wt}/node_modules/.bin" not in result["PATH"]
    assert "/usr/bin" in result["PATH"]


def test_pid_alive_current_process():
    """The current process is alive."""
    assert _pid_alive(os.getpid()) is True


def test_pid_alive_dead_process():
    """A non-existent PID is not alive."""
    assert _pid_alive(99999999) is False


def test_sweep_spares_live_probe(tmp_path):
    """sweep_dead_probes does not delete a probe whose owner pid is still alive."""
    workdir = tmp_path / "run"
    workdir.mkdir()
    refactory_dir = workdir / ".refactory"
    refactory_dir.mkdir()

    probe_dir = refactory_dir / "probe-test123"
    probe_dir.mkdir()
    (probe_dir / "owner.pid").write_text(str(os.getpid()))

    sweep_dead_probes(workdir)
    assert probe_dir.exists(), "should not delete a live probe"


def test_sweep_removes_dead_probe(tmp_path):
    """sweep_dead_probes removes a probe whose owner is dead."""
    workdir = tmp_path / "run"
    workdir.mkdir()
    refactory_dir = workdir / ".refactory"
    refactory_dir.mkdir()

    probe_dir = refactory_dir / "probe-test456"
    probe_dir.mkdir()
    (probe_dir / "owner.pid").write_text("99999999")

    sweep_dead_probes(workdir)
    assert not probe_dir.exists(), "should remove a dead probe"


def test_probe_result_to_dict():
    """ProbeResult serializes to dict."""
    result = ProbeResult(status="pass", digest="test digest")
    d = result.to_dict()
    assert d["status"] == "pass"
    assert d["digest"] == "test digest"


def test_emission_matches_last_no_worktree(tmp_path):
    """_emission_matches_last returns False when no worktree."""
    workdir = tmp_path / "run"
    workdir.mkdir()
    (workdir / ".refactory").mkdir()
    assert _emission_matches_last(workdir, "test content") is False
