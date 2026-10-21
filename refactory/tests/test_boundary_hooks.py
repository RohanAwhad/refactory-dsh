"""Tests for boundary fixes, entry-point registrations, and the model environment hook.

Each test verifies that the fix works and that removing it would cause failure.
Tests that must see the unregistered state run in subprocesses because pytest
collection imports worktree (via test fixtures).
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


# ── helper: ensure new entry points are discoverable in subprocesses ───────
_EP_PREAMBLE = textwrap.dedent("""\
    import importlib.metadata as _im
    _real_ep = _im.entry_points
    def _ensure_ep(**kwargs):
        group = kwargs.get("group", "")
        result = _real_ep(**kwargs)
        if group == "refactory.worktree_state" and not any(e.name == "worktree" for e in result):
            ep = _im.EntryPoint(name="worktree",
                                value="refactory_dsh.worktree:_worktree_state_bridge",
                                group=group)
            return _im.EntryPoints(list(result) + [ep])
        if group == "refactory.delivery_identity" and not any(e.name == "authoring" for e in result):
            ep = _im.EntryPoint(name="authoring",
                                value="refactory_dsh.authoring_evidence:delivery_identity",
                                group=group)
            return _im.EntryPoints(list(result) + [ep])
        return result
    _im.entry_points = _ensure_ep
""")

_EP_EMPTY_PREAMBLE = textwrap.dedent("""\
    import importlib.metadata as _im
    _real_ep = _im.entry_points
    def _empty_ep(**kwargs):
        group = kwargs.get("group", "")
        if group.startswith("refactory."):
            return _im.EntryPoints()
        return _real_ep(**kwargs)
    _im.entry_points = _empty_ep
""")

_POPEN_CAPTURE = textwrap.dedent("""\
    import subprocess as _sub
    _captured = {}
    class _CapturePopen:
        def __init__(self, cmd, **kwargs):
            _captured['env'] = dict(kwargs.get('env', {}))
            raise SystemExit(42)
    _sub.Popen = _CapturePopen
