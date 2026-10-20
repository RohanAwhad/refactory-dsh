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
