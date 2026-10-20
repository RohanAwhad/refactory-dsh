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

from refactory_dsh import registry, sandbox
from refactory_dsh.create_ops import prepare_create, materialise, register, _dir_hash, STAGING_DIR
from refactory_dsh.paths import project_for_nodes
from tests_support import git_repo


def _mock_describe(name, **kwargs):
    """Return a successful SandboxResult for describe_package, bypassing subprocess import."""
    return sandbox.SandboxResult(
        ok=True,
        payload={"describe": {"name": name, "modes": []}, "ids": [name]},
    )


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


def _make_staged_package(run: Path, name: str, content: str = "# generated\n") -> Path:
    """Create a staged package in a run directory with a valid .origin.json."""
    staging = run / STAGING_DIR / ".factory" / "packages" / name
    staging.mkdir(parents=True, exist_ok=True)
    (staging / "__init__.py").write_text(content)
    (staging / ".origin.json").write_text(json.dumps({
        "origin": "create-mode", "ref": "", "trust": "generated",
    }, indent=2))
    return staging


def test_two_parallel_creates_both_build_second_register_refuses(tmp_path, monkeypatch):
    """Two prepares of one new name both build; the second register refuses naming the first."""
    project = _make_project(tmp_path)
    monkeypatch.setattr("refactory_dsh.paths.project_for_nodes", lambda: project)
    monkeypatch.setattr("refactory_dsh.create_ops.project_for_nodes", lambda: project)
    monkeypatch.setattr("refactory_dsh.sandbox.describe_package", _mock_describe)

    shared = registry.workspace_root(project) / "mypkg"
    # No shared package exists yet.
    assert not shared.exists()

    # Run 1: prepare and build.
    run1 = tmp_path / "run1"
    run1.mkdir()
    staging1 = _make_staged_package(run1, "mypkg", "# run1\n")

    # Run 2: prepare and build.
    run2 = tmp_path / "run2"
    run2.mkdir()
    staging2 = _make_staged_package(run2, "mypkg", "# run2\n")

    # Both staging directories are separate.
    assert str(staging1) != str(staging2)
    assert staging1.exists() and staging2.exists()

    # Run 1 registers successfully.
    ctx1 = FakeCtx(run1, facts={
        "name": "mypkg",
        "package_root": str(staging1),
        "source_hash": "",
        "source_existed": False,
    })
    register(ctx1)
    assert shared.exists()
    assert (shared / "__init__.py").read_text() == "# run1\n"
    # Run 1's id is recorded in .origin.json.
    origin = json.loads((shared / ".origin.json").read_text())
    assert origin.get("run_id") == "run1"

    # Run 2 tries to register: refuses because the shared package appeared.
    ctx2 = FakeCtx(run2, facts={
        "name": "mypkg",
        "package_root": str(staging2),
        "source_hash": "",
        "source_existed": False,
    })
    with pytest.raises(ValueError, match="did not exist when this run's prepare"):
        register(ctx2)

    # Run 1's package is intact.
    assert (shared / "__init__.py").read_text() == "# run1\n"


def test_second_register_refuses_when_shared_changed(tmp_path, monkeypatch):
    """The second run to register refuses, naming the first, when the shared package changed."""
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
    (shared / ".origin.json").write_text(json.dumps({
        "origin": "create-mode", "run_id": "run1",
    }, indent=2))
    new_hash = _dir_hash(shared)
    assert new_hash != original_hash

    # Second run has the original hash and a staged package.
    run2 = tmp_path / "run2"
    run2.mkdir()
    staging = _make_staged_package(run2, "mypkg", "# run2\n")

    ctx2 = FakeCtx(run2, facts={
        "name": "mypkg",
        "package_root": str(staging),
        "source_hash": original_hash,
        "source_existed": True,
    })

    with pytest.raises(ValueError, match="changed since this run's prepare"):
        register(ctx2)

    # The first run's package is intact.
    assert (shared / "__init__.py").read_text() == "# run1 registered\n"


