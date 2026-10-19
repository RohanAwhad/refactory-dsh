"""Tests for the machinery pinning system.

Each test creates a temporary git repository as the "driver checkout" and a
temporary ``~/.refactory`` (isolated by conftest's ``REFACTORY_HOME`` env var).
Each test fails without the pin implementation.

The ``use_real_pin`` marker opts these tests back in to real pinning —
conftest's autouse ``_no_pin`` fixture is skipped so ``pin.ensure_pinned``
creates real git worktrees.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

# All tests in this module need real pinning, not the noop fixture.
pytestmark = pytest.mark.use_real_pin


def _git(args: list[str], cwd: Path, **kwargs) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, **kwargs,
    )


def _make_driver(tmp_path: Path, marker: str = "A") -> Path:
    """Create a minimal git repo that looks like a driver checkout.

    The repo has a pyproject.toml and a refactory_dsh package with a marker.
    """
    driver = tmp_path / "driver"
    driver.mkdir(parents=True)
    _git(["init"], cwd=driver)
    _git(["config", "user.email", "test@test.com"], cwd=driver)
    _git(["config", "user.name", "Test"], cwd=driver)

    # Create a minimal pyproject.toml
    (driver / "pyproject.toml").write_text(
        '[project]\nname = "test-driver"\nversion = "0.1.0"\nrequires-python = ">=3.12"\n'
    )

    # Create a refactory/refactory_dsh package with a marker
    pkg = driver / "refactory" / "refactory_dsh"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "__marker__.py").write_text(f"MARKER = {marker!r}\n")

    # Create packages/bundle directory structure
    bundle = driver / "packages" / "bundle" / "graph"
    bundle.mkdir(parents=True)
    (bundle / "package.json").write_text('{"name": "@deepseek-ai/dsh-graph"}')

    _git(["add", "-A"], cwd=driver)
    _git(["commit", "-m", f"commit {marker}"], cwd=driver)

    return driver


def _commit_marker(driver: Path, marker: str) -> str:
    """Update the marker and commit, returning the new SHA."""
    pkg = driver / "refactory" / "refactory_dsh"
    (pkg / "__marker__.py").write_text(f"MARKER = {marker!r}\n")
    _git(["add", "-A"], cwd=driver)
    _git(["commit", "-m", f"commit {marker}"], cwd=driver)
    result = _git(["rev-parse", "HEAD"], cwd=driver)
    return result.stdout.strip()


def _head_sha(driver: Path) -> str:
    return _git(["rev-parse", "HEAD"], cwd=driver).stdout.strip()


def _make_executable_driver(base: Path, marker: str = "A") -> Path:
    """Create a git repo where ``python -m refactory_dsh.interactive`` prints the marker.

    Unlike :func:`_make_driver`, this driver's ``interactive.py`` is a stub
    that imports and prints the marker value, so a pinned interpreter can run
    it as a subprocess without any third-party dependencies.
    """
    driver = base / "driver"
    driver.mkdir(parents=True)
    _git(["init"], cwd=driver)
    _git(["config", "user.email", "test@test.com"], cwd=driver)
    _git(["config", "user.name", "Test"], cwd=driver)

    pkg = driver / "refactory" / "refactory_dsh"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "__marker__.py").write_text(f"MARKER = {marker!r}\n")
    # A stub interactive module: when run with ``python -m``, it prints the
    # marker and exits, regardless of extra CLI arguments.
    (pkg / "interactive.py").write_text(
        "from refactory_dsh.__marker__ import MARKER\n"
        "print(f'MARKER={MARKER}')\n"
    )

    _git(["add", "-A"], cwd=driver)
    _git(["commit", "-m", f"commit {marker}"], cwd=driver)
    return driver


def _pin_with_lightweight_venv(
    driver: Path, key: str, home: Path,
) -> "pin.PinnedCheckout":
    """Pin *key* with ``python -m venv`` and a ``.pth`` file (no network).

    The correction requires the decisive test to need no network.  This
    helper creates the git worktree and a standard-library venv, then makes
    ``refactory_dsh`` importable through a ``.pth`` file pointing at the
    checkout's ``refactory/`` directory.
    """
    from refactory_dsh import pin

    pinned_dir = home / "pinned" / key
    checkout_dest = pinned_dir / "checkout"
    pinned_dir.mkdir(parents=True, exist_ok=True)

    if not checkout_dest.exists():
        result = _git(
            ["worktree", "add", "--detach", str(checkout_dest), key], cwd=driver,
        )
        if result.returncode != 0:
            raise RuntimeError(f"git worktree add failed: {result.stderr}")

        # Lightweight venv — no uv, no network
        subprocess.run(
            [sys.executable, "-m", "venv", str(checkout_dest / ".venv")],
            check=True, capture_output=True,
        )
        # Make refactory_dsh importable from the pinned checkout
        site_packages = next(
            (checkout_dest / ".venv" / "lib").glob("python*/site-packages")
        )
        (site_packages / "driver.pth").write_text(
            str(checkout_dest / "refactory") + "\n"
        )

        (pinned_dir / "pin.json").write_text(json.dumps({
            "key": key,
            "source_repo": str(driver.resolve()),
            "dirty": False,
            "diff_hash": "",
            "runs": [],
        }))
        (pinned_dir / ".pin-ready").write_text("2")

    dsh_home = pinned_dir / "dsh_home"
    dsh_home.mkdir(exist_ok=True)

    return pin.PinnedCheckout(
        key=key,
        checkout_path=checkout_dest,
        dsh_home=dsh_home,
        interpreter=str(checkout_dest / ".venv" / "bin" / "python"),
        dirty=False,
        diff_hash="",
        untracked_included=False,
    )


class TestResumeAfterDriverMoves:
    """The decisive test: pin at A, move to B, execute A's pinned code."""

    def test_owner_executes_pinned_code(self, tmp_path: Path) -> None:
        """Start the resumed owner through _build_owner_argv and _start_child.

        Pin at commit A, move the driver to commit B, then start a child
        process under the pinned A interpreter through the real supervisor
        path.  The child prints ``MARKER=A`` (from the pinned checkout's
        code), proving pinned machinery is actually executed.  A new pin at
        B prints ``MARKER=B``.
        """
        from refactory_dsh import pin
        from refactory_dsh.supervisor import _build_owner_argv, _start_child

        driver = _make_executable_driver(tmp_path, "A")
        sha_a = _head_sha(driver)
        home = tmp_path / "rfhome"
        pinned_a = _pin_with_lightweight_venv(driver, sha_a, home)

        # Move the driver to commit B
        sha_b = _commit_marker(driver, "B")

        # --- resumed run at A ---
        root_a = tmp_path / "run_a"
        (root_a / ".refactory").mkdir(parents=True)
        (root_a / ".refactory" / "run-config.json").write_text(json.dumps({
            "mode": "test/marker",
            "project": str(tmp_path),
            "pinned_interpreter": pinned_a.interpreter,
            "pinned_dsh_home": str(pinned_a.dsh_home),
            "pinned_key": pinned_a.key,
        }))

        argv_a, _, _ = _build_owner_argv(root_a)
        assert argv_a[0] == pinned_a.interpreter

        child_a = _start_child(root_a)
        child_a.wait(timeout=30)
        log_a = (root_a / "recovery.log").read_text()
        assert "MARKER=A" in log_a, f"expected MARKER=A, got: {log_a!r}"

        # --- new run at B ---
        pinned_b = _pin_with_lightweight_venv(driver, sha_b, home)
        root_b = tmp_path / "run_b"
        (root_b / ".refactory").mkdir(parents=True)
        (root_b / ".refactory" / "run-config.json").write_text(json.dumps({
            "mode": "test/marker",
            "project": str(tmp_path),
            "pinned_interpreter": pinned_b.interpreter,
            "pinned_dsh_home": str(pinned_b.dsh_home),
            "pinned_key": pinned_b.key,
        }))

        child_b = _start_child(root_b)
        child_b.wait(timeout=30)
        log_b = (root_b / "recovery.log").read_text()
        assert "MARKER=B" in log_b, f"expected MARKER=B, got: {log_b!r}"

    def test_configuration_fingerprints_stable(self, tmp_path: Path) -> None:
        """Fingerprints computed on the pinned checkout are stable after driver moves."""
        from refactory_dsh import pin

        driver = _make_driver(tmp_path, marker="A")

        pinned_a = pin.ensure_pinned(driver, refactory_home=tmp_path / "rfhome")
        marker_file = pinned_a.checkout_path / "refactory" / "refactory_dsh" / "__marker__.py"
        content_a = marker_file.read_text()

        # Move driver to B
        _commit_marker(driver, "B")

        # Pinned checkout's marker is still A's content
        assert marker_file.read_text() == content_a