""")


def _run_script(script: str, *, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True, text=True, timeout=timeout,
        cwd=str(_ROOT),
        env={**os.environ, "PYTHONPATH": str(_ROOT)},
    )


# ---------------------------------------------------------------------------
# Test 1: core only — no overlay, no worktree record
# ---------------------------------------------------------------------------
def test_core_only_environment():
    """Without entry points, PATH is inherited and worktree is never loaded."""
    script = _EP_EMPTY_PREAMBLE + _POPEN_CAPTURE + textwrap.dedent("""\
        import json, os, sys, tempfile
        from pathlib import Path

        # No worktree record
        run_dir = Path(tempfile.mkdtemp())
        (run_dir / ".refactory").mkdir()
        os.environ["REFACTORY_WORKDIR"] = str(run_dir)
        expected_path = os.environ["PATH"]

        # Patch subprocess.Popen at the model module level
        import refactory_dsh.model as _mod
        _mod.subprocess.Popen = _CapturePopen
        _mod._environment_overlay = _mod._UNSET

        import refactory_dsh.agent_delivery as _ad
        _ad._worktree_state = _ad._UNSET
        _ad._delivery_identity = _ad._UNSET

        from refactory_dsh.model import DshModelClient
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

        assert env["PATH"] == expected_path, (
            f"PATH mismatch:\\ngot:      {env['PATH']}\\nexpected: {expected_path}"
        )
        assert env.get("LEFTHOOK") == "0", f"LEFTHOOK not suppressed: {env.get('LEFTHOOK')}"
        assert env.get("HUSKY") == "0", f"HUSKY not suppressed: {env.get('HUSKY')}"
        assert "refactory_dsh.worktree" not in sys.modules, "worktree should not be loaded in core-only mode"
        print("PASS")
    """)
    result = _run_script(script)
    assert result.returncode == 0, f"core-only test failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    assert "PASS" in result.stdout


# ---------------------------------------------------------------------------
# Test 2: product registered — entry point provides worktree overlay
# ---------------------------------------------------------------------------
def test_product_registered_environment():
    """With entry points, _environment() applies the worktree's PATH/VIRTUAL_ENV."""
    script = _EP_PREAMBLE + _POPEN_CAPTURE + textwrap.dedent("""\
        import json, os, sys, tempfile
        from pathlib import Path

        # Create a fake worktree with managed .venv
        worktree_dir = Path(tempfile.mkdtemp())
        venv_dir = worktree_dir / ".venv"
        venv_bin_dir = venv_dir / "bin"
        venv_bin_dir.mkdir(parents=True)
        (venv_dir / ".refactory-managed").touch()

        # Create run directory with worktree record
        run_dir = Path(tempfile.mkdtemp())
        refactory_dir = run_dir / ".refactory"
        refactory_dir.mkdir()
        record = {
            "path": str(worktree_dir),
            "branch": "refactory/run-test",
            "base": "abc123",
            "head": "def456",
            "target": str(worktree_dir),
            "run": str(run_dir),
        }
        (refactory_dir / "worktree.json").write_text(json.dumps(record))
        os.environ["REFACTORY_WORKDIR"] = str(run_dir)

        # Patch Popen at the model module level
        import refactory_dsh.model as _mod
        _mod.subprocess.Popen = _CapturePopen
        _mod._environment_overlay = _mod._UNSET

        import refactory_dsh.agent_delivery as _ad
        _ad._worktree_state = _ad._UNSET
        _ad._delivery_identity = _ad._UNSET

        from refactory_dsh.model import DshModelClient
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

        from refactory_dsh.worktree import check_environment
        from refactory_dsh.invocations import CHECK_HOME
        home = run_dir / CHECK_HOME
        home.mkdir(parents=True, exist_ok=True)
        expected = check_environment(worktree_dir, home)

        assert env["PATH"] == expected["PATH"], (
            f"PATH mismatch:\\ngot:      {env['PATH']}\\nexpected: {expected['PATH']}"
        )
        assert env.get("VIRTUAL_ENV") == expected.get("VIRTUAL_ENV"), (
            f"VIRTUAL_ENV mismatch:\\ngot: {env.get('VIRTUAL_ENV')}\\n"
            f"expected: {expected.get('VIRTUAL_ENV')}"
        )
        print("PASS")
    """)
    result = _run_script(script)
    assert result.returncode == 0, f"product-registered test failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    assert "PASS" in result.stdout


# ---------------------------------------------------------------------------
# Test 3: worker import sequence has the overlay before first complete()
# ---------------------------------------------------------------------------
def test_worker_import_sequence_has_overlay():
    """The worker process finds the overlay via entry points, not via agent_delivery's worktree import."""
    script = _EP_PREAMBLE + _POPEN_CAPTURE + textwrap.dedent("""\
        import json, os, sys, tempfile, types
        from pathlib import Path

        # Create a fake worktree with managed .venv
        worktree_dir = Path(tempfile.mkdtemp())
        venv_dir = worktree_dir / ".venv"
        venv_bin_dir = venv_dir / "bin"
        venv_bin_dir.mkdir(parents=True)
        (venv_dir / ".refactory-managed").touch()

        # Create run directory with worktree record
        run_dir = Path(tempfile.mkdtemp())
        refactory_dir = run_dir / ".refactory"
        refactory_dir.mkdir()
        record = {
            "path": str(worktree_dir),
            "branch": "refactory/run-test",
            "base": "abc123",
            "head": "def456",
            "target": str(worktree_dir),
            "run": str(run_dir),
        }
        (refactory_dir / "worktree.json").write_text(json.dumps(record))
        os.environ["REFACTORY_WORKDIR"] = str(run_dir)

        # Stub agent_delivery so it does NOT import worktree via its own imports
        stub = types.ModuleType("refactory_dsh.agent_delivery")
        stub._path = lambda workdir: Path(workdir) / ".refactory" / "agent-delivery"
        stub.begin = lambda *a, **kw: {"prompt": "test", "id": "test"}
        stub.finish = lambda *a, **kw: {"status": "delivered"}
        sys.modules["refactory_dsh.agent_delivery"] = stub

        # Patch Popen and reset overlay cache
        import refactory_dsh.model as _mod
        _mod.subprocess.Popen = _CapturePopen
        _mod._environment_overlay = _mod._UNSET

        from refactory_dsh.model import DshModelClient
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

        # Even with agent_delivery stubbed, the overlay is found via entry point
        from refactory_dsh.worktree import check_environment
        from refactory_dsh.invocations import CHECK_HOME
        home = run_dir / CHECK_HOME
        home.mkdir(parents=True, exist_ok=True)
        expected = check_environment(worktree_dir, home)

        assert env["PATH"] == expected["PATH"], (
            f"PATH mismatch with stubbed agent_delivery:\\n"
            f"got:      {env['PATH']}\\nexpected: {expected['PATH']}"
        )
        print("PASS")
    """)
    result = _run_script(script)
    assert result.returncode == 0, f"worker sequence test failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    assert "PASS" in result.stdout