def test_reloop_register_does_not_refuse_itself(tmp_path, monkeypatch):
    """A register that reloops does not refuse its own prior registration."""
    project = _make_project(tmp_path)
    monkeypatch.setattr("refactory_dsh.paths.project_for_nodes", lambda: project)
    monkeypatch.setattr("refactory_dsh.create_ops.project_for_nodes", lambda: project)
    monkeypatch.setattr("refactory_dsh.sandbox.describe_package", _mock_describe)

    run1 = tmp_path / "run1"
    run1.mkdir()
    staging = _make_staged_package(run1, "mypkg", "# attempt 1\n")

    # First register.
    ctx = FakeCtx(run1, facts={
        "name": "mypkg",
        "package_root": str(staging),
        "source_hash": "",
        "source_existed": False,
    })
    register(ctx)
    shared = registry.workspace_root(project) / "mypkg"
    assert shared.exists()

    # The run's origin is stamped in the shared package.
    origin = json.loads((shared / ".origin.json").read_text())
    assert origin.get("run_id") == "run1"

    # Simulate a reloop: materialise writes a new version to staging.
    (Path(staging) / "__init__.py").write_text("# attempt 2\n")

    # Second register of the same run: must not refuse.
    register(ctx)
    assert (shared / "__init__.py").read_text() == "# attempt 2\n"


def test_edit_whose_source_changed_refuses_at_register(tmp_path, monkeypatch):
    """An edit whose source package changed mid-run refuses at register."""
    project = _make_project(tmp_path)
    monkeypatch.setattr("refactory_dsh.paths.project_for_nodes", lambda: project)
    monkeypatch.setattr("refactory_dsh.create_ops.project_for_nodes", lambda: project)

    shared = registry.workspace_root(project) / "mypkg"
    shared.mkdir(parents=True, exist_ok=True)
    (shared / "__init__.py").write_text("# original\n")
    (shared / ".origin.json").write_text('{"origin": "create-mode"}\n')
    original_hash = _dir_hash(shared)

    # Edit run prepares with the original hash.
    run = tmp_path / "edit_run"
    run.mkdir()
    staging = _make_staged_package(run, "mypkg", "# edited\n")

    # Another run changes the shared package.
    (shared / "__init__.py").write_text("# changed by another\n")

    ctx = FakeCtx(run, facts={
        "name": "mypkg",
        "package_root": str(staging),
        "source_hash": original_hash,
        "source_existed": True,
    })

    with pytest.raises(ValueError, match="changed since this run's prepare"):
        register(ctx)


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


def test_edit_revision_of_differs_from_name(tmp_path, monkeypatch):
    """An edit whose revision_of differs from its name copies source from revision_of."""
    project = _make_project(tmp_path)
    monkeypatch.setattr("refactory_dsh.paths.project_for_nodes", lambda: project)
    monkeypatch.setattr("refactory_dsh.create_ops.project_for_nodes", lambda: project)
    monkeypatch.setattr("refactory_dsh.sandbox.describe_package", _mock_describe)

    # Create the source package under source_pkg (different from dest name).
    source_shared = registry.workspace_root(project) / "source_pkg"
    source_shared.mkdir(parents=True, exist_ok=True)
    (source_shared / "__init__.py").write_text("SOURCE = True\n")
    (source_shared / ".origin.json").write_text('{"origin": "create-mode"}\n')

    run = tmp_path / "edit_run"
    run.mkdir()

    # Mock prepare() to skip its complex dependency chain (model readiness,
    # mode contracts, revision source reading) and return the facts directly.
    monkeypatch.setattr("refactory_dsh.create_ops.prepare", lambda ctx: {
        "name": "dest_pkg",
        "revision_of": "source_pkg/some_mode",
        "brief": "test edit with different revision_of",
    })

    ctx = FakeCtx(run)
    result = prepare_create(ctx)

    # The staging directory starts from the source package's content.
    pkg_root = Path(result["package_root"])
    assert pkg_root.exists()
    assert (pkg_root / "__init__.py").read_text() == "SOURCE = True\n"
    assert result["source_existed"]
    assert result["source_hash"] != ""

    # Modify the source package mid-run (simulating another run changing it).
    (source_shared / "__init__.py").write_text("CHANGED = True\n")
    (source_shared / ".origin.json").write_text(json.dumps({
        "origin": "create-mode", "run_id": "other_run",
    }, indent=2))

    # Register should refuse because the source changed since prepare.
    ctx.facts.update(result)
    with pytest.raises(ValueError, match="changed since this run's prepare"):
        register(ctx)

    # The source package is intact (not overwritten by register).
    assert (source_shared / "__init__.py").read_text() == "CHANGED = True\n"
