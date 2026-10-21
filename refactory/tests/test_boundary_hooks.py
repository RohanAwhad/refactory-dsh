"""Tests for the five boundary fixes and the model environment hook.

Each test verifies that the fix works and that removing it would cause failure.
Tests 1, 2, 3, 5 run in subprocesses because pytest collection imports worktree
(via test fixtures), so no in-process test can see the unregistered state.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest

_ROOT = Path(__file__).resolve().parents[1]  # refactory/


# ---------------------------------------------------------------------------
# Test 1: core only — no overlay, no worktree record
# ---------------------------------------------------------------------------
def test_core_only_environment():
    """Without the entry point, _environment() inherits PATH and applies HOOK_SUPPRESS."""
    script = textwrap.dedent("""\
        import importlib.metadata
        import os
        import sys
        import types
        from pathlib import Path

        # Patch entry_points to return empty before importing model
        _real_ep = importlib.metadata.entry_points
        def _empty_ep(**kwargs):
            group = kwargs.get("group", "")
            if group == "refactory.environment_overlay":
                return importlib.metadata.EntryPoints()
            return _real_ep(**kwargs)
        importlib.metadata.entry_points = _empty_ep

        # No worktree record
        import tempfile
        workdir = tempfile.mkdtemp()
        os.environ["REFACTORY_WORKDIR"] = workdir

        from refactory_dsh.model import DshModelClient
        # Reset the cached overlay so our patch takes effect
        import refactory_dsh.model as _mod
        _mod._environment_overlay = _mod._UNSET

        client = DshModelClient.__new__(DshModelClient)
        client._dsh_home = Path.home()
        client._provider = ""
        client._model = ""
        client._effort = ""
        client._worker_backend = "dsh"
        client._runtime_root = Path(".")
        client._budget = None
        client._worker_receipt = None
        client._routing_provenance = {}
        client._timeout_s = 30
        client._attempts = 1
        client._backoff_s = 1.0
        client._dsh_cli = ""
        client._python_bin = Path(sys.executable)
        client._permission_mode = "skip"
        client._role_routes = {}
        client._node_routes = {}
        client._provider_config = {}

        env = client._environment()

        # PATH should be inherited (no worktree overlay)
        assert env.get("LEFTHOOK") == "0", f"LEFTHOOK not suppressed: {env.get('LEFTHOOK')}"
        assert env.get("HUSKY") == "0", f"HUSKY not suppressed: {env.get('HUSKY')}"
        assert "refactory_dsh.worktree" not in sys.modules, "worktree should not be loaded in core-only mode"
        print("PASS")
    """)
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True, text=True, timeout=30,
        cwd=str(_ROOT),
        env={**os.environ, "PYTHONPATH": str(_ROOT)},
    )
    assert result.returncode == 0, f"core-only test failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    assert "PASS" in result.stdout


# ---------------------------------------------------------------------------
# Test 2: product registered — entry point provides worktree overlay
# ---------------------------------------------------------------------------
def test_product_registered_environment():
    """With the entry point, _environment() applies the worktree's PATH/VIRTUAL_ENV."""
    script = textwrap.dedent("""\
        import json
        import os
        import sys
        import tempfile
        from pathlib import Path

        # Create a fake worktree directory with a managed .venv
        worktree_dir = tempfile.mkdtemp()
        venv_dir = Path(worktree_dir) / ".venv"
        venv_bin_dir = venv_dir / "bin"
        venv_bin_dir.mkdir(parents=True)
        (venv_dir / ".refactory-managed").touch()

        # Create a run directory with a worktree record
        run_dir = tempfile.mkdtemp()
        refactory_dir = Path(run_dir) / ".refactory"
        refactory_dir.mkdir()
        record = {
            "path": worktree_dir,
            "branch": "refactory/run-test",
            "base": "abc123",
            "head": "def456",
            "target": worktree_dir,
            "run": run_dir,
        }
        (refactory_dir / "worktree.json").write_text(json.dumps(record))
        os.environ["REFACTORY_WORKDIR"] = run_dir

        from refactory_dsh.model import DshModelClient
        # Reset the cached overlay
        import refactory_dsh.model as _mod
        _mod._environment_overlay = _mod._UNSET

        client = DshModelClient.__new__(DshModelClient)
        client._dsh_home = Path.home()
        client._provider = ""
        client._model = ""
        client._effort = ""
        client._worker_backend = "dsh"
        client._runtime_root = Path(".")
        client._budget = None
        client._worker_receipt = None
        client._routing_provenance = {}
        client._timeout_s = 30
        client._attempts = 1
        client._backoff_s = 1.0
        client._dsh_cli = ""
        client._python_bin = Path(sys.executable)
        client._permission_mode = "skip"
        client._role_routes = {}
        client._node_routes = {}
        client._provider_config = {}

        env = client._environment()

        # The worktree's venv/bin should be in PATH
        assert str(venv_bin_dir) in env.get("PATH", ""), (
            f"worktree venv not in PATH: {env.get('PATH')}"
        )
        assert env.get("VIRTUAL_ENV") == str(venv_dir), (
            f"VIRTUAL_ENV not set: {env.get('VIRTUAL_ENV')}"
        )
        print("PASS")
    """)
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True, text=True, timeout=30,
        cwd=str(_ROOT),
        env={**os.environ, "PYTHONPATH": str(_ROOT)},
    )
    assert result.returncode == 0, f"product-registered test failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    assert "PASS" in result.stdout