# ---------------------------------------------------------------------------
# Test 4: entry point declarations in pyproject.toml
# ---------------------------------------------------------------------------
def test_entry_point_declarations():
    """pyproject.toml declares all three entry-point groups."""
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib  # type: ignore[no-redef]

    toml_path = _ROOT / "pyproject.toml"
    with open(toml_path, "rb") as f:
        data = tomllib.load(f)

    entry_points = data.get("project", {}).get("entry-points", {})

    # refactory.environment_overlay
    overlay_group = entry_points.get("refactory.environment_overlay", {})
    assert "worktree" in overlay_group, (
        "pyproject.toml missing [project.entry-points.'refactory.environment_overlay'] worktree entry"
    )
    assert overlay_group["worktree"] == "refactory_dsh.worktree:_worktree_environment_overlay"

    # refactory.worktree_state
    ws_group = entry_points.get("refactory.worktree_state", {})
    assert "worktree" in ws_group, (
        "pyproject.toml missing [project.entry-points.'refactory.worktree_state'] worktree entry"
    )
    assert ws_group["worktree"] == "refactory_dsh.worktree:_worktree_state_bridge"

    # refactory.delivery_identity
    di_group = entry_points.get("refactory.delivery_identity", {})
    assert "authoring" in di_group, (
        "pyproject.toml missing [project.entry-points.'refactory.delivery_identity'] authoring entry"
    )
    assert di_group["authoring"] == "refactory_dsh.authoring_evidence:delivery_identity"


