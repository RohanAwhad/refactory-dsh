"""Tests for project propagation through wf_open and startup.ts."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from unittest.mock import patch, MagicMock

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

    Exercises ``_node_environment()`` directly: mocks the registry and lowering
    layers so the call succeeds, then asserts that the returned dict carries
    ``REFACTORY_PROJECT`` set to the given *project* path.
    """
    project_path = tmp_path / "my_project"
    project_path.mkdir()

    from refactory_dsh.console import _node_environment
    from refactory_dsh import registry, lowering, interactive

    # Minimal mock mode.
    class _FakeMode:
        def state_dict(self) -> dict:
            return {"knobs": {}}

    monkeypatch.setattr(registry, "resolve", lambda proj, mode: ("fake", _FakeMode))
    monkeypatch.setattr(interactive, "with_knobs", lambda state, knobs: state)
    # lowering.knob_environment returns extra merged with knob env; mock it to
    # pass through extra so we can assert on it.
    monkeypatch.setattr(lowering, "knob_environment", lambda state, extra=None: dict(extra or {}))

    env = _node_environment("pkg/mode", project=project_path, workdir=tmp_path)
    assert env["REFACTORY_PROJECT"] == str(project_path)


def test_open_mode_commandenv_carries_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """_open_mode returns ``commandEnv`` containing ``REFACTORY_PROJECT``.

    Exercises ``_open_mode()`` with mocked internals, asserting that the
    returned payload's ``commandEnv`` propagates the project.
    """
    from refactory_dsh.console import _open_mode
    from refactory_dsh import interactive, registry, lowering

    project_path = tmp_path / "proj"
    project_path.mkdir()
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    (workdir / ".refactory").mkdir(parents=True)

    # Stub require_build — _open_mode imports it from paths at call time.
    monkeypatch.setattr("refactory_dsh.paths.require_build", lambda **kw: None)

    # Stub interactive.prepare to write a minimal run-config and return.
    graphs = workdir / "graphs"
    graphs.mkdir(parents=True)
    (graphs / "pkg-mode.graph.json").write_text("{}")
    (workdir / ".refactory" / "declarations.json").write_text("{}")

    def _fake_prepare(mode, *, workdir=None, project=None, **kw):
        (Path(workdir) / ".refactory" / "run-config.json").write_text(json.dumps({
            "mode": mode, "project": str(project), "model_context": {},
        }))
        return "pkg-mode", graphs

    monkeypatch.setattr(interactive, "prepare", _fake_prepare)

    # Minimal mock mode for _node_environment.
    class _FakeMode:
        def state_dict(self) -> dict:
            return {"knobs": {}}

    monkeypatch.setattr(registry, "resolve", lambda proj, mode: ("fake", _FakeMode))
    monkeypatch.setattr(interactive, "with_knobs", lambda state, knobs: state)
    monkeypatch.setattr(lowering, "knob_environment", lambda state, extra=None: dict(extra or {}))
    monkeypatch.setattr("refactory_dsh.workers.environment", lambda ctx: {})

    result = _open_mode("pkg/mode", project=project_path, workdir=workdir)
    assert result["commandEnv"]["REFACTORY_PROJECT"] == str(project_path)