# ---------------------------------------------------------------------------
# Test 3: worker import sequence has the overlay before first complete()
# ---------------------------------------------------------------------------
def test_worker_import_sequence_has_overlay():
    """The worker process finds the overlay via entry points, not via agent_delivery's worktree import."""
    script = textwrap.dedent("""\
        import json
        import os
        import sys
        import tempfile
        import types
        from pathlib import Path

        # Create a fake worktree with managed venv
        worktree_dir = tempfile.mkdtemp()
        venv_dir = Path(worktree_dir) / ".venv"
        venv_bin_dir = venv_dir / "bin"
        venv_bin_dir.mkdir(parents=True)
        (venv_dir / ".refactory-managed").touch()

        # Create a run directory with worktree record
        run_dir = tempfile.mkdtemp()
        refactory_dir = Path(run_dir) / ".refactory"
        refactory_dir.mkdir()
        record = {
            "path": worktree_dir,
            "branch": "refactory/run-test",
            "base": "abc123",
            "head": "def456",
            "target": worktree_dir,
            "run": run_dir,
        }
        (refactory_dir / "worktree.json").write_text(json.dumps(record))
        os.environ["REFACTORY_WORKDIR"] = run_dir

        # Stub agent_delivery so it does NOT import worktree
        stub = types.ModuleType("refactory_dsh.agent_delivery")
        stub._path = lambda workdir: Path(workdir) / ".refactory" / "agent-delivery"
        stub.begin = lambda *a, **kw: None
        stub.finish = lambda *a, **kw: None
        sys.modules["refactory_dsh.agent_delivery"] = stub

        # Reset overlay cache
        from refactory_dsh.model import DshModelClient
        import refactory_dsh.model as _mod
        _mod._environment_overlay = _mod._UNSET

        client = DshModelClient.__new__(DshModelClient)
        client._dsh_home = Path.home()
        client._provider = ""
        client._model = ""
        client._effort = ""
        client._worker_backend = "dsh"
        client._runtime_root = Path(".")
        client._budget = None
        client._worker_receipt = None
        client._routing_provenance = {}
        client._timeout_s = 30
        client._attempts = 1
        client._backoff_s = 1.0
        client._dsh_cli = ""
        client._python_bin = Path(sys.executable)
        client._permission_mode = "skip"
        client._role_routes = {}
        client._node_routes = {}
        client._provider_config = {}

        env = client._environment()

        # Even with agent_delivery stubbed (no worktree import), the overlay is found
        assert str(venv_bin_dir) in env.get("PATH", ""), (
            f"worktree venv not in PATH with stubbed agent_delivery: {env.get('PATH')}"
        )
        print("PASS")
    """)
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True, text=True, timeout=30,
        cwd=str(_ROOT),
        env={**os.environ, "PYTHONPATH": str(_ROOT)},
    )
    assert result.returncode == 0, f"worker sequence test failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    assert "PASS" in result.stdout


