"""Pin a runnable git worktree of the driver checkout so every run process executes it.

At launch, ``ensure_pinned()`` snapshots the driver's exact commit (or dirty state)
into ``~/.refactory/pinned/<key>/checkout`` with its own ``.venv``, ``node_modules``,
build, and DSH profiles.  ``reexec_if_needed()`` replaces the current process with
the pinned checkout's interpreter so that every ``__file__``, ``sys.executable``,
``REPO_ROOT`` and ``dsh_command()`` resolution points at the pinned copy.

A second run at the same commit reuses the existing pinned checkout.
``cleanup()`` removes pinned copies no live or resumable run refers to.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any


@dataclass
class PinnedCheckout:
    """The five values every caller needs after pinning."""
    key: str
    checkout_path: Path
    dsh_home: Path
    interpreter: str
    dirty: bool
    diff_hash: str
    untracked_included: bool


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _git(args: list[str], cwd: Path, **kwargs: Any) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, **kwargs,
    )


def _resolve_key(checkout_root: Path) -> tuple[str, bool, str, bool]:
    """Return (key, dirty, diff_hash, untracked_included)."""
    head = _git(["rev-parse", "HEAD"], cwd=checkout_root)
    head_sha = head.stdout.strip()
    if head.returncode != 0 or not head_sha:
        raise RuntimeError(f"cannot read HEAD of {checkout_root}: {head.stderr.strip()}")

    status = _git(["status", "--porcelain"], cwd=checkout_root)
    is_dirty = bool(status.stdout.strip())

    if not is_dirty:
        return head_sha, False, "", False

    # Dirty: create a synthetic commit capturing the working state.
    stash = _git(["stash", "create"], cwd=checkout_root)
    stash_sha = stash.stdout.strip()

    # Capture untracked files via a temporary index.
    untracked_included = False
    with tempfile.NamedTemporaryFile(prefix="pin-idx-", delete=False) as tmp_idx:
        tmp_idx_path = tmp_idx.name
    try:
        env_copy = {**os.environ, "GIT_INDEX_FILE": tmp_idx_path}
        # Start from HEAD so we get a full picture
        subprocess.run(
            ["git", "read-tree", "HEAD"], cwd=str(checkout_root),
            capture_output=True, text=True, env=env_copy,
        )
        subprocess.run(
            ["git", "add", "-A"], cwd=str(checkout_root),
            capture_output=True, text=True,
            env=env_copy,
        )
        tree_result = subprocess.run(
            ["git", "write-tree"], cwd=str(checkout_root),
            capture_output=True, text=True,
            env=env_copy,
        )
        if tree_result.returncode == 0 and tree_result.stdout.strip():
            untracked_tree = tree_result.stdout.strip()
            # Combine stash and untracked tree into a synthetic commit
            if stash_sha:
                commit_result = _git(
                    ["commit-tree", untracked_tree, "-p", stash_sha,
                     "-m", "pin: dirty state with untracked files"],
                    cwd=checkout_root,
                )
            else:
                commit_result = _git(
                    ["commit-tree", untracked_tree, "-p", head_sha,
                     "-m", "pin: dirty state with untracked files"],
                    cwd=checkout_root,
                )
            if commit_result.returncode == 0 and commit_result.stdout.strip():
                key = commit_result.stdout.strip()
                untracked_included = True
            elif stash_sha:
                key = stash_sha
            else:
                key = head_sha
        elif stash_sha:
            key = stash_sha
        else:
            key = head_sha
    finally:
        try:
            os.unlink(tmp_idx_path)
        except OSError:
            pass

    # Compute diff hash for recording
    diff_parts = []
    for diff_cmd in (["diff", "HEAD"], ["diff", "--cached"], ["ls-files", "--others", "--exclude-standard"]):
        r = _git(diff_cmd, cwd=checkout_root)
        if r.stdout:
            diff_parts.append(r.stdout)
    diff_text = "\n".join(diff_parts)
    diff_hash = hashlib.sha256(diff_text.encode()).hexdigest() if diff_text else ""

    return key, True, diff_hash, untracked_included


def _refactory_home(override: Path | None = None) -> Path:
    from refactory_dsh.paths import refactory_home
    return override or refactory_home()


_READY_MARKER = ".pin-ready"
# Bumped when the preparation logic changes in a way that invalidates
# previously-prepared checkouts (e.g. the rename-after-uv-sync fix).
# Old markers with a different or missing version trigger re-preparation.
_READY_VERSION = "2"


def _prepare_worktree(
    key: str,
    checkout_root: Path,
    pinned_dir: Path,
    dirty: bool,
) -> tuple[float, int]:
    """Create the pinned worktree and prepare it. Returns (seconds, disk_bytes).

    The worktree is created directly at its final location (``checkout/``)
    rather than a temporary name, because ``uv sync`` records the checkout
    path in the editable-install finder module.  Renaming the directory
    afterwards would leave the finder with stale paths and every ``import
    refactory_dsh`` under the pinned interpreter would fail with
    ``ModuleNotFoundError``.  The per-key lock in ``ensure_pinned`` and the
    ``.pin-ready`` marker provide the atomicity guarantee instead.
    """
    start = time.monotonic()
    checkout_dest = pinned_dir / "checkout"

    # Create temporary ref for the worktree
    ref_name = f"refs/refactory/pin/{key}"
    _git(["update-ref", ref_name, key], cwd=checkout_root).check_returncode()

    try:
        # Create worktree directly at final location
        result = _git(
            ["worktree", "add", "--detach", str(checkout_dest), key],
            cwd=checkout_root,
        )
        if result.returncode != 0:
            raise RuntimeError(f"git worktree add failed: {result.stderr.strip()}")

        # If dirty, we need to apply the dirty state
        if dirty:
            # Reset to the synthetic commit's tree
            _git(["checkout", "--force", key], cwd=checkout_dest)

        # Prepare the venv
        _prepare_venv(checkout_dest)

        # Prepare node_modules and build
        _prepare_node(checkout_dest)

        # Provision DSH profiles
        dsh_home_dir = pinned_dir / "dsh_home"
        _provision_profiles(checkout_dest, dsh_home_dir)

        # Link credentials
        _link_credentials(dsh_home_dir)

        # Write ready marker
        (pinned_dir / _READY_MARKER).write_text(_READY_VERSION)
    finally:
        # Delete temporary ref
        _git(["update-ref", "-d", ref_name], cwd=checkout_root)

    elapsed = time.monotonic() - start
    # Measure disk usage
    try:
        du = subprocess.run(
            ["du", "-sb", str(pinned_dir)], capture_output=True, text=True,
        )
        disk_bytes = int(du.stdout.split()[0]) if du.returncode == 0 else 0
    except (OSError, ValueError, IndexError):
        disk_bytes = 0

    return elapsed, disk_bytes


def _prepare_venv(checkout: Path) -> None:
    """Create a venv and install the package in the pinned checkout."""
    venv_dir = checkout / ".venv"
    # Use uv sync if pyproject.toml exists, else python -m venv
    if (checkout / "pyproject.toml").exists():
        result = subprocess.run(
            ["uv", "sync", "--frozen"],
            cwd=str(checkout), capture_output=True, text=True,
            timeout=600,
        )
        if result.returncode != 0:
            # Fallback: try without --frozen
            result = subprocess.run(
                ["uv", "sync"],
                cwd=str(checkout), capture_output=True, text=True,
                timeout=600,
            )
            if result.returncode != 0:
                raise RuntimeError(f"uv sync failed in pinned checkout: {result.stderr.strip()}")
    else:
        subprocess.run(
            [sys.executable, "-m", "venv", str(venv_dir)],
            check=True, capture_output=True, text=True,
        )


def _prepare_node(checkout: Path) -> None:
    """Install node dependencies and build if applicable."""
    if not (checkout / "package.json").exists():
        return
    pnpm = shutil.which("pnpm")
    if pnpm is None:
        return
    result = subprocess.run(
        [pnpm, "install", "--frozen-lockfile"],
        cwd=str(checkout), capture_output=True, text=True,
        timeout=600,
    )
    if result.returncode != 0:
        # Try without frozen lockfile
        subprocess.run(
            [pnpm, "install"],
            cwd=str(checkout), capture_output=True, text=True,
            timeout=600,
        )
    # Build
    subprocess.run(
        [pnpm, "run", "build"],
        cwd=str(checkout), capture_output=True, text=True,
        timeout=600,
    )


def _provision_profiles(checkout: Path, dsh_home_dir: Path) -> None:
    """Provision DSH profiles into the pinned dsh_home."""
    dsh_home_dir.mkdir(parents=True, exist_ok=True)
    # Import dsh_profile from the pinned checkout if possible,
    # otherwise use the current one
    try:
        from refactory_dsh import dsh_profile
        steps = dsh_profile.provision_plan()
    except Exception:
        return

    for step in steps:
        try:
            env = {**os.environ, "DSH_HOME": str(dsh_home_dir)}
            bundle_root = checkout / "packages" / "bundle"
            if step.kind == "init":
                dsh_bin = shutil.which("dsh") or str(checkout / "node_modules" / ".bin" / "dsh")
                subprocess.run(
                    [dsh_bin, "--profile", step.profile,
                     "--from-default-profile", step.value, "--dump-config"],
                    env=env, capture_output=True, text=True, timeout=120,
                )
            elif step.kind == "add":
                dsh_bin = shutil.which("dsh") or str(checkout / "node_modules" / ".bin" / "dsh")
                bundle_path = bundle_root / step.value
                subprocess.run(
                    [dsh_bin, "plugin", "--profile", step.profile,
                     "add", str(bundle_path)],
                    env=env, capture_output=True, text=True, timeout=120,
                )
            elif step.kind == "remove":
                dsh_bin = shutil.which("dsh") or str(checkout / "node_modules" / ".bin" / "dsh")
                subprocess.run(
                    [dsh_bin, "plugin", "--profile", step.profile,
                     "remove", *step.value.split()],
                    env=env, capture_output=True, text=True, timeout=120,
                )
        except Exception:
            pass  # Best-effort provisioning


def _link_credentials(dsh_home_dir: Path) -> None:
    """Symlink credentials from the machine's DSH_HOME."""
    try:
        from refactory_dsh.dsh_profile import dsh_home
        machine_home = dsh_home()
    except Exception:
        machine_home = Path.home() / ".dsh"

    for name in (".credentials.yaml", ".dsh-credentials"):
        source = machine_home / name
        target = dsh_home_dir / name
        if source.exists() and not target.exists():
            try:
                target.symlink_to(source)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def ensure_pinned(
    checkout_root: Path,
    *,
    refactory_home: Path | None = None,
) -> PinnedCheckout:
    """The single entry point for launch pinning.

    Creates or reuses a pinned worktree of *checkout_root* under
    ``~/.refactory/pinned/<key>/``.  Returns a :class:`PinnedCheckout` with
    everything the caller needs to re-exec under the pinned interpreter.
    """
    home = _refactory_home(refactory_home)
    key, dirty, diff_hash, untracked_included = _resolve_key(checkout_root)

    pinned_dir = home / "pinned" / key
    checkout_dest = pinned_dir / "checkout"

    # Atomic, serialized preparation per key
    pinned_dir.mkdir(parents=True, exist_ok=True)
    lock_path = pinned_dir / ".prepare.lock"
    lock_fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        ready_marker = pinned_dir / _READY_MARKER
        _marker_valid = (
            ready_marker.exists()
            and ready_marker.read_text().strip() == _READY_VERSION
        )
        if not _marker_valid:
            # Clean up any failed or outdated previous preparation
            if ready_marker.exists():
                ready_marker.unlink(missing_ok=True)
            if checkout_dest.exists():
                try:
                    _git(["worktree", "remove", "--force", str(checkout_dest)],
                         cwd=checkout_root)
                except Exception:
                    shutil.rmtree(checkout_dest, ignore_errors=True)

            elapsed, disk_bytes = _prepare_worktree(key, checkout_root, pinned_dir, dirty)

            # Write pin.json
            pin_meta: dict[str, Any] = {
                "key": key,
                "source_repo": str(checkout_root.resolve()),
                "dirty": dirty,
                "diff_hash": diff_hash,
                "untracked_included": untracked_included,
                "prepared_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "first_pin_seconds": round(elapsed, 2),
                "disk_bytes": disk_bytes,
                "runs": [],
            }
            (pinned_dir / "pin.json").write_text(json.dumps(pin_meta, indent=2))
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)

    interpreter = str(checkout_dest / ".venv" / "bin" / "python")
    dsh_home_dir = pinned_dir / "dsh_home"

    return PinnedCheckout(
        key=key,
        checkout_path=checkout_dest,
        dsh_home=dsh_home_dir,
        interpreter=interpreter,
        dirty=dirty,
        diff_hash=diff_hash,
        untracked_included=untracked_included,
    )


