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
#: Covers both the Python package and TS package sources.
MACHINERY_GLOBS: tuple[str, ...] = (
    "refactory/refactory_dsh",
    "packages/workflow/workflow-spine/src",
    "packages/bundle/graph/src",
    "packages/bundle/refactory/src",
    "packages/bundle/refactory-web/src",
    "packages/refactory/tool-refactory/src",
    "packages/refactory/refactory-console/src",
    "packages/client/ui-refactory/src",
    "packages/client/ui-brand-refactory/src",
)


# ── driver recording ─────────────────────────────────────────────────────


def record_driver(run_config_path: Path) -> None:
    """Add a ``driver`` key to the run's ``run-config.json``.

    Called once at launch, right after the config is first written.
    The dirty diff covers staged changes, unstaged changes and the TS packages
    (``git diff HEAD`` captures both staged and unstaged in one pass).
    """
    run_config_path = Path(run_config_path)
    config = json.loads(run_config_path.read_text())

    checkout = str(REPO_ROOT)
    commit = _git_rev_parse(Path(checkout))

    # Dirty diff of machinery paths: ``git diff HEAD`` captures both staged
    # and unstaged changes in one pass.
    dirty_diff: str | None = None
    try:
        result = subprocess.run(
            ["git", "-C", checkout, "diff", "HEAD", "--", *MACHINERY_GLOBS],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0 and result.stdout.strip():
            dirty_diff = result.stdout
    except (OSError, subprocess.SubprocessError):
        pass

    # Also capture untracked files under machinery paths.
    try:
        result_untracked = subprocess.run(
            ["git", "-C", checkout, "ls-files", "--others", "--exclude-standard",
             "--", *MACHINERY_GLOBS],
            capture_output=True, text=True, timeout=30,
        )
        if result_untracked.returncode == 0 and result_untracked.stdout.strip():
            untracked_parts: list[str] = []
            for untracked_file in result_untracked.stdout.strip().splitlines():
                full_path = Path(checkout) / untracked_file
                if full_path.is_file():
                    try:
                        content = full_path.read_text()
                    except (OSError, UnicodeDecodeError):
                        continue
                    lines = content.splitlines(keepends=True)
                    n = len(lines)
                    header = (
                        f"diff --git a/{untracked_file} b/{untracked_file}\n"
                        f"new file mode 100644\n"
                        f"--- /dev/null\n"
                        f"+++ b/{untracked_file}\n"
                        f"@@ -0,0 +1,{n} @@\n"
                    )
                    body = "".join(
                        f"+{line}" if line.endswith("\n")
                        else f"+{line}\n\\ No newline at end of file\n"
                        for line in lines
                    )
                    untracked_parts.append(header + body)
            if untracked_parts:
                untracked_section = "\n".join(untracked_parts)
                dirty_diff = (dirty_diff + "\n" + untracked_section) if dirty_diff else untracked_section
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

    # Register the run on its driver checkout so rebuild checks can find it.
    run_dir = run_config_path.parent.parent  # .refactory/run-config.json → run_dir
    register_run_on_driver(Path(checkout), run_dir)


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


# ── pin key ──────────────────────────────────────────────────────────────


def _pin_key(commit: str, dirty_diff: str | None) -> str:
    """The directory name for a pin, keyed by commit and diff hash.

    Two launches from the same commit with different dirty diffs get
    different pins.
    """
    if dirty_diff:
        diff_hash = hashlib.sha256(dirty_diff.encode()).hexdigest()[:12]
        return f"{commit}-{diff_hash}"
    return commit


# ── pin resolution ───────────────────────────────────────────────────────


def resolve_pin(driver_info: dict, *, run_dir: Path | None = None) -> Path:
    """Return the pin's checkout path, creating the pin if needed.

    Updates the driver record in run-config.json: sets ``pin_checkout`` and
    ``profile`` to ``graph-pin-<commit12>``.
    """
    commit = driver_info["commit"]
    commit12 = commit[:12]
    dirty_diff = driver_info.get("dirty_diff")
    key = _pin_key(commit, dirty_diff)
    pin_root = refactory_home() / "pins" / key
    pin_checkout = pin_root / "checkout"

    # Already pinned and prepared?
    if driver_info.get("pin_checkout"):
        existing = Path(driver_info["pin_checkout"])
        if existing.is_dir():
            return existing

    # A prepared pin for this commit+diff may already exist from another run.
    # pin.json is the readiness marker: it is written only after all preparation
    # succeeds, so its presence means the pin is fully usable.
    if (pin_root / "pin.json").is_file():
        # Pin exists; register this run if a run_dir was given.
        if run_dir is not None:
            _update_driver_record(run_dir, pin_checkout, commit12)
            register_run(key, run_dir)
        return pin_checkout

    # Create the pin.
    driver_checkout = Path(driver_info["checkout"])
    pin_checkout = create_pin(driver_checkout, commit, dirty_diff)

    if run_dir is not None:
        _update_driver_record(run_dir, pin_checkout, commit12)
        register_run(key, run_dir)

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
    Preparation happens directly at the final path (no staging rename) so
    git worktree registrations and editable installs reference the permanent
    path from the start.  ``pin.json`` is the readiness marker: written only
    after all preparation succeeds.

    Uses :data:`worktree.NO_HOOKS` for the worktree add and
    :func:`worktree._isolate_hooks` for per-worktree hook isolation, the same
    helpers run worktrees use.
    """
    from refactory_dsh.worktree import NO_HOOKS, _isolate_hooks

    commit12 = commit[:12]
    key = _pin_key(commit, dirty_diff)
    pin_root = refactory_home() / "pins" / key
    pin_checkout = pin_root / "checkout"
    lock_path = refactory_home() / "pins" / f"{key}.lock"

    # Fast path: already prepared (pin.json is the readiness marker).
    if (pin_root / "pin.json").is_file():
        return pin_checkout

    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        # Re-check after acquiring the lock.
        if (pin_root / "pin.json").is_file():
            return pin_checkout

        # Clean up any previous failed attempt at this path.
        if pin_root.exists():
            _cleanup_pin_dir(pin_root, driver_checkout)
        pin_root.mkdir(parents=True, exist_ok=True)

        # Create a detached worktree using worktree.py's NO_HOOKS pattern so
        # hooks do not run during creation.
        subprocess.run(
            ["git", "-C", str(driver_checkout),
             *NO_HOOKS,
             "worktree", "add", "--detach",
             str(pin_checkout), commit],
            check=True, capture_output=True, text=True, timeout=120,
        )

        # Isolate hooks using the same helper run worktrees use.
        # This enables extensions.worktreeConfig on the driver (expected by
        # canary checks) and sets a per-worktree core.hooksPath.
        _isolate_hooks(driver_checkout, pin_checkout)

        # Apply dirty diff if present.
        if dirty_diff:
            result = subprocess.run(
                ["git", "-C", str(pin_checkout), "apply", "-"],
                input=dirty_diff, capture_output=True, text=True, timeout=60,
            )
            if result.returncode != 0:
                raise RuntimeError(
                    f"Run was launched from commit {commit} with uncommitted changes "
                    f"that conflict with the pin; the conflicting output: {result.stderr[:500]}"
                )

        # Prepare the pin: uv sync, pnpm install, pnpm build.
        # Errors propagate — the outer except BaseException cleans up.
        # A broken pin must never be marked ready.
        env = {**os.environ, "LEFTHOOK": "0", "HUSKY": "0"}
        subprocess.run(
            ["uv", "sync"], cwd=str(pin_checkout), env=env,
            check=True, capture_output=True, text=True, timeout=600,
        )
        subprocess.run(
            ["pnpm", "install"], cwd=str(pin_checkout), env=env,
            check=True, capture_output=True, text=True, timeout=600,
        )
        subprocess.run(
            ["pnpm", "run", "build"], cwd=str(pin_checkout), env=env,
            check=True, capture_output=True, text=True, timeout=600,
        )

        # Provision the profile in the person's DSH home.
        profile_name = f"graph-pin-{commit12}"
        bundle_path = pin_checkout / "packages" / "bundle" / "graph"
        from refactory_dsh.paths import dsh_command
        dsh_argv = dsh_command()
        subprocess.run(
            [*dsh_argv, "plugin", "--profile", profile_name, "add", str(bundle_path)],
            check=True, capture_output=True, text=True, timeout=120,
        )

        # Write pin.json as the LAST step: it is the readiness marker.
        # Its presence tells resolve_pin and the fast-path that the pin is
        # fully prepared and safe to use.
        pin_json = {
            "commit": commit,
            "created": datetime.now(timezone.utc).isoformat(),
            "driver_checkout": str(driver_checkout),
            "runs": [],
        }
        _atomic_write_json(pin_root / "pin.json", pin_json)

        return pin_checkout

    except BaseException:
        _cleanup_pin_dir(pin_root, driver_checkout)
        raise
    finally:
        os.close(lock_fd)


def _cleanup_pin_dir(pin_dir: Path, driver_checkout: Path) -> None:
    """Remove a pin directory and its git worktree registration."""
    if not pin_dir.exists():
        return
    checkout_in_dir = pin_dir / "checkout"
    if checkout_in_dir.exists():
        try:
            subprocess.run(
                ["git", "-C", str(driver_checkout), "worktree", "remove", "--force",
                 str(checkout_in_dir)],
                capture_output=True, text=True, timeout=60,
            )
        except (OSError, subprocess.SubprocessError):
            pass
    shutil.rmtree(pin_dir, ignore_errors=True)


# ── pin utilities ────────────────────────────────────────────────────────


def pin_python(pin_checkout: Path) -> Path:
    """The Python interpreter inside a pin's checkout."""
    return Path(pin_checkout) / ".venv" / "bin" / "python"


def register_run(key: str, run_dir: Path) -> None:
    """Record that ``run_dir`` uses the pin at ``key``.

    Uses a file lock to prevent concurrent writes from losing registrations.
    """
    pin_json_path = refactory_home() / "pins" / key / "pin.json"
    if not pin_json_path.exists():
        return
    lock_path = pin_json_path.with_suffix(".lock")
    lock_fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        data = json.loads(pin_json_path.read_text())
        runs = data.get("runs", [])
        run_str = str(Path(run_dir).resolve())
        if run_str not in runs:
            runs.append(run_str)
            data["runs"] = runs
            _atomic_write_json(pin_json_path, data)
    finally:
        os.close(lock_fd)


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
    """Remove pins no run refers to. Returns pruned commit SHAs.

    A run whose directory still exists is treated as resumable regardless of
    its status (done, failed, cancelled).  Only runs whose directories are
    gone are pruned from the pin's list.
    """
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

        # A run is live (and its pin must be kept) as long as the run
        # directory exists.  Failed and done runs are resumable unless
        # the directory is gone.
        live_runs: list[str] = []
        for run_path_str in runs:
            run_path = Path(run_path_str)
            if run_path.exists():
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

        # Remove the profile by its package name.
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
    """Compute a SHA-256 stamp of all TS source files across workspace packages.

    Covers both ``.ts`` and ``.tsx`` files.
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
            p for p in src_dir.rglob("*")
            if p.is_file() and p.suffix in (".ts", ".tsx")
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


def register_run_on_driver(checkout: Path, run_dir: Path) -> None:
    """Register a run as using the given driver checkout.

    Uses ``<refactory_home>/drivers/<checkout_hash>/runs.json`` under a file lock
    so that :func:`driver_live_runs` can refuse a rebuild while a run is active.
    """
    checkout_hash = hashlib.sha256(str(Path(checkout).resolve()).encode()).hexdigest()[:16]
    driver_meta = refactory_home() / "drivers" / checkout_hash
    driver_meta.mkdir(parents=True, exist_ok=True)
    lock_path = driver_meta / "runs.lock"
    runs_json = driver_meta / "runs.json"
    lock_fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        data: dict = {"checkout": str(checkout), "runs": []}
        if runs_json.exists():
            try:
                data = json.loads(runs_json.read_text())
            except (OSError, json.JSONDecodeError):
                pass
        run_str = str(Path(run_dir).resolve())
        runs = data.get("runs", [])
        if run_str not in runs:
            runs.append(run_str)
            data["runs"] = runs
            data["checkout"] = str(checkout)
            _atomic_write_json(runs_json, data)
    finally:
        os.close(lock_fd)


def driver_live_runs(checkout: Path) -> list[str]:
    """Return run directories registered on *checkout* that are currently live.

    A run is live when its invocation lock is held by another process.
    """
    from refactory_dsh.run_ownership import held as _run_held

    checkout_hash = hashlib.sha256(str(Path(checkout).resolve()).encode()).hexdigest()[:16]
    runs_json = refactory_home() / "drivers" / checkout_hash / "runs.json"
    if not runs_json.exists():
        return []
    try:
        data = json.loads(runs_json.read_text())
    except (OSError, json.JSONDecodeError):
        return []
    live: list[str] = []
    for run_path_str in data.get("runs", []):
        rp = Path(run_path_str)
        if rp.exists():
            try:
                if _run_held(rp):
                    live.append(run_path_str)
            except Exception:
                pass
    return live


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