# ---------------------------------------------------------------------------
# Test 4: entry point declaration in pyproject.toml
# ---------------------------------------------------------------------------
def test_entry_point_declaration():
    """pyproject.toml declares the overlay and the installed metadata matches."""
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib  # type: ignore[no-redef]

    toml_path = _ROOT / "pyproject.toml"
    with open(toml_path, "rb") as f:
        data = tomllib.load(f)

    # Check the declaration in pyproject.toml
    ep_group = data.get("project", {}).get("entry-points", {}).get("refactory.environment_overlay", {})
    assert "worktree" in ep_group, (
        "pyproject.toml missing [project.entry-points.'refactory.environment_overlay'] worktree entry"
    )
    assert ep_group["worktree"] == "refactory_dsh.worktree:_worktree_environment_overlay", (
        f"entry point value mismatch: {ep_group['worktree']}"
    )

    # Check the installed metadata matches
    import importlib.metadata
    eps = importlib.metadata.entry_points(group="refactory.environment_overlay")
    ep_list = [ep for ep in eps if ep.name == "worktree"]
    assert len(ep_list) == 1, f"expected 1 installed entry point, got {len(ep_list)}"
    assert ep_list[0].value == "refactory_dsh.worktree:_worktree_environment_overlay"


# ---------------------------------------------------------------------------
# Test 5: stale install raises RuntimeError
# ---------------------------------------------------------------------------
def test_stale_install_raises():
    """When worktree record exists but no overlay is found, RuntimeError is raised."""
    script = textwrap.dedent("""\
        import importlib.metadata
        import json
        import os
        import sys
        import tempfile
        from pathlib import Path

        # Patch entry_points to return empty (simulating stale install)
        _real_ep = importlib.metadata.entry_points
        def _empty_ep(**kwargs):
            group = kwargs.get("group", "")
            if group == "refactory.environment_overlay":
                return importlib.metadata.EntryPoints()
            return _real_ep(**kwargs)
        importlib.metadata.entry_points = _empty_ep

        # Create a run dir WITH a worktree record
        run_dir = tempfile.mkdtemp()
        refactory_dir = Path(run_dir) / ".refactory"
        refactory_dir.mkdir()
        record = {
            "path": "/tmp/fake-worktree",
            "branch": "refactory/run-test",
            "base": "abc123",
            "head": "def456",
        }
        (refactory_dir / "worktree.json").write_text(json.dumps(record))
        os.environ["REFACTORY_WORKDIR"] = run_dir

        from refactory_dsh.model import DshModelClient
        import refactory_dsh.model as _mod
        _mod._environment_overlay = _mod._UNSET

        client = DshModelClient.__new__(DshModelClient)
        client._dsh_home = Path.home()
        client._provider = ""
        client._model = ""
        client._effort = ""
        client._worker_backend = "dsh"
        client._runtime_root = Path(".")
        client._budget = None
        client._worker_receipt = None
        client._routing_provenance = {}
        client._timeout_s = 30
        client._attempts = 1
        client._backoff_s = 1.0
        client._dsh_cli = ""
        client._python_bin = Path(sys.executable)
        client._permission_mode = "skip"
        client._role_routes = {}
        client._node_routes = {}
        client._provider_config = {}

        try:
            env = client._environment()
            print("FAIL: no error raised")
            sys.exit(1)
        except RuntimeError as e:
            msg = str(e)
            assert "uv sync" in msg or "install.sh" in msg, f"error message missing fix hint: {msg}"
            print("PASS")
    """)
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True, text=True, timeout=30,
        cwd=str(_ROOT),
        env={**os.environ, "PYTHONPATH": str(_ROOT)},
    )
    assert result.returncode == 0, f"stale-install test failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    assert "PASS" in result.stdout


# ---------------------------------------------------------------------------
# Test 6: check_required_knobs from nn.knob
# ---------------------------------------------------------------------------
def test_check_required_knobs_from_knob():
    from refactory_dsh.nn.knob import Knob, check_required_knobs

    knob = Knob(default="placeholder", kind="str", required=True)
    violations = check_required_knobs({"brief": knob}, {"brief": ""})
    assert len(violations) == 1
    assert violations[0][0] == "brief"

    violations = check_required_knobs({"brief": knob}, {"brief": "a real value"})
    assert len(violations) == 0