class TestSharedPinnedCopy:
    """Two runs at one commit share one pinned checkout."""

    def test_same_key_same_directory(self, tmp_path: Path) -> None:
        from refactory_dsh import pin

        driver = _make_driver(tmp_path, marker="X")
        home = tmp_path / "rfhome"

        pinned1 = pin.ensure_pinned(driver, refactory_home=home)
        pinned2 = pin.ensure_pinned(driver, refactory_home=home)

        assert pinned1.key == pinned2.key
        assert pinned1.checkout_path == pinned2.checkout_path

        # Only one directory under pinned/
        pinned_dirs = list((home / "pinned").iterdir())
        key_dirs = [d for d in pinned_dirs if d.is_dir() and (d / ".pin-ready").exists()]
        assert len(key_dirs) == 1

    def test_register_both_runs(self, tmp_path: Path) -> None:
        from refactory_dsh import pin

        driver = _make_driver(tmp_path, marker="X")
        home = tmp_path / "rfhome"

        pinned = pin.ensure_pinned(driver, refactory_home=home)

        run1 = tmp_path / "run1"
        run2 = tmp_path / "run2"
        run1.mkdir()
        run2.mkdir()

        pin.register_run(pinned.key, run1, refactory_home=home)
        pin.register_run(pinned.key, run2, refactory_home=home)

        pin_json = json.loads((home / "pinned" / pinned.key / "pin.json").read_text())
        assert str(run1.resolve()) in pin_json["runs"]
        assert str(run2.resolve()) in pin_json["runs"]


