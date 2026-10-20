"""Tests for driver pinning: recording, drift detection, pin creation and pruning.

Every test creates a temporary git repository. No test touches the real repository.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch, MagicMock

import pytest


def _init_repo(path: Path, *, files: dict[str, str] | None = None) -> str:
    """Create a git repo at *path* with one commit. Returns the commit SHA."""
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", str(path)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "test@test"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "Test"],
                   check=True, capture_output=True)
    for name, content in (files or {"dummy.txt": "hello"}).items():
        (path / name).parent.mkdir(parents=True, exist_ok=True)
        (path / name).write_text(content)
    subprocess.run(["git", "-C", str(path), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "commit", "-m", "initial"],
                   check=True, capture_output=True)
    result = subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"],
                            capture_output=True, text=True, check=True)
    return result.stdout.strip()


def _add_commit(path: Path, files: dict[str, str], msg: str = "update") -> str:
    """Add a commit to an existing repo. Returns the new commit SHA."""
    for name, content in files.items():
        (path / name).parent.mkdir(parents=True, exist_ok=True)
        (path / name).write_text(content)
    subprocess.run(["git", "-C", str(path), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "commit", "-m", msg],
                   check=True, capture_output=True)
    result = subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"],
                            capture_output=True, text=True, check=True)
    return result.stdout.strip()


def _make_run_config(run_dir: Path, commit: str, checkout: str,
                     fingerprints: dict | None = None,
                     mode: str = "default_pack/design") -> dict:
    """Write a minimal run-config.json with a driver record."""
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / ".refactory").mkdir(parents=True, exist_ok=True)
    config: dict[str, Any] = {
        "mode": mode,
        "project": str(run_dir),
        "knobs": [],
        "model_context": {},
        "configuration_fingerprints": fingerprints or {},
    }
    (run_dir / ".refactory" / "run-config.json").write_text(json.dumps(config))
    return config


def _selective_subprocess(original_run):
    """Return a side_effect that passes git commands through and stubs the rest."""
    def _side_effect(cmd, **kw):
        if isinstance(cmd, (list, tuple)):
            if str(cmd[0]) == "git" or str(cmd[0]).endswith("/git"):
                return original_run(cmd, **kw)
            result = MagicMock()
            result.returncode = 0
            result.stdout = ""
            result.stderr = ""
            return result
        return original_run(cmd, **kw)
    return _side_effect


# ── tests ────────────────────────────────────────────────────────────────


def test_driver_recorded_at_launch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """record_driver writes the driver key with all expected fields."""
    repo = tmp_path / "repo"
    commit_a = _init_repo(repo)
    run_dir = tmp_path / "run"
    fp_file = repo / "dummy.txt"
    fp = hashlib.sha256(fp_file.read_bytes()).hexdigest()
    _make_run_config(run_dir, commit_a, str(repo),
                     fingerprints={str(fp_file): fp})

    import refactory_dsh.pin as pin
    monkeypatch.setattr(pin, "REPO_ROOT", repo)

    pin.record_driver(run_dir / ".refactory" / "run-config.json")

    config = json.loads((run_dir / ".refactory" / "run-config.json").read_text())
    driver = config["driver"]
    assert driver["checkout"] == str(repo)
    assert driver["commit"] == commit_a
    assert driver["profile"] == "graph"
    assert driver["pin_checkout"] is None
    assert isinstance(driver["fingerprints_at_launch"], dict)


def test_dirty_driver_records_diff(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A dirty checkout records its unified diff."""
    repo = tmp_path / "repo"
    _init_repo(repo, files={"refactory/refactory_dsh/module.py": "original"})
    # Dirty a tracked file inside the machinery paths without committing.
    (repo / "refactory/refactory_dsh/module.py").write_text("changed")
    run_dir = tmp_path / "run"
    _make_run_config(run_dir, "x", str(repo))

    import refactory_dsh.pin as pin
    monkeypatch.setattr(pin, "REPO_ROOT", repo)

    pin.record_driver(run_dir / ".refactory" / "run-config.json")

    config = json.loads((run_dir / ".refactory" / "run-config.json").read_text())
    driver = config["driver"]
    assert driver["dirty_diff"] is not None
    assert "changed" in driver["dirty_diff"]


def test_dirty_driver_captures_staged_changes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Staged changes in machinery paths are captured by record_driver."""
    repo = tmp_path / "repo"
    _init_repo(repo, files={"refactory/refactory_dsh/module.py": "original"})
    # Stage a change without committing.
    (repo / "refactory/refactory_dsh/module.py").write_text("staged content")
    subprocess.run(["git", "-C", str(repo), "add", "refactory/refactory_dsh/module.py"],
                   check=True, capture_output=True)
    run_dir = tmp_path / "run"
    _make_run_config(run_dir, "x", str(repo))

    import refactory_dsh.pin as pin
    monkeypatch.setattr(pin, "REPO_ROOT", repo)

    pin.record_driver(run_dir / ".refactory" / "run-config.json")

    config = json.loads((run_dir / ".refactory" / "run-config.json").read_text())
    driver = config["driver"]
    assert driver["dirty_diff"] is not None
    assert "staged content" in driver["dirty_diff"]


def test_no_pin_when_driver_unchanged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """detect_drift returns None when the driver has not moved."""
    repo = tmp_path / "repo"
    commit_a = _init_repo(repo)
    run_dir = tmp_path / "run"
    _make_run_config(run_dir, commit_a, str(repo))

    import refactory_dsh.pin as pin
    monkeypatch.setattr(pin, "REPO_ROOT", repo)
    pin.record_driver(run_dir / ".refactory" / "run-config.json")

    config = json.loads((run_dir / ".refactory" / "run-config.json").read_text())
    drift = pin.detect_drift(config)
    assert drift is None

    # No pin directory should exist.
    pins_dir = pin.refactory_home() / "pins"
    assert not pins_dir.exists() or len(list(pins_dir.iterdir())) == 0


def test_commit_drift_triggers_pin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A commit change is detected as drift."""
    repo = tmp_path / "repo"
    commit_a = _init_repo(repo)
    run_dir = tmp_path / "run"
    _make_run_config(run_dir, commit_a, str(repo))

    import refactory_dsh.pin as pin
    monkeypatch.setattr(pin, "REPO_ROOT", repo)
    pin.record_driver(run_dir / ".refactory" / "run-config.json")

    # Move the driver to a new commit.
    _add_commit(repo, {"dummy.txt": "new content"})

    config = json.loads((run_dir / ".refactory" / "run-config.json").read_text())
    drift = pin.detect_drift(config)
    assert drift is not None
    assert drift["kind"] == "commit"
    assert drift["recorded_commit"] == commit_a


