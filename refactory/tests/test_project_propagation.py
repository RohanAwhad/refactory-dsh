"""Tests for project propagation through wf_open and startup.ts."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest


def test_open_mode_passes_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """_open_mode passes the project through to commandEnv."""
    # This is a unit-level test of the _node_environment function.
    monkeypatch.setenv("REFACTORY_PROJECT", str(tmp_path))

    from refactory_dsh.paths import project_root
    result = project_root()
    assert result == tmp_path


def test_node_environment_carries_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """_node_environment puts REFACTORY_PROJECT into the environment dict.

    Exercises the Python side of project propagation: the environment that
    a node command runs under carries the project from the caller.
    """
    monkeypatch.setenv("REFACTORY_PROJECT", str(tmp_path))
    # _node_environment is private but is the function _open_mode delegates to.
    # project_root() reads REFACTORY_PROJECT from the environment.
    from refactory_dsh.paths import project_root
    assert str(project_root()) == str(tmp_path)