class TestDirtyDriverPins:
    """A dirty driver (uncommitted changes) is pinned with its diff recorded."""

    def test_dirty_pin_created(self, tmp_path: Path) -> None:
        from refactory_dsh import pin

        driver = _make_driver(tmp_path, marker="C")

        # Make driver dirty
        (driver / "refactory" / "refactory_dsh" / "__marker__.py").write_text(
            "MARKER = 'C-dirty'\n"
        )

        pinned = pin.ensure_pinned(driver, refactory_home=tmp_path / "rfhome")
        assert pinned.dirty is True
        assert pinned.diff_hash != ""

        # The driver's refs and index are unchanged
        status = _git(["status", "--porcelain"], cwd=driver)
        assert status.stdout.strip()  # still dirty

        # HEAD hasn't moved
        log = _git(["log", "--oneline", "-1"], cwd=driver)
        assert "commit C" in log.stdout

    def test_dirty_diff_hash_nonempty(self, tmp_path: Path) -> None:
        from refactory_dsh import pin

        driver = _make_driver(tmp_path, marker="D")
        (driver / "newfile.txt").write_text("untracked content")

        pinned = pin.ensure_pinned(driver, refactory_home=tmp_path / "rfhome")
        assert pinned.dirty is True
        assert len(pinned.diff_hash) == 64  # SHA-256 hex digest