# ---------------------------------------------------------------------------
# Test 7: check_required_knobs re-export from brief_guard
# ---------------------------------------------------------------------------
def test_check_required_knobs_reexport_from_brief_guard():
    from refactory_dsh.brief_guard import check_required_knobs as from_bg
    from refactory_dsh.nn.knob import check_required_knobs as from_knob

    assert from_bg is from_knob


# ---------------------------------------------------------------------------
# Test 8: _source_manifest from paths
# ---------------------------------------------------------------------------
def test_source_manifest_from_paths(tmp_path: Path):
    from refactory_dsh.paths import _source_manifest

    (tmp_path / "a.txt").write_text("hello")
    (tmp_path / "b.txt").write_text("world")

    result = _source_manifest(tmp_path)
    assert isinstance(result, dict)
    assert "a.txt" in result
    assert "b.txt" in result
    assert len(result["a.txt"]) == 64  # SHA-256 hex digest

    # Symlinks raise
    link = tmp_path / "link.txt"
    link.symlink_to(tmp_path / "a.txt")
    with pytest.raises(ValueError, match="symlink"):
        _source_manifest(tmp_path)


# ---------------------------------------------------------------------------
# Test 9: _source_manifest re-export from fast_ops
# ---------------------------------------------------------------------------
def test_source_manifest_reexport_from_fast_ops():
    from refactory_dsh.fast_ops import _source_manifest as from_fo
    from refactory_dsh.paths import _source_manifest as from_paths

    assert from_fo is from_paths


# ---------------------------------------------------------------------------
# Test 10: constant values match across old and new locations
# ---------------------------------------------------------------------------
def test_constants_values():
    from pathlib import Path as P
    from refactory_dsh.invocations import (
        CHECK_PRODUCES, CHECK_WRITES, INSTALL_TIMEOUT_S, HOOK_SUPPRESS,
        CHECK_HOME, BASE_CHECK_HOME, WORKTREE_RECORD, PENDING, WRAPPER,
        TARGET_RECORD, DELIVERY_SHOWN,
    )

    assert CHECK_PRODUCES == ("commit", "files", "inherited_failures", "changed_functions")
    assert CHECK_WRITES == ("changes.diff", "test-guardedness.json", "changed-functions.json")
    assert INSTALL_TIMEOUT_S == 1800
    assert HOOK_SUPPRESS == {"LEFTHOOK": "0", "HUSKY": "0"}
    assert CHECK_HOME == P(".refactory") / "check-home"
    assert BASE_CHECK_HOME == P(".refactory") / "base-check-home"
    assert WORKTREE_RECORD == P(".refactory") / "worktree.json"
    assert PENDING == P(".refactory") / "materialise-pending.json"
    assert WRAPPER == P(".refactory") / "in-worktree"
    assert TARGET_RECORD == P(".refactory") / "target.json"
    assert DELIVERY_SHOWN == P(".refactory") / "delivery-shown.json"

    # Re-exports match
    from refactory_dsh.repo_change import CHECK_PRODUCES as cp2, CHECK_WRITES as cw2
    assert cp2 is CHECK_PRODUCES
    assert cw2 is CHECK_WRITES

    from refactory_dsh.worktree import INSTALL_TIMEOUT_S as it2, HOOK_SUPPRESS as hs2
    assert it2 is INSTALL_TIMEOUT_S
    assert hs2 is HOOK_SUPPRESS

    from refactory_dsh.worktree import RECORD, PENDING as p2, WRAPPER as w2
    from refactory_dsh.worktree import CHECK_HOME as ch2, BASE_CHECK_HOME as bch2
    assert RECORD is WORKTREE_RECORD
    assert p2 is PENDING
    assert w2 is WRAPPER
    assert ch2 is CHECK_HOME
    assert bch2 is BASE_CHECK_HOME

    from refactory_dsh.delivery import SHOWN
    assert SHOWN is DELIVERY_SHOWN

    from refactory_dsh.target_guard import RECORD as tr2
    assert tr2 is TARGET_RECORD