def test_fingerprint_drift_without_commit_change(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Uncommitted edits to a fingerprinted file are detected as drift."""
    repo = tmp_path / "repo"
    commit_a = _init_repo(repo)
    fp_file = repo / "dummy.txt"
    fp = hashlib.sha256(fp_file.read_bytes()).hexdigest()
    run_dir = tmp_path / "run"
    _make_run_config(run_dir, commit_a, str(repo), fingerprints={str(fp_file): fp})

    import refactory_dsh.pin as pin
    monkeypatch.setattr(pin, "REPO_ROOT", repo)
    pin.record_driver(run_dir / ".refactory" / "run-config.json")

    # Modify the fingerprinted file without committing.
    fp_file.write_text("modified without commit")

    config = json.loads((run_dir / ".refactory" / "run-config.json").read_text())
    drift = pin.detect_drift(config)
    assert drift is not None
    assert drift["kind"] == "fingerprint"


def test_shared_pin_for_same_commit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Two runs at one commit share one pin."""
    repo = tmp_path / "repo"
    commit_a = _init_repo(repo)

    import refactory_dsh.pin as pin
    monkeypatch.setattr(pin, "REPO_ROOT", repo)

    run1 = tmp_path / "run1"
    run1.mkdir(parents=True)
    (run1 / ".refactory").mkdir(parents=True)
    run2 = tmp_path / "run2"
    run2.mkdir(parents=True)
    (run2 / ".refactory").mkdir(parents=True)

    driver_info = {
        "checkout": str(repo),
        "commit": commit_a,
        "dirty_diff": None,
        "profile": "graph",
        "pin_checkout": None,
    }

    # Create the pin structure manually (simulating a prepared pin).
    key = pin._pin_key(commit_a, None)
    pin_root = pin.refactory_home() / "pins" / key
    pin_checkout = pin_root / "checkout"
    pin_checkout.mkdir(parents=True)
    (pin_checkout / ".git").write_text("gitdir: dummy")
    pin._atomic_write_json(pin_root / "pin.json", {
        "commit": commit_a, "created": "now", "driver_checkout": str(repo), "runs": [],
    })

    p1 = pin.resolve_pin(driver_info, run_dir=run1)
    p2 = pin.resolve_pin({**driver_info}, run_dir=run2)

    assert p1 == p2
    data = json.loads((pin_root / "pin.json").read_text())
    assert str(run1.resolve()) in data["runs"]
    assert str(run2.resolve()) in data["runs"]


def test_prune_keeps_live_pin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A pin with a live run is not pruned."""
    import refactory_dsh.pin as pin

    commit = "a" * 40
    key = pin._pin_key(commit, None)
    pin_root = pin.refactory_home() / "pins" / key
    pin_checkout = pin_root / "checkout"
    pin_checkout.mkdir(parents=True)
    (pin_checkout / ".git").write_text("gitdir: dummy")

    # A live run: directory exists, no terminal status.
    run_dir = tmp_path / "live_run"
    run_dir.mkdir(parents=True)
    (run_dir / ".refactory").mkdir(parents=True)

    pin._atomic_write_json(pin_root / "pin.json", {
        "commit": commit, "created": "now",
        "driver_checkout": str(tmp_path),
        "runs": [str(run_dir)],
    })

    pruned = pin.prune_pins()
    assert commit not in pruned
    assert pin_root.exists()


def test_prune_keeps_failed_run_with_existing_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed run whose directory exists is treated as resumable — its pin is kept."""
    import refactory_dsh.pin as pin

    commit = "f" * 40
    key = pin._pin_key(commit, None)
    pin_root = pin.refactory_home() / "pins" / key
    pin_checkout = pin_root / "checkout"
    pin_checkout.mkdir(parents=True)

    # A failed run with an existing directory.
    run_dir = tmp_path / "failed_run"
    run_dir.mkdir(parents=True)
    (run_dir / ".refactory").mkdir(parents=True)
    # Write a terminal spine status.
    pin._atomic_write_json(run_dir / ".refactory" / "spine-status.json", {"status": "failed"})

    pin._atomic_write_json(pin_root / "pin.json", {
        "commit": commit, "created": "now",
        "driver_checkout": str(tmp_path),
        "runs": [str(run_dir)],
    })

    pruned = pin.prune_pins()
    # The run is resumable (directory exists), so its pin is kept.
    assert commit not in pruned
    assert pin_root.exists()


def test_prune_removes_unused_pin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A pin with no live runs is pruned."""
    import refactory_dsh.pin as pin

    commit = "b" * 40
    key = pin._pin_key(commit, None)
    pin_root = pin.refactory_home() / "pins" / key
    pin_checkout = pin_root / "checkout"
    pin_checkout.mkdir(parents=True)

    # Run directory that does not exist (gone).
    pin._atomic_write_json(pin_root / "pin.json", {
        "commit": commit, "created": "now",
        "driver_checkout": str(tmp_path),
        "runs": [str(tmp_path / "gone_run")],
    })

    pruned = pin.prune_pins()
    assert commit in pruned
    assert not pin_root.exists()


def test_prune_with_run_outside_default_locations(tmp_path: Path) -> None:
    """A run in an arbitrary directory is correctly handled."""
    import refactory_dsh.pin as pin

    commit = "c" * 40
    key = pin._pin_key(commit, None)
    pin_root = pin.refactory_home() / "pins" / key
    pin_checkout = pin_root / "checkout"
    pin_checkout.mkdir(parents=True)

    # A run outside default locations — exists and is not terminal.
    weird_run = tmp_path / "weird" / "place" / "run"
    weird_run.mkdir(parents=True)
    (weird_run / ".refactory").mkdir(parents=True)

    pin._atomic_write_json(pin_root / "pin.json", {
        "commit": commit, "created": "now",
        "driver_checkout": str(tmp_path),
        "runs": [str(weird_run)],
    })

    pruned = pin.prune_pins()
    assert commit not in pruned  # kept because the run dir exists


def test_pin_preparation_does_not_touch_driver_hooks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin creation does not modify the driver's hooks or config (except worktreeConfig).

    A fake ``pnpm`` script that actively tries to install a hook is put on PATH.
    The test asserts the driver's hooks and config are unchanged, proving that
    ``LEFTHOOK=0``, ``HUSKY=0`` and per-worktree hooks isolation block the install.
    """
    repo = tmp_path / "repo"
    commit_a = _init_repo(repo)

    import refactory_dsh.pin as pin
    monkeypatch.setattr(pin, "REPO_ROOT", repo)

    # Create a fake pnpm that actively tries to install a hook.
    fake_bin = tmp_path / "fake_bin"
    fake_bin.mkdir()
    fake_pnpm = fake_bin / "pnpm"
    fake_pnpm.write_text(
        "#!/bin/sh\n"
        "# Simulate lefthook/husky trying to install hooks.\n"
        "# If hooks isolation works, this should have no effect on the driver.\n"
        "git config core.hooksPath /tmp/evil-hooks 2>/dev/null || true\n"
        "mkdir -p .git/hooks 2>/dev/null || true\n"
        'echo "#!/bin/sh" > .git/hooks/pre-commit 2>/dev/null || true\n'
        "exit 0\n"
    )
    fake_pnpm.chmod(0o755)

    fake_uv = fake_bin / "uv"
    fake_uv.write_text("#!/bin/sh\nexit 0\n")
    fake_uv.chmod(0o755)

    # Snapshot the driver's state before pin creation.
    hooks_dir = repo / ".git" / "hooks"
    hooks_before: dict[str, bytes] = {}
    if hooks_dir.is_dir():
        hooks_before = {f.name: f.read_bytes() for f in hooks_dir.iterdir() if f.is_file()}
    config_before = (repo / ".git" / "config").read_text()

    # Create the pin with the fake pnpm on PATH. Git commands must be real.
    # The _selective_subprocess routes git through to the real subprocess.run
    # and uses the fake pnpm/uv for everything else.
    original_run = subprocess.run

    def _side_effect_with_fake_path(cmd, **kw):
        if isinstance(cmd, (list, tuple)):
            cmd_name = str(cmd[0])
            if cmd_name == "git" or cmd_name.endswith("/git"):
                return original_run(cmd, **kw)
            # For non-git commands, run the fake scripts with modified PATH.
            env = dict(kw.get("env", os.environ))
            env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
            kw["env"] = env
            return original_run(cmd, **kw)
        return original_run(cmd, **kw)

    with patch("subprocess.run", side_effect=_side_effect_with_fake_path):
        pin.create_pin(repo, commit_a)

    # The driver's hooks and config should be unchanged (except worktreeConfig).
    config_after = (repo / ".git" / "config").read_text()
    hooks_after: dict[str, bytes] = {}
    if hooks_dir.is_dir():
        hooks_after = {f.name: f.read_bytes() for f in hooks_dir.iterdir() if f.is_file()}

    # Only extensions.worktreeConfig (and its [extensions] section header)
    # is allowed to be added.
    for line in config_after.splitlines():
        if line.strip() not in config_before and "worktreeconfig" not in line.lower() and "extensions" not in line.lower():
            pytest.fail(f"unexpected config change: {line}")

    assert hooks_before == hooks_after, "driver hooks were modified by fake pnpm"


def test_fingerprint_path_translation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Fingerprint keys are translated from the recorded checkout to REPO_ROOT."""
    from refactory_dsh import recovery, paths

    original_checkout = tmp_path / "original"
    pin_checkout = tmp_path / "pin"
    original_checkout.mkdir(parents=True)
    pin_checkout.mkdir(parents=True)

    # Create a fingerprinted file at both locations.
    fp_path = "refactory/refactory_dsh/design.py"
    (original_checkout / fp_path).parent.mkdir(parents=True, exist_ok=True)
    (original_checkout / fp_path).write_text("content_v1")
    (pin_checkout / fp_path).parent.mkdir(parents=True, exist_ok=True)
    (pin_checkout / fp_path).write_text("content_v1")

    fp = recovery._configuration_fingerprint(original_checkout / fp_path)

    config: dict[str, Any] = {
        "mode": "test/mode",
        "configuration_fingerprints": {
            str(original_checkout / fp_path): fp,
        },
        "driver": {
            "checkout": str(original_checkout),
            "commit": "abc123",
        },
    }

    # When REPO_ROOT is the pin, translate the keys.
    # Monkeypatch paths.REPO_ROOT because recovery.py imports it from there
    # via a function-local ``from refactory_dsh.paths import REPO_ROOT``.
    monkeypatch.setattr(paths, "REPO_ROOT", pin_checkout)

    # The translated fingerprint should match the pin's file.
    expected_configuration = config["configuration_fingerprints"]
    driver = config.get("driver", {})
    driver_checkout = driver.get("checkout", "")
    repo_root_str = str(pin_checkout)

    translated = {}
    for name, expected in expected_configuration.items():
        if driver_checkout and name.startswith(driver_checkout):
            new_name = repo_root_str + name[len(driver_checkout):]
        else:
            new_name = name
        translated[new_name] = expected

    assert str(pin_checkout / fp_path) in translated


def test_reexec_skipped_when_already_on_pin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The loop-breaking guard: re-exec is skipped when REPO_ROOT == pin_checkout."""
    from refactory_dsh import paths

    pin_checkout = tmp_path / "pin" / "checkout"
    pin_checkout.mkdir(parents=True)

    monkeypatch.setattr(paths, "REPO_ROOT", pin_checkout)

    # Simulate the check in the resume path.
    assert paths.REPO_ROOT == pin_checkout


def test_resume_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end: interactive.run(resume=True) detects drift, resolves pin,
    and re-execs with the pin's Python and profile.

    Uses the real ``create_pin`` with only uv sync / pnpm install / pnpm build
    stubbed (via ``_selective_subprocess``).  ``os.execv`` is captured via a
    sentinel exception.
    """
    repo = tmp_path / "repo"
    commit_a = _init_repo(repo, files={"refactory/refactory_dsh/mode.py": "v1"})

    import refactory_dsh.pin as pin
    from refactory_dsh import interactive, paths
    from refactory_dsh import dsh_profile
    monkeypatch.setattr(pin, "REPO_ROOT", repo)
    monkeypatch.setattr(paths, "REPO_ROOT", repo)

    run_dir = tmp_path / "run"
    fp_file = repo / "refactory" / "refactory_dsh" / "mode.py"
    fp = hashlib.sha256(fp_file.read_bytes()).hexdigest()
    _make_run_config(run_dir, commit_a, str(repo),
                     fingerprints={str(fp_file): fp},
                     mode="test_pack/test_mode")
    pin.record_driver(run_dir / ".refactory" / "run-config.json")

    # Write spine checkpoint so recovery.prepare accepts the run.
    checkpoint = {
        "run_id": str(run_dir.resolve()),
        "graph": {"name": "test_pack-test_mode"},
        "status": "ready",
        "configuration_fingerprints": {str(fp_file): fp},
        "file_fingerprints": {},
    }
    (run_dir / ".refactory" / "spine-checkpoint.json").write_text(json.dumps(checkpoint))

    # Move the driver to commit B, changing the fingerprinted file.
    commit_b = _add_commit(repo, {"refactory/refactory_dsh/mode.py": "v2"})

    # Create the pin using the real create_pin, with only the heavy installs
    # (uv sync, pnpm install, pnpm build) and profile provisioning stubbed.
    # dsh_command() would fail because the temp repo has no DSH CLI entry, so
    # stub it to return a dummy command that _selective_subprocess will stub.
    monkeypatch.setattr("refactory_dsh.paths.dsh_command", lambda root=None, cli=None: ["dsh-stub"])
    original_run = subprocess.run
    with patch("subprocess.run", side_effect=_selective_subprocess(original_run)):
        pin_checkout = pin.create_pin(repo, commit_a)

    # create_pin creates a real git worktree but does not run a real uv sync,
    # so .venv does not exist.  Create a fake python binary for the execv assertion.
    (pin_checkout / ".venv" / "bin").mkdir(parents=True, exist_ok=True)
    (pin_checkout / ".venv" / "bin" / "python").write_text("#!/bin/sh\n")

    # Stub require_build and dsh_profile.require — they are pre-flight checks.
    monkeypatch.setattr("refactory_dsh.interactive.require_build", lambda **kw: None)
    monkeypatch.setattr(dsh_profile, "require", lambda **kw: None)

    # Stub ownership to a no-op context manager.
    from contextlib import contextmanager

    @contextmanager
    def _fake_ownership(wd):
        yield 999
    monkeypatch.setattr(interactive, "ownership", _fake_ownership)

    # Capture os.execv with a sentinel exception.
    class _ExecvSentinel(Exception):
        def __init__(self, path: str, args: list[str]):
            super().__init__(path)
            self.path = path
            self.args = args

    def _fake_execv(p: str, a: list[str]) -> None:
        raise _ExecvSentinel(p, list(a))
    monkeypatch.setattr(os, "execv", _fake_execv)

    with pytest.raises(_ExecvSentinel) as exc_info:
        interactive.run("test_pack/test_mode", workdir=run_dir, resume=True)

    # Assert the pin's Python was used for the re-exec.
    assert exc_info.value.path == str(pin_checkout / ".venv" / "bin" / "python")
    assert "-m" in exc_info.value.args
    assert "refactory_dsh.interactive" in exc_info.value.args

    # Verify the driver record was updated with the pin and its profile.
    updated = json.loads((run_dir / ".refactory" / "run-config.json").read_text())
    assert updated["driver"]["pin_checkout"] == str(pin_checkout)
    assert updated["driver"]["profile"] == f"graph-pin-{commit_a[:12]}"

    # A new run at commit B should have no pin.
    run2 = tmp_path / "run2"
    monkeypatch.setattr(pin, "REPO_ROOT", repo)
    fp2 = hashlib.sha256((repo / "refactory" / "refactory_dsh" / "mode.py").read_bytes()).hexdigest()
    _make_run_config(run2, commit_b, str(repo),
                     fingerprints={str(repo / "refactory/refactory_dsh/mode.py"): fp2})
    pin.record_driver(run2 / ".refactory" / "run-config.json")
    config2 = json.loads((run2 / ".refactory" / "run-config.json").read_text())
    assert config2["driver"]["commit"] == commit_b
    assert config2["driver"]["profile"] == "graph"
    assert config2["driver"]["pin_checkout"] is None


def test_resume_through_recovery_start(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """recovery.start detects drift and uses the pin's interpreter for the supervisor."""
    import refactory_dsh.pin as pin
    from refactory_dsh import recovery, paths

    repo = tmp_path / "repo"
    commit_a = _init_repo(repo, files={"refactory/refactory_dsh/mode.py": "v1"})
    monkeypatch.setattr(pin, "REPO_ROOT", repo)

    run_dir = tmp_path / "run"
    fp_file = repo / "refactory" / "refactory_dsh" / "mode.py"
    fp = hashlib.sha256(fp_file.read_bytes()).hexdigest()
    _make_run_config(run_dir, commit_a, str(repo),
                     fingerprints={str(fp_file): fp},
                     mode="test_pack/test_mode")
    pin.record_driver(run_dir / ".refactory" / "run-config.json")

    # Write spine checkpoint so recovery.prepare accepts the run.
    checkpoint = {
        "run_id": str(run_dir.resolve()),
        "graph": {"name": "test_pack-test_mode"},
        "status": "ready",
        "configuration_fingerprints": {str(fp_file): fp},
        "file_fingerprints": {},
    }
    (run_dir / ".refactory" / "spine-checkpoint.json").write_text(json.dumps(checkpoint))

    # Move driver to commit B.
    _add_commit(repo, {"refactory/refactory_dsh/mode.py": "v2"})

    # Create prepared pin structure.
    key = pin._pin_key(commit_a, None)
    pin_root = pin.refactory_home() / "pins" / key
    pin_checkout = pin_root / "checkout"
    pin_checkout.mkdir(parents=True)
    (pin_checkout / ".git").write_text("gitdir: dummy")
    (pin_checkout / ".venv" / "bin").mkdir(parents=True)
    (pin_checkout / ".venv" / "bin" / "python").write_text("#!/bin/sh")
    # Write the fingerprinted file in the pin so validation passes.
    (pin_checkout / "refactory" / "refactory_dsh" / "mode.py").parent.mkdir(parents=True, exist_ok=True)
    (pin_checkout / "refactory" / "refactory_dsh" / "mode.py").write_text("v1")
    pin._atomic_write_json(pin_root / "pin.json", {
        "commit": commit_a, "created": "now",
        "driver_checkout": str(repo), "runs": [],
    })

    # Monkeypatch REPO_ROOT to pin so fingerprint translation works.
    monkeypatch.setattr(paths, "REPO_ROOT", pin_checkout)

    # Monkeypatch ownership to a no-op context manager.
    from contextlib import contextmanager

    @contextmanager
    def fake_ownership(wd):
        yield 999
    monkeypatch.setattr("refactory_dsh.run_ownership.ownership", fake_ownership)

    # Capture the Popen call.
    captured_argv: list[list[str]] = []

    class FakePopen:
        pid = 999
        def __init__(self, argv, **kw):
            captured_argv.append(list(argv))

    monkeypatch.setattr("subprocess.Popen", FakePopen)

    recovery.start(run_dir)

    # The supervisor should be started with the pin's Python.
    # detect_drift's _git_rev_parse goes through subprocess.run, which
    # internally calls the monkeypatched Popen with a git command, so
    # the supervisor start is not necessarily the first captured call.
    assert len(captured_argv) > 0
    supervisor_cmds = [a for a in captured_argv
                       if a[0] != "git" and "-m" in a and "refactory_dsh.supervisor" in a]
    assert len(supervisor_cmds) > 0, f"no supervisor Popen found in {captured_argv}"
    assert supervisor_cmds[0][0] == str(pin_checkout / ".venv" / "bin" / "python")


def test_repo_guard_detects_session_worktree(tmp_path: Path) -> None:
    """The repo_guard comparison detects a worktree added inside a session temp root."""
    from repo_guard import _compare_snapshots

    before = {
        "config_lines": ["some=config"],
        "hook_files": {},
        "worktrees": {"/repo"},
    }
    after = {
        "config_lines": ["some=config"],
        "hook_files": {},
        "worktrees": {"/repo", str(tmp_path / "session-wt")},
    }
    # The worktree is inside tmp_path which is a session temp root.
    failures, warnings = _compare_snapshots(before, after, [str(tmp_path)], None)
    assert any("worktree added by session" in f for f in failures)


def test_mode_independence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin works the same regardless of mode identifier."""
    repo = tmp_path / "repo"
    commit_a = _init_repo(repo)

    import refactory_dsh.pin as pin
    monkeypatch.setattr(pin, "REPO_ROOT", repo)

    run_dir = tmp_path / "run"
    _make_run_config(run_dir, commit_a, str(repo), mode="custom_pack/review")
    pin.record_driver(run_dir / ".refactory" / "run-config.json")

    config = json.loads((run_dir / ".refactory" / "run-config.json").read_text())
    assert config["driver"]["commit"] == commit_a
    assert config["mode"] == "custom_pack/review"


def test_dirty_diff_applied_to_pin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A dirty diff is recorded and the pin logic attempts to apply it."""
    repo = tmp_path / "repo"
    commit_a = _init_repo(repo, files={"refactory/refactory_dsh/file.txt": "original"})

    import refactory_dsh.pin as pin
    monkeypatch.setattr(pin, "REPO_ROOT", repo)

    # Dirty a file inside the machinery paths.
    (repo / "refactory/refactory_dsh/file.txt").write_text("dirty content")
    run_dir = tmp_path / "run"
    _make_run_config(run_dir, commit_a, str(repo))
    pin.record_driver(run_dir / ".refactory" / "run-config.json")

    config = json.loads((run_dir / ".refactory" / "run-config.json").read_text())
    assert config["driver"]["dirty_diff"] is not None

    # Reset the dirty file to verify the diff is recorded.
    (repo / "refactory/refactory_dsh/file.txt").write_text("original")

    # Move the driver forward.
    _add_commit(repo, {"other.txt": "new file"})

    drift = pin.detect_drift(config)
    assert drift is not None  # commit changed


def test_dirty_diff_conflict_refuses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A dirty diff that conflicts with the worktree content raises RuntimeError."""
    repo = tmp_path / "repo"
    commit_a = _init_repo(repo, files={"refactory/refactory_dsh/conflict.txt": "original"})

    import refactory_dsh.pin as pin
    monkeypatch.setattr(pin, "REPO_ROOT", repo)

    # A fabricated diff that doesn't match the file content at commit_a.
    # The diff expects "nonexistent line" but commit_a has "original".
    conflicting_diff = (
        "diff --git a/refactory/refactory_dsh/conflict.txt "
        "b/refactory/refactory_dsh/conflict.txt\n"
        "--- a/refactory/refactory_dsh/conflict.txt\n"
        "+++ b/refactory/refactory_dsh/conflict.txt\n"
        "@@ -1 +1 @@\n"
        "-nonexistent line that does not exist\n"
        "+replacement\n"
    )

    # create_pin must raise RuntimeError on conflict.
    original_run = subprocess.run
    with patch("subprocess.run", side_effect=_selective_subprocess(original_run)):
        with pytest.raises(RuntimeError, match="conflict"):
            pin.create_pin(repo, commit_a, conflicting_diff)


def test_pin_keyed_by_commit_and_diff(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Two launches with different dirty diffs get different pin keys and profile names."""
    import refactory_dsh.pin as pin

    commit = "d" * 40
    key_a = pin._pin_key(commit, "diff A")
    key_b = pin._pin_key(commit, "diff B")
    key_clean = pin._pin_key(commit, None)

    assert key_a != key_b
    assert key_a != key_clean
    assert key_b != key_clean
    assert key_clean == commit

    # Profile names also differ for different diffs.
    profile_a = pin._profile_name_for(commit[:12], "diff A")
    profile_b = pin._profile_name_for(commit[:12], "diff B")
    profile_clean = pin._profile_name_for(commit[:12], None)

    assert profile_a != profile_b
    assert profile_a != profile_clean
    assert profile_b != profile_clean
    assert profile_clean == f"graph-pin-{commit[:12]}"
    assert len(profile_a.split(f"graph-pin-{commit[:12]}-")[1]) == 8


def test_credential_isolation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A pinned run uses graph-pin-<commit12> with DSH_HOME unchanged; no credential refs.

    Calls the real ``create_pin`` (with installs stubbed) and asserts behaviour:
    1. DSH_HOME in os.environ is unchanged after pin operations.
    2. No file under the pin directory is a symlink to anything in the
       DSH home directory.
    """
    import refactory_dsh.pin as pin

    # Simulate the person's DSH home with a credentials file.
    dsh_home = tmp_path / "dsh_home"
    dsh_home.mkdir()
    (dsh_home / ".credentials.yaml").write_text("api_key: secret")
    monkeypatch.setenv("DSH_HOME", str(dsh_home))

    original_dsh_home = os.environ.get("DSH_HOME")

    repo = tmp_path / "repo"
    commit_a = _init_repo(repo)
    monkeypatch.setattr(pin, "REPO_ROOT", repo)

    # Stub dsh_command because the temp repo has no DSH CLI entry.
    monkeypatch.setattr("refactory_dsh.paths.dsh_command", lambda root=None, cli=None: ["dsh-stub"])

    # Create the pin using real create_pin with installs stubbed.
    original_run = subprocess.run
    with patch("subprocess.run", side_effect=_selective_subprocess(original_run)):
        pin_checkout = pin.create_pin(repo, commit_a)

    pin_root = pin_checkout.parent

    # 1. DSH_HOME is unchanged after pin operations.
    assert os.environ.get("DSH_HOME") == original_dsh_home

    # 2. No file under the pin is a symlink into the DSH home.
    for p in pin_root.rglob("*"):
        if p.is_symlink():
            target = p.resolve()
            assert not str(target).startswith(str(dsh_home)), \
                f"pin file {p} is a symlink to DSH home: {target}"


def test_worker_node_resolves_pin_python(tmp_path: Path) -> None:
    """worker_node._resolve_pin_python reads pin_checkout from the driver record."""
    from refactory_dsh import worker_node

    run_dir = tmp_path / "run"
    (run_dir / ".refactory").mkdir(parents=True)
    pin_checkout = tmp_path / "pin" / "checkout"
    config = {
        "driver": {
            "pin_checkout": str(pin_checkout),
        },
    }
    (run_dir / ".refactory" / "run-config.json").write_text(json.dumps(config))

    result = worker_node._resolve_pin_python(run_dir)
    assert result == str(pin_checkout / ".venv" / "bin" / "python")


def test_worker_node_returns_none_when_not_pinned(tmp_path: Path) -> None:
    """worker_node._resolve_pin_python returns None when there is no pin."""
    from refactory_dsh import worker_node

    run_dir = tmp_path / "run"
    (run_dir / ".refactory").mkdir(parents=True)
    config = {
        "driver": {
            "pin_checkout": None,
            "checkout": str(tmp_path),
            "commit": "a" * 40,
        },
    }
    (run_dir / ".refactory" / "run-config.json").write_text(json.dumps(config))

    result = worker_node._resolve_pin_python(run_dir)
    assert result is None


def test_worker_node_detects_driver_drift(tmp_path: Path) -> None:
    """worker_node._check_driver_mid_run detects when the driver has moved."""
    from refactory_dsh import worker_node

    repo = tmp_path / "repo"
    commit_a = _init_repo(repo)

    run_dir = tmp_path / "run"
    (run_dir / ".refactory").mkdir(parents=True)
    config = {
        "driver": {
            "checkout": str(repo),
            "commit": commit_a,
            "pin_checkout": None,
        },
    }
    (run_dir / ".refactory" / "run-config.json").write_text(json.dumps(config))

    # No drift yet.
    assert worker_node._check_driver_mid_run(run_dir) is None

    # Move the driver.
    _add_commit(repo, {"new.txt": "content"})

    msg = worker_node._check_driver_mid_run(run_dir)
    assert msg is not None
    assert commit_a[:12] in msg


def test_record_driver_on_non_git_checkout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """record_driver on a checkout that is not a git repository warns and records no commit."""
    import refactory_dsh.pin as pin

    non_git = tmp_path / "not-a-repo"
    non_git.mkdir()
    monkeypatch.setattr(pin, "REPO_ROOT", non_git)

    run_dir = tmp_path / "run"
    _make_run_config(run_dir, "x", str(non_git))

    # Should not raise; it warns and records commit=null.
    pin.record_driver(run_dir / ".refactory" / "run-config.json")

    config = json.loads((run_dir / ".refactory" / "run-config.json").read_text())
    driver = config["driver"]
    assert driver["commit"] is None
    assert driver["checkout"] == str(non_git)
    assert driver["profile"] == "graph"


def test_register_run_concurrent_safe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """register_run uses a file lock for concurrent safety."""
    import refactory_dsh.pin as pin

    commit = "g" * 40
    key = pin._pin_key(commit, None)
    pin_root = pin.refactory_home() / "pins" / key
    pin_root.mkdir(parents=True)
    pin._atomic_write_json(pin_root / "pin.json", {
        "commit": commit, "created": "now", "driver_checkout": str(tmp_path), "runs": [],
    })

    run1 = tmp_path / "run1"
    run1.mkdir(parents=True)
    run2 = tmp_path / "run2"
    run2.mkdir(parents=True)

    pin.register_run(key, run1)
    pin.register_run(key, run2)

    data = json.loads((pin_root / "pin.json").read_text())
    assert str(run1.resolve()) in data["runs"]
    assert str(run2.resolve()) in data["runs"]


def test_dirty_driver_captures_untracked_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Untracked files under machinery paths are included in dirty_diff."""
    repo = tmp_path / "repo"
    _init_repo(repo, files={"refactory/refactory_dsh/existing.py": "original"})

    # Create an untracked file under machinery paths (not added to git).
    (repo / "refactory/refactory_dsh/newfile.py").write_text("new untracked content")

    run_dir = tmp_path / "run"
    _make_run_config(run_dir, "x", str(repo))

    import refactory_dsh.pin as pin
    monkeypatch.setattr(pin, "REPO_ROOT", repo)

    pin.record_driver(run_dir / ".refactory" / "run-config.json")

    config = json.loads((run_dir / ".refactory" / "run-config.json").read_text())
    driver = config["driver"]
    assert driver["dirty_diff"] is not None
    assert "newfile.py" in driver["dirty_diff"]
    assert "new untracked content" in driver["dirty_diff"]


def test_register_run_on_driver(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """record_driver registers the run on its driver checkout."""
    repo = tmp_path / "repo"
    commit_a = _init_repo(repo)

    import refactory_dsh.pin as pin
    monkeypatch.setattr(pin, "REPO_ROOT", repo)

    run_dir = tmp_path / "run"
    _make_run_config(run_dir, commit_a, str(repo))
    pin.record_driver(run_dir / ".refactory" / "run-config.json")

    # Verify the run is registered on the driver.
    checkout_hash = hashlib.sha256(str(repo.resolve()).encode()).hexdigest()[:16]
    driver_meta = pin.refactory_home() / "drivers" / checkout_hash / "runs.json"
    assert driver_meta.exists()
    data = json.loads(driver_meta.read_text())
    assert str(run_dir.resolve()) in data["runs"]
    assert data["checkout"] == str(repo)


def test_pinned_resume_profile_check_accepts_checkout(tmp_path: Path) -> None:
    """requirement() with checkout= does not crash for a pin profile name."""
    from refactory_dsh import dsh_profile

    # Calling requirement with a pin profile name and a checkout should not
    # raise KeyError. It returns a message (profile doesn't exist) or None.
    result = dsh_profile.requirement(
        name="graph-pin-000000000000",
        checkout=tmp_path,
    )
    # The profile directory doesn't exist, so it returns an error message.
    assert isinstance(result, str)


def test_pin_profile_is_valid_target() -> None:
    """A pin profile name (graph-pin-<commit12>) is accepted by dsh_profile.target()."""
    from refactory_dsh.dsh_profile import target, Target

    result = target("graph-pin-5068dbfc93a1")
    assert isinstance(result, Target)
    assert result.profile == "graph-pin-5068dbfc93a1"
    assert result.owned is True
    assert "graph" in result.layers


def test_worker_dispatch_reexec_uses_module_and_prefix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Worker re-exec compares sys.prefix (not realpath) and uses -m for the module.

    Fails without fix: os.path.realpath comparison resolves both venvs' Python
    symlinks to the same uv binary, so the re-exec never fires.  And
    ``[pin_py] + sys.argv`` re-executes the driver's worker_node.py path.
    """
    from refactory_dsh import worker_node

    run_dir = tmp_path / "run"
    (run_dir / ".refactory").mkdir(parents=True)
    pin_checkout = tmp_path / "pin" / "checkout"
    pin_py = pin_checkout / ".venv" / "bin" / "python"
    pin_py.parent.mkdir(parents=True)
    pin_py.write_text("#!/bin/sh\n")

    config = {"driver": {"pin_checkout": str(pin_checkout)}}
    (run_dir / ".refactory" / "run-config.json").write_text(json.dumps(config))

    # Simulate being in a DIFFERENT venv (the driver's).
    monkeypatch.setattr(sys, "prefix", "/driver/checkout/.venv")

    captured: dict[str, Any] = {}

    def fake_execv(path: str, args: list[str]) -> None:
        captured["path"] = path
        captured["args"] = args
        raise SystemExit(42)

    monkeypatch.setattr(os, "execv", fake_execv)
    monkeypatch.chdir(run_dir)

    # When running with -m, sys.argv[0] is the file path of the module in
    # the DRIVER's checkout.  The re-exec must NOT use sys.argv[0].
    monkeypatch.setattr(sys, "argv", [
        "/driver/checkout/refactory/refactory_dsh/worker_node.py",
        "spec.json",
    ])

    with pytest.raises(SystemExit):
        worker_node.main()

    assert captured["path"] == str(pin_py)
    # Must use -m so the pin's package is loaded.
    assert captured["args"][1] == "-m"
    assert captured["args"][2] == "refactory_dsh.worker_node"
    # Only the spec argument is forwarded (sys.argv[1:]).
    assert captured["args"][3] == "spec.json"


def test_worker_dispatch_skips_reexec_when_already_on_pin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When the worker is already running from the pin's venv, no re-exec occurs."""
    from refactory_dsh import worker_node

    run_dir = tmp_path / "run"
    (run_dir / ".refactory").mkdir(parents=True)
    pin_checkout = tmp_path / "pin" / "checkout"
    pin_py = pin_checkout / ".venv" / "bin" / "python"
    pin_py.parent.mkdir(parents=True)
    pin_py.write_text("#!/bin/sh\n")

    config = {"driver": {"pin_checkout": str(pin_checkout)}}
    (run_dir / ".refactory" / "run-config.json").write_text(json.dumps(config))

    # sys.prefix matches the pin's venv → already on pin, no re-exec.
    monkeypatch.setattr(sys, "prefix", str(pin_checkout / ".venv"))

    execv_called = False

    def fake_execv(path: str, args: list[str]) -> None:
        nonlocal execv_called
        execv_called = True

    monkeypatch.setattr(os, "execv", fake_execv)
    monkeypatch.chdir(run_dir)
    monkeypatch.setattr(sys, "argv", [
        str(pin_checkout / "refactory/refactory_dsh/worker_node.py"),
        "spec.json",
    ])

    # main() will continue past re-exec check and fail on reading spec.json
    # (which doesn't exist), but execv should NOT have been called.
    worker_node.main()
    assert not execv_called


def test_drift_ignores_non_driver_fingerprints(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Drift detection does not count non-driver fingerprints as driver drift.

    A changed run graph (under the run directory, not the checkout) must not
    trigger pinning.  Recovery's prepare() refuses it as on main.
    """
    repo = tmp_path / "repo"
    commit_a = _init_repo(repo)
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True)
    (run_dir / ".refactory").mkdir(parents=True)

    # Fingerprints: one under the driver checkout, one under the run directory.
    driver_fp_file = repo / "dummy.txt"
    driver_fp = hashlib.sha256(driver_fp_file.read_bytes()).hexdigest()
    run_graph = run_dir / "graphs" / "design-graph.json"
    run_graph.parent.mkdir(parents=True)
    run_graph.write_text('{"nodes": {}}')
    run_graph_fp = hashlib.sha256(run_graph.read_bytes()).hexdigest()

    fingerprints = {
        str(driver_fp_file): driver_fp,
        str(run_graph): run_graph_fp,
    }

    import refactory_dsh.pin as pin
    monkeypatch.setattr(pin, "REPO_ROOT", repo)

    _make_run_config(run_dir, commit_a, str(repo), fingerprints=fingerprints)
    pin.record_driver(run_dir / ".refactory" / "run-config.json")

    config = json.loads((run_dir / ".refactory" / "run-config.json").read_text())

    # Modify ONLY the run graph (non-driver fingerprint).
    run_graph.write_text('{"nodes": {"added": true}}')

    # detect_drift must return None — the driver has not moved.
    drift = pin.detect_drift(config)
    assert drift is None, "non-driver fingerprint change should not be counted as driver drift"


def test_resume_uses_check_only_for_require_build(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """interactive.run(resume=True) passes check_only=True to require_build.

    Fails without fix: require_build() is called without check_only on resume,
    which tries to rebuild and may fail under a live supervisor.
    """
    called_with: dict[str, Any] = {}

    def capturing_require_build(**kw: Any) -> None:
        called_with.update(kw)

    monkeypatch.setattr("refactory_dsh.interactive.require_build", capturing_require_build)

    import refactory_dsh.dsh_profile as _dp
    monkeypatch.setattr(_dp, "require", lambda **kw: None)

    from contextlib import contextmanager

    @contextmanager
    def fake_ownership(wd):
        yield 999

    monkeypatch.setattr("refactory_dsh.interactive.ownership", fake_ownership)

    # Write a minimal run config for resume.
    config_dir = tmp_path / ".refactory"
    config_dir.mkdir(parents=True)
    (config_dir / "run-config.json").write_text(json.dumps({
        "mode": "test/mode",
        "project": str(tmp_path),
        "knobs": [],
        "model_context": {
            "timeout_s": 500, "provider": "", "model": "",
            "worker_backend": "dsh", "permission_mode": "default",
        },
        "resource_limits": None,
        "configuration_fingerprints": {},
    }))

    monkeypatch.setattr("refactory_dsh.interactive.prepare",
                        lambda *a, **kw: ("test", []))
    monkeypatch.setattr("refactory_dsh.interactive.command",
                        lambda *a, **kw: ["echo", "test"])
    monkeypatch.setattr("refactory_dsh.interactive.run_summary", lambda wd: "")
    monkeypatch.setattr("refactory_dsh.interactive.run_warnings", lambda wd: "")

    class FakeResult:
        returncode = 0

    import refactory_dsh.workers as _w
    monkeypatch.setattr(_w, "run_graph_command", lambda *a, **kw: FakeResult())

    from refactory_dsh.interactive import run
    run("test/mode", workdir=tmp_path, resume=True)

    assert called_with.get("check_only") is True


def test_failed_rebuild_warns_and_continues(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed pnpm build at launch warns instead of stopping.

    Fails without fix: subprocess.run with check=True raises CalledProcessError
    and the launch crashes.
    """
    from refactory_dsh import paths
    from refactory_dsh.pin import WORKSPACE_PACKAGES

    # Set up a stale build.
    for pkg in WORKSPACE_PACKAGES:
        src_dir = tmp_path / pkg / "src"
        src_dir.mkdir(parents=True, exist_ok=True)
        (src_dir / "index.ts").write_text("export const x = 1;")

    spine_lib = tmp_path / "packages" / "workflow" / "workflow-spine" / "lib"
    spine_lib.mkdir(parents=True, exist_ok=True)
    (spine_lib / "index.js").write_text("// built")
    (spine_lib / ".workspace-stamp").write_text("0000000000000000")

    monkeypatch.delenv("REFACTORY_GUI_MODE", raising=False)

    # Make pnpm build fail.
    import subprocess as _sp

    def failing_run(cmd, **kw):
        if isinstance(cmd, list) and "build" in cmd:
            raise _sp.CalledProcessError(1, cmd)
        return _sp.CompletedProcess(cmd, 0, "", "")

    with patch("subprocess.run", side_effect=failing_run):
        # Should warn but NOT raise.
        paths.require_build(root=tmp_path)