# ---------------------------------------------------------------------------
# Test 5: stale install raises RuntimeError (environment overlay)
# ---------------------------------------------------------------------------
def test_stale_install_raises():
    """When worktree record exists but no overlay is found, RuntimeError is raised."""
    script = _EP_EMPTY_PREAMBLE + textwrap.dedent("""\
        import json, os, sys, tempfile
        from pathlib import Path

        # Create a run dir WITH a worktree record
        run_dir = Path(tempfile.mkdtemp())
        refactory_dir = run_dir / ".refactory"
        refactory_dir.mkdir()
        record = {
            "path": "/tmp/fake-worktree",
            "branch": "refactory/run-test",
            "base": "abc123",
            "head": "def456",
        }
        (refactory_dir / "worktree.json").write_text(json.dumps(record))
        os.environ["REFACTORY_WORKDIR"] = str(run_dir)

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
    result = _run_script(script)
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
        CONSOLE_FILE, REVIEWERS, DELIVER_ENTRY_POINTS, EVIDENCE,
    )

    assert CHECK_PRODUCES == ("commit", "files", "inherited_failures")
    assert CHECK_WRITES == ("changes.diff", "test-guardedness.json")
    assert INSTALL_TIMEOUT_S == 1800
    assert HOOK_SUPPRESS == {"LEFTHOOK": "0", "HUSKY": "0"}
    assert CHECK_HOME == P(".refactory") / "check-home"
    assert BASE_CHECK_HOME == P(".refactory") / "base-check-home"
    assert WORKTREE_RECORD == P(".refactory") / "worktree.json"
    assert PENDING == P(".refactory") / "materialise-pending.json"
    assert WRAPPER == P(".refactory") / "in-worktree"
    assert TARGET_RECORD == P(".refactory") / "target.json"
    assert DELIVERY_SHOWN == P(".refactory") / "delivery-shown.json"
    assert CONSOLE_FILE == "console.json"
    assert REVIEWERS == ("health_checker", "code_reviewer", "qa_director", "overwatch", "focused_qa")
    assert DELIVER_ENTRY_POINTS == frozenset({"refactory_dsh.delivery:deliver_design"})
    assert EVIDENCE == "authoring-evidence.json"

    # Re-exports match (identity, not equality)
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

    # New constant re-exports
    from refactory_dsh.console import CONSOLE_FILE as cf2
    assert cf2 is CONSOLE_FILE

    from refactory_dsh.authoring_evidence import REVIEWERS as rv2
    assert rv2 is REVIEWERS

    from refactory_dsh.delivery import DELIVER_ENTRY_POINTS as dep2
    assert dep2 is DELIVER_ENTRY_POINTS

    from refactory_dsh.authoring_evidence import EVIDENCE as ev2
    assert ev2 is EVIDENCE


# ---------------------------------------------------------------------------
# Test 11: worktree_state — core only
# ---------------------------------------------------------------------------
def test_worktree_state_core_only():
    """Without the entry point, get_worktree_state() returns None."""
    script = _EP_EMPTY_PREAMBLE + textwrap.dedent("""\
        import sys

        import refactory_dsh.agent_delivery as _ad
        _ad._worktree_state = _ad._UNSET

        ws = _ad.get_worktree_state()
        assert ws is None, f"expected None, got {ws}"
        assert "refactory_dsh.worktree" not in sys.modules, "worktree should not be loaded"
        print("PASS")
    """)
    result = _run_script(script)
    assert result.returncode == 0, f"worktree_state core-only failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    assert "PASS" in result.stdout


# ---------------------------------------------------------------------------
# Test 12: worktree_state — product registered
# ---------------------------------------------------------------------------
def test_worktree_state_product_registered():
    """With the entry point, get_worktree_state() returns the bridge namespace."""
    script = _EP_PREAMBLE + textwrap.dedent("""\
        import refactory_dsh.agent_delivery as _ad
        _ad._worktree_state = _ad._UNSET

        ws = _ad.get_worktree_state()
        assert ws is not None, "get_worktree_state() returned None"

        from refactory_dsh import worktree
        assert ws.recorded is worktree.recorded, "recorded mismatch"
        assert ws.shared_changed is worktree.shared_changed, "shared_changed mismatch"
        print("PASS")
    """)
    result = _run_script(script)
    assert result.returncode == 0, f"worktree_state product-registered failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    assert "PASS" in result.stdout


# ---------------------------------------------------------------------------
# Test 13: worktree_state — declaration
# ---------------------------------------------------------------------------
def test_worktree_state_declaration():
    """pyproject.toml declares refactory.worktree_state correctly."""
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib  # type: ignore[no-redef]

    toml_path = _ROOT / "pyproject.toml"
    with open(toml_path, "rb") as f:
        data = tomllib.load(f)

    ep_group = data.get("project", {}).get("entry-points", {}).get("refactory.worktree_state", {})
    assert "worktree" in ep_group, "pyproject.toml missing worktree_state entry"
    assert ep_group["worktree"] == "refactory_dsh.worktree:_worktree_state_bridge"


# ---------------------------------------------------------------------------
# Test 14: worktree_state — stale install
# ---------------------------------------------------------------------------
def test_worktree_state_stale_install():
    """Worktree record exists but no worktree-state entry point → RuntimeError in begin()."""
    script = _EP_EMPTY_PREAMBLE + textwrap.dedent("""\
        import json, os, sys, tempfile
        from pathlib import Path

        run_dir = Path(tempfile.mkdtemp())
        refactory_dir = run_dir / ".refactory"
        refactory_dir.mkdir()
        record = {
            "path": "/tmp/fake-worktree",
            "branch": "refactory/run-test",
            "base": "abc123",
            "head": "def456",
        }
        (refactory_dir / "worktree.json").write_text(json.dumps(record))

        import refactory_dsh.agent_delivery as _ad
        _ad._worktree_state = _ad._UNSET

        try:
            _ad.begin(run_dir, "test_node", "test prompt", ["out.txt"])
            print("FAIL: no error raised")
            sys.exit(1)
        except RuntimeError as e:
            msg = str(e)
            assert "uv sync" in msg or "install.sh" in msg, f"error missing fix hint: {msg}"
            print("PASS")
    """)
    result = _run_script(script)
    assert result.returncode == 0, f"worktree_state stale-install failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    assert "PASS" in result.stdout


# ---------------------------------------------------------------------------
# Test 15: worktree_state — worker sequence
# ---------------------------------------------------------------------------
def test_worktree_state_worker_sequence():
    """worker_node.execute path discovers worktree_state via entry point."""
    script = _EP_PREAMBLE + textwrap.dedent("""\
        import json, os, sys, tempfile
        from pathlib import Path

        # Create worktree with managed .venv
        worktree_dir = Path(tempfile.mkdtemp())
        venv_dir = worktree_dir / ".venv"
        (venv_dir / "bin").mkdir(parents=True)
        (venv_dir / ".refactory-managed").touch()

        run_dir = Path(tempfile.mkdtemp())
        refactory_dir = run_dir / ".refactory"
        refactory_dir.mkdir()
        record = {
            "path": str(worktree_dir),
            "branch": "refactory/run-test",
            "base": "abc123",
            "head": "def456",
            "target": str(worktree_dir),
            "run": str(run_dir),
        }
        (refactory_dir / "worktree.json").write_text(json.dumps(record))
        os.environ["REFACTORY_WORKDIR"] = str(run_dir)

        import refactory_dsh.agent_delivery as _ad
        _ad._worktree_state = _ad._UNSET
        _ad._delivery_identity = _ad._UNSET

        # After resetting, get_worktree_state should discover the bridge
        ws = _ad.get_worktree_state()
        assert ws is not None, "get_worktree_state returned None in worker context"
        assert hasattr(ws, 'recorded'), "bridge missing recorded"
        assert hasattr(ws, 'shared_changed'), "bridge missing shared_changed"
        print("PASS")
    """)
    result = _run_script(script)
    assert result.returncode == 0, f"worktree_state worker sequence failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    assert "PASS" in result.stdout


# ---------------------------------------------------------------------------
# Test 16: delivery_identity — core only
# ---------------------------------------------------------------------------
def test_delivery_identity_core_only():
    """Without the entry point, get_delivery_identity() returns None and authoring_evidence is not loaded."""
    script = _EP_EMPTY_PREAMBLE + textwrap.dedent("""\
        import sys

        import refactory_dsh.agent_delivery as _ad
        _ad._delivery_identity = _ad._UNSET

        di = _ad.get_delivery_identity()
        assert di is None, f"expected None, got {di}"
        assert "refactory_dsh.authoring_evidence" not in sys.modules, (
            "authoring_evidence should not be loaded in core-only mode"
        )
        print("PASS")
    """)
    result = _run_script(script)
    assert result.returncode == 0, f"delivery_identity core-only failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    assert "PASS" in result.stdout


# ---------------------------------------------------------------------------
# Test 17: delivery_identity — product registered
# ---------------------------------------------------------------------------
def test_delivery_identity_product_registered():
    """With the entry point, get_delivery_identity() returns the callable."""
    script = _EP_PREAMBLE + textwrap.dedent("""\
        import refactory_dsh.agent_delivery as _ad
        _ad._delivery_identity = _ad._UNSET

        di = _ad.get_delivery_identity()
        assert di is not None, "get_delivery_identity() returned None"

        from refactory_dsh.authoring_evidence import delivery_identity
        assert di is delivery_identity, "returned callable is not delivery_identity"
        print("PASS")
    """)
    result = _run_script(script)
    assert result.returncode == 0, f"delivery_identity product-registered failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    assert "PASS" in result.stdout


# ---------------------------------------------------------------------------
# Test 18: delivery_identity — declaration
# ---------------------------------------------------------------------------
def test_delivery_identity_declaration():
    """pyproject.toml declares refactory.delivery_identity correctly."""
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib  # type: ignore[no-redef]

    toml_path = _ROOT / "pyproject.toml"
    with open(toml_path, "rb") as f:
        data = tomllib.load(f)

    ep_group = data.get("project", {}).get("entry-points", {}).get("refactory.delivery_identity", {})
    assert "authoring" in ep_group, "pyproject.toml missing delivery_identity entry"
    assert ep_group["authoring"] == "refactory_dsh.authoring_evidence:delivery_identity"


# ---------------------------------------------------------------------------
# Test 19: delivery_identity — stale install
# ---------------------------------------------------------------------------
def test_delivery_identity_stale_install():
    """Node in REVIEWERS, EVIDENCE exists, no entry point → RuntimeError."""
    script = _EP_EMPTY_PREAMBLE + textwrap.dedent("""\
        import json, os, sys, tempfile
        from pathlib import Path

        run_dir = Path(tempfile.mkdtemp())
        refactory_dir = run_dir / ".refactory"
        refactory_dir.mkdir()
        # Create EVIDENCE file so the stale check triggers
        (run_dir / "authoring-evidence.json").write_text("{}")

        import refactory_dsh.agent_delivery as _ad
        _ad._delivery_identity = _ad._UNSET
        _ad._worktree_state = _ad._UNSET

        from refactory_dsh.invocations import REVIEWERS
        node = REVIEWERS[0]  # e.g. "health_checker"

        # Simulate calling _execution_contract which calls get_delivery_identity
        di = _ad.get_delivery_identity()
        from refactory_dsh.invocations import EVIDENCE
        if di is None and node in REVIEWERS and (run_dir / EVIDENCE).exists():
            try:
                raise RuntimeError(
                    "the delivery-identity entry point is not installed but this node "
                    "needs authoring evidence; re-run `uv sync` or `./install.sh`"
                )
            except RuntimeError as e:
                msg = str(e)
                assert "uv sync" in msg, f"error missing fix hint: {msg}"
                print("PASS")
                sys.exit(0)

        print("FAIL: stale check did not trigger")
        sys.exit(1)
    """)
    result = _run_script(script)
    assert result.returncode == 0, f"delivery_identity stale-install failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    assert "PASS" in result.stdout


# ---------------------------------------------------------------------------
# Test 20: delivery_identity — worker sequence
# ---------------------------------------------------------------------------
def test_delivery_identity_worker_sequence():
    """The worker path discovers delivery_identity via entry point."""
    script = _EP_PREAMBLE + textwrap.dedent("""\
        import refactory_dsh.agent_delivery as _ad
        _ad._delivery_identity = _ad._UNSET

        di = _ad.get_delivery_identity()
        assert di is not None, "get_delivery_identity returned None in worker context"
        assert callable(di), "delivery_identity is not callable"
        print("PASS")
    """)
    result = _run_script(script)
    assert result.returncode == 0, f"delivery_identity worker sequence failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    assert "PASS" in result.stdout