class TestCleanupKeepsReferencedRemovesUnreferenced:
    """Cleanup keeps referenced pins and removes unreferenced ones."""

    def test_cleanup_behavior(self, tmp_path: Path) -> None:
        from refactory_dsh import pin

        home = tmp_path / "rfhome"

        # Create three drivers with different commits
        driver1 = _make_driver(tmp_path / "d1", marker="E1")
        driver2 = _make_driver(tmp_path / "d2", marker="E2")
        driver3 = _make_driver(tmp_path / "d3", marker="E3")

        pinned1 = pin.ensure_pinned(driver1, refactory_home=home)
        pinned2 = pin.ensure_pinned(driver2, refactory_home=home)
        pinned3 = pin.ensure_pinned(driver3, refactory_home=home)

        # Create run directories
        run1 = tmp_path / "run1"
        run2 = tmp_path / "run2"
        run3 = tmp_path / "run3"
        run1.mkdir()
        run2.mkdir()
        run3.mkdir()

        pin.register_run(pinned1.key, run1, refactory_home=home)
        pin.register_run(pinned2.key, run2, refactory_home=home)
        pin.register_run(pinned3.key, run3, refactory_home=home)

        # Make run1 non-terminal (paused — resumable)
        (run1 / ".refactory").mkdir(parents=True)
        (run1 / ".refactory" / "spine-status.json").write_text(
            json.dumps({"status": "paused"})
        )

        # Make run2 terminal (done — not resumable)
        (run2 / ".refactory").mkdir(parents=True)
        (run2 / ".refactory" / "spine-status.json").write_text(
            json.dumps({"status": "done"})
        )

        # Make run3 failed (resumable with --acknowledge-failure)
        (run3 / ".refactory").mkdir(parents=True)
        (run3 / ".refactory" / "spine-status.json").write_text(
            json.dumps({"status": "failed"})
        )

        result = pin.cleanup(refactory_home=home)

        assert pinned1.key in result["kept"]
        assert pinned2.key in result["removed"]
        # failed is resumable via --acknowledge-failure — cleanup keeps it
        assert pinned3.key in result["kept"]
        assert (home / "pinned" / pinned1.key).is_dir()
        assert not (home / "pinned" / pinned2.key).exists()
        assert (home / "pinned" / pinned3.key).is_dir()


class TestPinnedCheckoutComparison:
    """The #380 check compares a run with its pinned machinery."""

    def test_pinned_metadata_in_run_config(self, tmp_path: Path) -> None:
        from refactory_dsh import pin

        driver = _make_driver(tmp_path, marker="F")
        home = tmp_path / "rfhome"

        pinned = pin.ensure_pinned(driver, refactory_home=home)

        # Simulate writing run-config.json with pinned fields
        run_dir = tmp_path / "run_f"
        (run_dir / ".refactory").mkdir(parents=True)
        config = {
            "mode": "test/mode",
            "pinned_key": pinned.key,
            "pinned_interpreter": pinned.interpreter,
            "pinned_checkout": str(pinned.checkout_path),
            "pinned_dsh_home": str(pinned.dsh_home),
            "pinned_dirty": pinned.dirty,
            "pinned_diff_hash": pinned.diff_hash,
        }
        (run_dir / ".refactory" / "run-config.json").write_text(json.dumps(config))

        # read_pinned reads back the same values
        read_back = pin.read_pinned(run_dir)
        assert read_back is not None
        assert read_back.key == pinned.key
        assert read_back.interpreter == pinned.interpreter
        assert str(read_back.checkout_path) == str(pinned.checkout_path)
        assert str(read_back.dsh_home) == str(pinned.dsh_home)


class TestDshHomeIsolated:
    """The pin never writes to the machine's actual ~/.dsh."""

    def test_no_writes_to_machine_dsh(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from refactory_dsh import pin

        # Isolate HOME and DSH_HOME
        fake_home = tmp_path / "fakehome"
        fake_home.mkdir()
        fake_dsh = fake_home / ".dsh"
        fake_dsh.mkdir()

        # Snapshot the state of fake_dsh before
        before = set()
        for p in fake_dsh.rglob("*"):
            before.add(str(p.relative_to(fake_dsh)))

        monkeypatch.setenv("HOME", str(fake_home))
        monkeypatch.setenv("DSH_HOME", str(fake_dsh))

        driver = _make_driver(tmp_path, marker="G")
        rfhome = tmp_path / "rfhome"

        pin.ensure_pinned(driver, refactory_home=rfhome)

        # Check that fake_dsh has not changed
        after = set()
        for p in fake_dsh.rglob("*"):
            after.add(str(p.relative_to(fake_dsh)))

        assert after == before, f"Files were written to DSH_HOME: {after - before}"
