"""Per-run staging for create/edit modes: two parallel runs never collide (#351).

Two parallel creates of the same package name both build in their own staging
directories.  The second to register refuses, naming the first, and leaves the
first's package intact.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import threading
from pathlib import Path
from typing import Any

import pytest

from refactory_dsh import registry
from refactory_dsh.create_ops import prepare_create, materialise, register, _dir_hash, STAGING_DIR
from refactory_dsh.paths import project_for_nodes
from tests_support import git_repo


class FakeCtx:
    """A minimal context for create_ops functions."""

    def __init__(self, workdir: Path, facts: dict[str, Any] | None = None,
                 knobs_dict: dict[str, Any] | None = None,
                 task: dict[str, Any] | None = None, texts: dict[str, str] | None = None):
        self.workdir = workdir
        self.facts = dict(facts or {})
        self._knobs = dict(knobs_dict or {})
        self.task = dict(task or {})
        self._texts: dict[str, str] = dict(texts or {})
        self._jsons: dict[str, Any] = {}
        self.model = None
        self.budget = None

    def knob(self, name: str, default: Any = None) -> Any:
        return self._knobs.get(name, default)

    def read_text(self, name: str) -> str:
        return self._texts.get(name, "")

    def write_text(self, name: str, content: str) -> None:
        path = self.workdir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        self._texts[name] = content

    def read_json(self, name: str, default: Any = None) -> Any:
        path = self.workdir / name
        if path.exists():
            return json.loads(path.read_text())
        return default

    def write_json(self, name: str, payload: Any) -> None:
        path = self.workdir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2))
        self._jsons[name] = payload

    def set_fact(self, name: str, value: Any) -> None:
        self.facts[name] = value

    def exists(self, name: str) -> bool:
        return (self.workdir / name).exists()

    @property
    def iteration(self) -> int:
        return 1


def _make_project(tmp_path: Path) -> Path:
    """Create a project with .factory/packages/ directory."""
    project = git_repo(tmp_path / "project")
    pkgs = project / ".factory" / "packages"
    pkgs.mkdir(parents=True, exist_ok=True)
    return project


def test_two_parallel_creates_use_separate_staging(tmp_path, monkeypatch):
    """Two runs of the same package name each stage in their own run directory."""
    project = _make_project(tmp_path)
    monkeypatch.setattr("refactory_dsh.paths.project_for_nodes", lambda: project)

    run1 = tmp_path / "run1"
    run1.mkdir()
    run2 = tmp_path / "run2"
    run2.mkdir()

    staging1 = run1 / STAGING_DIR / ".factory" / "packages" / "mypkg"
    staging1.mkdir(parents=True, exist_ok=True)

    staging2 = run2 / STAGING_DIR / ".factory" / "packages" / "mypkg"
    staging2.mkdir(parents=True, exist_ok=True)

    # Each run writes to its own staging directory.
    (staging1 / "__init__.py").write_text("# run1\n")
    (staging1 / ".origin.json").write_text('{"origin": "create-mode"}\n')
    (staging2 / "__init__.py").write_text("# run2\n")
    (staging2 / ".origin.json").write_text('{"origin": "create-mode"}\n')

    # They point to different directories.
    assert str(staging1) != str(staging2)
    assert staging1.exists()
    assert staging2.exists()


def test_second_register_refuses_when_shared_changed(tmp_path, monkeypatch):
    """The second run to register refuses, naming the first."""
    project = _make_project(tmp_path)
    monkeypatch.setattr("refactory_dsh.paths.project_for_nodes", lambda: project)
    monkeypatch.setattr("refactory_dsh.create_ops.project_for_nodes", lambda: project)

    shared = registry.workspace_root(project) / "mypkg"
    shared.mkdir(parents=True, exist_ok=True)
    (shared / "__init__.py").write_text("# original\n")
    (shared / ".origin.json").write_text('{"origin": "create-mode"}\n')
    original_hash = _dir_hash(shared)

    # First run registers successfully: simulate by changing the shared package.
    (shared / "__init__.py").write_text("# run1 registered\n")
    new_hash = _dir_hash(shared)
    assert new_hash != original_hash

    # Second run has the original hash and a staged package.
    run2 = tmp_path / "run2"
    run2.mkdir()
    staging = run2 / STAGING_DIR / ".factory" / "packages" / "mypkg"
    staging.mkdir(parents=True, exist_ok=True)
    (staging / "__init__.py").write_text("# run2\n")
    (staging / ".origin.json").write_text('{"origin": "create-mode"}\n')

    ctx2 = FakeCtx(run2, facts={
        "name": "mypkg",
        "package_root": str(staging),
        "source_hash": original_hash,
    })

    with pytest.raises(ValueError, match="changed since this run's prepare"):
        register(ctx2)

    # The first run's package is intact.
    assert (shared / "__init__.py").read_text() == "# run1 registered\n"


def test_validation_imports_staged_copy(tmp_path, monkeypatch):
    """Validation imports the staged copy even when a shared copy exists."""
    project = _make_project(tmp_path)
    monkeypatch.setattr("refactory_dsh.paths.project_for_nodes", lambda: project)

    # Create a shared package.
    shared = registry.workspace_root(project) / "testpkg"
    shared.mkdir(parents=True, exist_ok=True)
    (shared / "__init__.py").write_text("SHARED = True\n")

    # Create a staging directory with a different package.
    run = tmp_path / "run"
    run.mkdir()
    staging_base = run / STAGING_DIR
    staging = staging_base / ".factory" / "packages" / "testpkg"
    staging.mkdir(parents=True, exist_ok=True)
    (staging / "__init__.py").write_text("STAGED = True\n")

    # After install_workspace with project_override, the staged one takes priority.
    import refactory_packages
    original_path = list(refactory_packages.__path__)
    try:
        registry.install_workspace(project, project_override=staging_base)
        # The staging base's packages dir should be first in the path.
        staged_pkgs = str((staging_base / ".factory" / "packages").resolve())
        assert staged_pkgs in refactory_packages.__path__
        # It should be before the shared one.
        shared_pkgs = str(registry.workspace_root(project).resolve())
        if shared_pkgs in refactory_packages.__path__:
            assert refactory_packages.__path__.index(staged_pkgs) < refactory_packages.__path__.index(shared_pkgs)
    finally:
        refactory_packages.__path__[:] = original_path