def register_run(key: str, run_dir: Path, *, refactory_home: Path | None = None) -> None:
    """Record a run directory in the pin's ``pin.json`` so cleanup knows about it."""
    home = _refactory_home(refactory_home)
    pin_json = home / "pinned" / key / "pin.json"
    if not pin_json.exists():
        return
    try:
        data = json.loads(pin_json.read_text())
    except (OSError, json.JSONDecodeError):
        return
    run_str = str(Path(run_dir).resolve())
    runs = data.get("runs", [])
    if run_str not in runs:
        runs.append(run_str)
        data["runs"] = runs
        tmp = pin_json.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        tmp.replace(pin_json)


def read_pinned(run_dir: Path) -> PinnedCheckout | None:
    """Read pinned checkout metadata from a run's ``run-config.json``.

    Returns ``None`` for pre-pinning runs that have no pinned fields.
    """
    config_path = Path(run_dir) / ".refactory" / "run-config.json"
    if not config_path.exists():
        return None
    try:
        config = json.loads(config_path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    key = config.get("pinned_key")
    if not key:
        return None
    return PinnedCheckout(
        key=key,
        checkout_path=Path(config.get("pinned_checkout", "")),
        dsh_home=Path(config.get("pinned_dsh_home", "")),
        interpreter=config.get("pinned_interpreter", ""),
        dirty=config.get("pinned_dirty", False),
        diff_hash=config.get("pinned_diff_hash", ""),
        untracked_included=config.get("pinned_untracked_included", False),
    )


def _build_reexec_argv(pinned: PinnedCheckout) -> list[str]:
    """Build the re-exec argv using module invocation, not script paths.

    Uses ``sys.orig_argv`` to detect whether the process was started with
    ``-m module`` and rebuilds accordingly.  Falls back to ``-m`` form
    when the original invocation used a script path.
    """
    orig = getattr(sys, "orig_argv", None) or []
    # Find if -m was used in orig_argv
    module_name = None
    for i, arg in enumerate(orig):
        if arg == "-m" and i + 1 < len(orig):
            module_name = orig[i + 1]
            break

    if module_name:
        # Rebuild as: pinned_interpreter -m <module> <rest of args>
        # sys.argv[0] is the script path when -m is used; skip it and use
        # the original module name with the remaining args from sys.argv[1:]
        return [pinned.interpreter, "-m", module_name, *sys.argv[1:]]
    else:
        # Not started with -m: reconstruct as -m invocation if possible
        # Try to infer the module from sys.argv[0]
        script = sys.argv[0]
        # Common patterns: .../refactory_dsh/interactive.py -> refactory_dsh.interactive
        parts = Path(script).parts
        try:
            idx = parts.index("refactory_dsh")
            module_parts = list(parts[idx:])
            module_parts[-1] = module_parts[-1].removesuffix(".py")
            module_name = ".".join(module_parts)
            return [pinned.interpreter, "-m", module_name, *sys.argv[1:]]
        except (ValueError, IndexError):
            # Fallback: just use the script path but with pinned interpreter
            return [pinned.interpreter, *sys.argv]


def reexec_if_needed(pinned: PinnedCheckout) -> None:
    """Replace the process with the pinned interpreter when needed.

    If ``sys.executable`` already is the pinned interpreter, this is a no-op.
    Otherwise, ``os.execv`` replaces the process.  The function never returns
    in the re-exec case.

    Sets ``DSH_HOME`` in the environment before re-exec so every downstream
    process inherits the pinned DSH home.
    """
    current = str(Path(sys.executable).resolve())
    target = str(Path(pinned.interpreter).resolve())
    if current == target:
        return
    if not Path(pinned.interpreter).exists():
        return  # pinned interpreter not available; continue with current

    argv = _build_reexec_argv(pinned)
    os.environ["DSH_HOME"] = str(pinned.dsh_home)
    os.environ["REFACTORY_MODEL_DSH_HOME"] = str(pinned.dsh_home)
    os.execv(pinned.interpreter, argv)


def pinned_env(pinned: PinnedCheckout, base_env: dict[str, str] | None = None) -> dict[str, str]:
    """Build an environment dict with DSH_HOME set to the pinned value.

    For hosts that start children under the pinned interpreter without
    re-exec'ing themselves.
    """
    env = dict(base_env or os.environ)
    env["DSH_HOME"] = str(pinned.dsh_home)
    env["REFACTORY_MODEL_DSH_HOME"] = str(pinned.dsh_home)
    return env


def cleanup(*, refactory_home: Path | None = None) -> dict[str, list[str]]:
    """Remove pinned copies that no live or resumable run refers to.

    Never removes a copy that a live or resumable run's ``run-config.json``
    names.  Returns ``{"kept": [...], "removed": [...]}`` naming the keys.
    """
    from refactory_dsh import run_state

    home = _refactory_home(refactory_home)
    pinned_root = home / "pinned"
    if not pinned_root.is_dir():
        return {"kept": [], "removed": []}

    kept: list[str] = []
    removed: list[str] = []

    for pin_dir in sorted(pinned_root.iterdir()):
        if not pin_dir.is_dir():
            continue
        pin_json = pin_dir / "pin.json"
        if not pin_json.exists():
            continue
        try:
            data = json.loads(pin_json.read_text())
        except (OSError, json.JSONDecodeError):
            continue

        key = data.get("key", pin_dir.name)
        runs = data.get("runs", [])
        source_repo = data.get("source_repo", "")

        # Check if any run is live or resumable
        referenced = False
        for run_dir_str in runs:
            run_dir = Path(run_dir_str)
            if not run_dir.is_dir():
                continue
            # Check spine status
            status = run_state.spine_status(run_dir)
            if not status:
                # No spine status = might still be starting
                referenced = True
                break
            if not run_state.is_terminal(status):
                # Non-terminal includes: ready, executing, paused, halted,
                # waiting_user, refused — all are resumable
                referenced = True
                break

        if referenced:
            kept.append(key)
        else:
            # Remove the worktree and the pinned directory
            checkout_dest = pin_dir / "checkout"
            if checkout_dest.is_dir() and source_repo:
                try:
                    _git(["worktree", "remove", "--force", str(checkout_dest)],
                         cwd=Path(source_repo))
                    _git(["worktree", "prune"], cwd=Path(source_repo))
                except Exception:
                    pass
            shutil.rmtree(pin_dir, ignore_errors=True)
            removed.append(key)

    return {"kept": kept, "removed": removed}


def list_pins(*, refactory_home: Path | None = None) -> list[dict[str, Any]]:
    """List pinned keys, their runs, and their state."""
    home = _refactory_home(refactory_home)
    pinned_root = home / "pinned"
    if not pinned_root.is_dir():
        return []

    result: list[dict[str, Any]] = []
    for pin_dir in sorted(pinned_root.iterdir()):
        if not pin_dir.is_dir():
            continue
        pin_json = pin_dir / "pin.json"
        if not pin_json.exists():
            continue
        try:
            data = json.loads(pin_json.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        result.append(data)
    return result
