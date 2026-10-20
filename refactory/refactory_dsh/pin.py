"""Pin lifecycle: record, detect drift, create, resolve and prune driver pins.

A run records its driver checkout at launch. When the driver later moves (commit
changes or fingerprinted files differ), the run lazily creates a pin: a prepared
git worktree at the original commit with a commit-specific DSH profile
(``graph-pin-<commit12>``) in the person's own DSH home. Credentials stay in
``~/.dsh``; DSH_HOME is never changed.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import os
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from refactory_dsh.paths import REPO_ROOT, refactory_home

logger = logging.getLogger(__name__)

#: The TS packages whose sources feed the workspace-wide build stamp, in order.
WORKSPACE_PACKAGES: tuple[str, ...] = (
    "packages/workflow/workflow-spine",
    "packages/bundle/graph",
    "packages/bundle/refactory",
    "packages/bundle/refactory-web",
    "packages/refactory/tool-refactory",
    "packages/refactory/refactory-console",
    "packages/client/ui-refactory",
    "packages/client/ui-brand-refactory",
)

#: Machinery paths within the checkout whose changes matter for fingerprint drift.
MACHINERY_GLOBS: tuple[str, ...] = (
    "refactory/refactory_dsh",
)


# ── driver recording ─────────────────────────────────────────────────────


def record_driver(run_config_path: Path) -> None:
    """Add a ``driver`` key to the run's ``run-config.json``.

    Called once at launch, right after the config is first written.
    """
    run_config_path = Path(run_config_path)
    config = json.loads(run_config_path.read_text())

    checkout = str(REPO_ROOT)
    commit = _git_rev_parse(Path(checkout))

    # Dirty diff of machinery paths.
    dirty_diff: str | None = None
    try:
        result = subprocess.run(
            ["git", "-C", checkout, "diff", "--", *MACHINERY_GLOBS],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0 and result.stdout.strip():
            dirty_diff = result.stdout
    except (OSError, subprocess.SubprocessError):
        pass

    fingerprints_at_launch = dict(config.get("configuration_fingerprints") or {})

    config["driver"] = {
        "checkout": checkout,
        "commit": commit,
        "dirty_diff": dirty_diff,
        "fingerprints_at_launch": fingerprints_at_launch,
        "profile": "graph",
        "pin_checkout": None,
    }
    _atomic_write_json(run_config_path, config)


# ── drift detection ──────────────────────────────────────────────────────


def detect_drift(run_config: dict) -> dict | None:
    """Return a drift descriptor when the driver has moved, else ``None``.

    Checks *both* the commit and the recorded configuration fingerprints
    against the current checkout state.
    """
    driver = run_config.get("driver")
    if driver is None:
        return None

    checkout = driver["checkout"]
    recorded_commit = driver["commit"]

    # 1. Commit drift.
    try:
        current_commit = _git_rev_parse(Path(checkout))
    except Exception:
        current_commit = recorded_commit  # cannot check; assume no drift

    if current_commit != recorded_commit:
        return {"kind": "commit", "recorded_commit": recorded_commit}

    # 2. Fingerprint drift (uncommitted edits to fingerprinted files).
    from refactory_dsh.recovery import _configuration_fingerprint

    fingerprints_at_launch = driver.get("fingerprints_at_launch") or {}
    for fp_path, expected in fingerprints_at_launch.items():
        try:
            current = _configuration_fingerprint(Path(fp_path))
        except Exception:
            continue
        if current != expected:
            return {"kind": "fingerprint", "recorded_commit": recorded_commit}

    return None


# ── pin resolution ───────────────────────────────────────────────────────


def resolve_pin(driver_info: dict, *, run_dir: Path | None = None) -> Path:
    """Return the pin's checkout path, creating the pin if needed.

    Updates the driver record in run-config.json: sets ``pin_checkout`` and
    ``profile`` to ``graph-pin-<commit12>``.
    """
    commit = driver_info["commit"]
    commit12 = commit[:12]
    pin_root = refactory_home() / "pins" / commit
    pin_checkout = pin_root / "checkout"

    # Already pinned and prepared?
    if driver_info.get("pin_checkout"):
        existing = Path(driver_info["pin_checkout"])
        if existing.is_dir():
            return existing

    # A prepared pin for this commit may already exist from another run.
    if (pin_checkout / ".git").exists() or (pin_checkout / ".git").is_file():
        # Pin exists; register this run if a run_dir was given.
        if run_dir is not None:
            _update_driver_record(run_dir, pin_checkout, commit12)
            register_run(commit, run_dir)
        return pin_checkout

    # Create the pin.
    driver_checkout = Path(driver_info["checkout"])
    dirty_diff = driver_info.get("dirty_diff")
    pin_checkout = create_pin(driver_checkout, commit, dirty_diff)

    if run_dir is not None:
        _update_driver_record(run_dir, pin_checkout, commit12)
        register_run(commit, run_dir)

    return pin_checkout


def _update_driver_record(run_dir: Path, pin_checkout: Path, commit12: str) -> None:
    """Update the driver record in the run's run-config.json."""
    config_path = Path(run_dir) / ".refactory" / "run-config.json"
    if not config_path.exists():
        return
    config = json.loads(config_path.read_text())
    driver = config.get("driver")
    if driver is None:
        return
    driver["pin_checkout"] = str(pin_checkout)
    driver["profile"] = f"graph-pin-{commit12}"
    _atomic_write_json(config_path, config)


