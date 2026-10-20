"""Tests for the workspace-wide build stamp and require_build()."""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


def _make_package_tree(root: Path, packages: list[str], files: dict[str, str] | None = None) -> None:
    """Create a minimal TS package tree."""
    for pkg in packages:
        src_dir = root / pkg / "src"
        src_dir.mkdir(parents=True, exist_ok=True)
        (src_dir / "index.ts").write_text(files.get(pkg, "export const x = 1;") if files else "export const x = 1;")


def test_workspace_stamp_changes_on_non_spine_source(tmp_path: Path) -> None:
    """A source change in a package other than workflow-spine changes the stamp."""
    from refactory_dsh.pin import workspace_stamp, WORKSPACE_PACKAGES

    _make_package_tree(tmp_path, list(WORKSPACE_PACKAGES))
    stamp1 = workspace_stamp(tmp_path)
    assert stamp1 is not None

    # Modify a file in graph bundle, not workflow-spine.
    (tmp_path / "packages/bundle/graph/src/index.ts").write_text("export const y = 2;")
    stamp2 = workspace_stamp(tmp_path)
    assert stamp2 is not None
    assert stamp1 != stamp2


def test_workspace_stamp_deterministic(tmp_path: Path) -> None:
    """The stamp is deterministic for the same source tree."""
    from refactory_dsh.pin import workspace_stamp, WORKSPACE_PACKAGES

    _make_package_tree(tmp_path, list(WORKSPACE_PACKAGES))
    stamp1 = workspace_stamp(tmp_path)
    stamp2 = workspace_stamp(tmp_path)
    assert stamp1 == stamp2
    assert stamp1 is not None
    assert len(stamp1) == 16


def test_require_build_triggers_rebuild_when_stale(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A stale workspace stamp triggers pnpm build."""
    from refactory_dsh import paths, pin
    from refactory_dsh.pin import WORKSPACE_PACKAGES

    _make_package_tree(tmp_path, list(WORKSPACE_PACKAGES))

    # Create the spine lib directory.
    spine_lib = tmp_path / "packages" / "workflow" / "workflow-spine" / "lib"
    spine_lib.mkdir(parents=True, exist_ok=True)
    (spine_lib / "index.js").write_text("// built")

    # Write a stale workspace stamp.
    (spine_lib / ".workspace-stamp").write_text("0000000000000000")

    monkeypatch.delenv("REFACTORY_GUI_MODE", raising=False)

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        paths.require_build(root=tmp_path)
        # pnpm build should have been called.
        calls = [c for c in mock_run.call_args_list
                 if isinstance(c[0][0], list) and "build" in c[0][0]]
        assert len(calls) > 0


def test_require_build_asks_restart_under_gui(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Under GUI mode, a stale build raises RuntimeError instead of rebuilding."""
    from refactory_dsh import paths
    from refactory_dsh.pin import WORKSPACE_PACKAGES

    _make_package_tree(tmp_path, list(WORKSPACE_PACKAGES))

    spine_lib = tmp_path / "packages" / "workflow" / "workflow-spine" / "lib"
    spine_lib.mkdir(parents=True, exist_ok=True)
    (spine_lib / "index.js").write_text("// built")
    (spine_lib / ".workspace-stamp").write_text("0000000000000000")

    monkeypatch.setenv("REFACTORY_GUI_MODE", "1")

    with pytest.raises(RuntimeError, match="restart"):
        paths.require_build(root=tmp_path)


def test_require_build_skips_when_running_from_source(tmp_path: Path) -> None:
    """Running from source (SOURCE_CLI_ENTRY exists) skips the stamp check."""
    from refactory_dsh import paths

    # Create the source CLI entry.
    source_entry = tmp_path / paths.SOURCE_CLI_ENTRY
    source_entry.parent.mkdir(parents=True, exist_ok=True)
    source_entry.write_text("// source")

    # No stamp file, no lib — should not raise.
    paths.require_build(root=tmp_path)


def test_refuse_rebuild_when_unpinned_live_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An unpinned live run on the same checkout blocks a rebuild."""
    from refactory_dsh import paths
    from refactory_dsh.pin import WORKSPACE_PACKAGES, register_run_on_driver

    _make_package_tree(tmp_path, list(WORKSPACE_PACKAGES))

    spine_lib = tmp_path / "packages" / "workflow" / "workflow-spine" / "lib"
    spine_lib.mkdir(parents=True, exist_ok=True)
    (spine_lib / "index.js").write_text("// built")
    (spine_lib / ".workspace-stamp").write_text("0000000000000000")

    monkeypatch.delenv("REFACTORY_GUI_MODE", raising=False)

    # Register a run on this checkout.
    run_dir = tmp_path / "my_run"
    run_dir.mkdir(parents=True)
    (run_dir / ".refactory").mkdir(parents=True)
    register_run_on_driver(tmp_path, run_dir)

    # Mock run_ownership.held to report the run as live.
    monkeypatch.setattr("refactory_dsh.run_ownership.held", lambda wd: True)

    with pytest.raises(RuntimeError, match="live run"):
        paths.require_build(root=tmp_path)


def test_require_build_check_only_never_rebuilds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """check_only=True raises instead of rebuilding, so mid-run calls never build."""
    from refactory_dsh import paths
    from refactory_dsh.pin import WORKSPACE_PACKAGES

    _make_package_tree(tmp_path, list(WORKSPACE_PACKAGES))

    spine_lib = tmp_path / "packages" / "workflow" / "workflow-spine" / "lib"
    spine_lib.mkdir(parents=True, exist_ok=True)
    (spine_lib / "index.js").write_text("// built")
    (spine_lib / ".workspace-stamp").write_text("0000000000000000")

    monkeypatch.delenv("REFACTORY_GUI_MODE", raising=False)

    with pytest.raises(RuntimeError, match="stale"):
        paths.require_build(root=tmp_path, check_only=True)