# ── pin creation ─────────────────────────────────────────────────────────


def create_pin(driver_checkout: Path, commit: str, dirty_diff: str | None = None) -> Path:
    """Create a pin worktree at ``commit``, prepared and ready to use.

    Uses an exclusive file lock so concurrent callers share one preparation.
    """
    commit12 = commit[:12]
    pin_root = refactory_home() / "pins" / commit
    pin_checkout = pin_root / "checkout"
    staging_root = refactory_home() / "pins" / f"{commit}.preparing"
    staging_checkout = staging_root / "checkout"
    lock_path = refactory_home() / "pins" / f"{commit}.lock"

    # Fast path: already prepared.
    if pin_checkout.is_dir():
        return pin_checkout

    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        # Re-check after acquiring the lock.
        if pin_checkout.is_dir():
            return pin_checkout

        # Clean up any previous failed staging.
        if staging_root.exists():
            shutil.rmtree(staging_root, ignore_errors=True)
        staging_root.mkdir(parents=True, exist_ok=True)

        # Create a detached worktree.
        subprocess.run(
            ["git", "-C", str(driver_checkout), "worktree", "add", "--detach",
             str(staging_checkout), commit],
            check=True, capture_output=True, text=True, timeout=120,
        )

        # Enable extensions.worktreeConfig on the driver if not already set.
        try:
            result = subprocess.run(
                ["git", "-C", str(driver_checkout), "config", "--get", "extensions.worktreeConfig"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode != 0 or result.stdout.strip().lower() != "true":
                subprocess.run(
                    ["git", "-C", str(driver_checkout), "config", "extensions.worktreeConfig", "true"],
                    check=True, capture_output=True, text=True, timeout=10,
                )
        except (OSError, subprocess.SubprocessError):
            pass

        # Isolate hooks: per-worktree core.hooksPath = /dev/null.
        try:
            subprocess.run(
                ["git", "-C", str(staging_checkout), "config", "--worktree",
                 "core.hooksPath", "/dev/null"],
                check=True, capture_output=True, text=True, timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            pass

        # Apply dirty diff if present.
        if dirty_diff:
            result = subprocess.run(
                ["git", "-C", str(staging_checkout), "apply", "-"],
                input=dirty_diff, capture_output=True, text=True, timeout=60,
            )
            if result.returncode != 0:
                # Clean up and raise.
                shutil.rmtree(staging_root, ignore_errors=True)
                raise RuntimeError(
                    f"Run was launched from commit {commit} with uncommitted changes "
                    f"that conflict with the pin; the conflicting output: {result.stderr[:500]}"
                )

        # Prepare the pin: uv sync, pnpm install, pnpm build.
        env = {**os.environ, "LEFTHOOK": "0", "HUSKY": "0"}
        try:
            subprocess.run(
                ["uv", "sync"], cwd=str(staging_checkout), env=env,
                check=True, capture_output=True, text=True, timeout=600,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            logger.warning("pin uv sync failed: %s", exc)

        try:
            subprocess.run(
                ["pnpm", "install"], cwd=str(staging_checkout), env=env,
                check=True, capture_output=True, text=True, timeout=600,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            logger.warning("pin pnpm install failed: %s", exc)

        try:
            subprocess.run(
                ["pnpm", "run", "build"], cwd=str(staging_checkout), env=env,
                check=True, capture_output=True, text=True, timeout=600,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            logger.warning("pin pnpm build failed: %s", exc)

        # Provision the profile in the person's DSH home.
        profile_name = f"graph-pin-{commit12}"
        bundle_path = staging_checkout / "packages" / "bundle" / "graph"
        try:
            from refactory_dsh.paths import dsh_command
            dsh_argv = dsh_command()
            subprocess.run(
                [*dsh_argv, "plugin", "--profile", profile_name, "add", str(bundle_path)],
                check=True, capture_output=True, text=True, timeout=120,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            logger.warning("pin profile provisioning failed: %s", exc)

        # Write pin.json.
        pin_json = {
            "commit": commit,
            "created": datetime.now(timezone.utc).isoformat(),
            "driver_checkout": str(driver_checkout),
            "runs": [],
        }
        _atomic_write_json(staging_root / "pin.json", pin_json)

        # Atomically rename staging to final.
        staging_root.rename(pin_root)
        return pin_checkout

    except BaseException:
        # Clean up staging on failure.
        if staging_root.exists():
            shutil.rmtree(staging_root, ignore_errors=True)
        raise
    finally:
        os.close(lock_fd)


# ── pin utilities ────────────────────────────────────────────────────────


def pin_python(pin_checkout: Path) -> Path:
    """The Python interpreter inside a pin's checkout."""
    return Path(pin_checkout) / ".venv" / "bin" / "python"


def register_run(commit: str, run_dir: Path) -> None:
    """Record that ``run_dir`` uses the pin at ``commit``."""
    pin_json_path = refactory_home() / "pins" / commit / "pin.json"
    if not pin_json_path.exists():
        return
    data = json.loads(pin_json_path.read_text())
    runs = data.get("runs", [])
    run_str = str(Path(run_dir).resolve())
    if run_str not in runs:
        runs.append(run_str)
        data["runs"] = runs
        _atomic_write_json(pin_json_path, data)


def list_pins() -> list[dict]:
    """List all pins with their metadata."""
    pins_dir = refactory_home() / "pins"
    if not pins_dir.is_dir():
        return []
    result = []
    for entry in sorted(pins_dir.iterdir()):
        pin_json = entry / "pin.json"
        if pin_json.is_file():
            try:
                data = json.loads(pin_json.read_text())
                data["pin_dir"] = str(entry)
                result.append(data)
            except (OSError, json.JSONDecodeError):
                continue
    return result


def prune_pins(dsh_home: Path | None = None) -> list[str]:
    """Remove pins no run refers to. Returns pruned commit SHAs."""
    pruned: list[str] = []
    pins_dir = refactory_home() / "pins"
    if not pins_dir.is_dir():
        return pruned

    for entry in sorted(pins_dir.iterdir()):
        if not entry.is_dir():
            continue
        pin_json = entry / "pin.json"
        if not pin_json.is_file():
            continue
        try:
            data = json.loads(pin_json.read_text())
        except (OSError, json.JSONDecodeError):
            continue

        commit = data.get("commit", entry.name)
        commit12 = commit[:12]
        runs = data.get("runs", [])

        # Check each run: remove dead references.
        live_runs: list[str] = []
        for run_path_str in runs:
            run_path = Path(run_path_str)
            if not run_path.exists():
                continue
            # Check if the run is terminal.
            config_path = run_path / ".refactory" / "run-config.json"
            status_path = run_path / ".refactory" / "spine-status.json"
            is_terminal = False
            try:
                if status_path.exists():
                    status_data = json.loads(status_path.read_text())
                    if status_data.get("status") in ("done", "failed", "cancelled"):
                        is_terminal = True
            except (OSError, json.JSONDecodeError):
                pass
            if not is_terminal:
                live_runs.append(run_path_str)

        if live_runs:
            # Update the runs list but keep the pin.
            data["runs"] = live_runs
            _atomic_write_json(pin_json, data)
            continue

        # No live runs — remove the pin.
        driver_checkout = data.get("driver_checkout")
        checkout_path = entry / "checkout"
        if checkout_path.is_dir() and driver_checkout:
            try:
                subprocess.run(
                    ["git", "-C", str(driver_checkout), "worktree", "remove", "--force",
                     str(checkout_path)],
                    capture_output=True, text=True, timeout=60,
                )
            except (OSError, subprocess.SubprocessError):
                pass

        # Remove the profile.
        profile_name = f"graph-pin-{commit12}"
        try:
            from refactory_dsh.paths import dsh_command
            dsh_argv = dsh_command()
            bundle_path = checkout_path / "packages" / "bundle" / "graph"
            subprocess.run(
                [*dsh_argv, "plugin", "--profile", profile_name, "remove", str(bundle_path)],
                capture_output=True, text=True, timeout=60,
            )
        except (OSError, subprocess.SubprocessError):
            pass

        # Remove the pin directory.
        shutil.rmtree(entry, ignore_errors=True)
        pruned.append(commit)

    return pruned


# ── workspace stamp ──────────────────────────────────────────────────────


def workspace_stamp(root: Path) -> str | None:
    """Compute a SHA-256 stamp of all ``*.ts`` source files across workspace packages.

    Returns a 16-character hex hash, or ``None`` when no source files exist.
    """
    root = Path(root)
    hasher = hashlib.sha256()
    found_any = False

    for pkg_rel in WORKSPACE_PACKAGES:
        src_dir = root / pkg_rel / "src"
        if not src_dir.is_dir():
            continue
        ts_files = sorted(
            p for p in src_dir.rglob("*.ts") if p.is_file()
        )
        for ts_file in ts_files:
            rel = str(ts_file.relative_to(root))
            hasher.update(rel.encode())
            hasher.update(ts_file.read_bytes())
            found_any = True

    return hasher.hexdigest()[:16] if found_any else None


# ── helpers ──────────────────────────────────────────────────────────────


def _git_rev_parse(checkout: Path) -> str:
    """The HEAD commit of ``checkout``."""
    result = subprocess.run(
        ["git", "-C", str(checkout), "rev-parse", "HEAD"],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git rev-parse HEAD failed in {checkout}: {result.stderr}")
    return result.stdout.strip()


def _atomic_write_json(path: Path, data: Any) -> None:
    """Write JSON atomically through a temporary file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(path)


def driver_profile(run_dir: Path) -> str:
    """Read the driver's profile name from run-config.json, defaulting to 'graph'."""
    config_path = Path(run_dir) / ".refactory" / "run-config.json"
    if not config_path.exists():
        return "graph"
    try:
        config = json.loads(config_path.read_text())
        return config.get("driver", {}).get("profile", "graph")
    except (OSError, json.JSONDecodeError):
        return "graph"
