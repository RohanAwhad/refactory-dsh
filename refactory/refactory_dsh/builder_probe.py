"""Sandboxed builder probe: run named tests in an isolated throwaway clone.

The builder can test its candidate emission before making its final answer,
without touching the target, the run's worktree or the shared ``.git``.
Every isolation guarantee from dm-96 and #368 is preserved.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from refactory_dsh import sandbox, worktree
from refactory_dsh.worktree import (
    BRANCH_PREFIX, NO_HOOKS, COMMITTER, Worktree,
    _json, _write_json, _inside,
)
from refactory_dsh.check_digest import (
    build_probe_command, digest_check_output, format_digest,
)


@dataclass
class ProbeResult:
    status: str  # "pass", "fail", "timed_out", "refused", "error"
    digest: str = ""
    refusal: str = ""
    raw_tail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status, "digest": self.digest,
                "refusal": self.refusal, "raw_tail": self.raw_tail}


# ── emission matching ─────────────────────────────────────────────────────────

def _emission_matches_last(workdir: Path, emission_content: str) -> bool:
    """Whether the emission matches the last materialised one (the applied_to case).

    Uses the same three-part condition as _commit_emission:
    - head differs from base;
    - the digests are equal;
    - head^ equals attempt_on.
    """
    from refactory_dsh.repo_change import EMISSION
    found = worktree.recorded(workdir)
    if found is None:
        return False
    if found.head == found.base:
        return False
    last = _json(Path(workdir) / EMISSION)
    if not last:
        return False
    digest = hashlib.sha256(emission_content.encode()).hexdigest()
    if last.get("digest") != digest:
        return False
    try:
        parent = worktree.git(Path(found.path), "rev-parse", f"{found.head}^").strip()
    except (ValueError, subprocess.SubprocessError):
        return False
    return parent == last.get("attempt_on")


# ── apply emission ────────────────────────────────────────────────────────────

def _apply_emission_to_clone(clone_root: Path, files: dict[Path, str], deletes: tuple[Path, ...]) -> None:
    """Write the emission's files into a clone, with .git and symlink guards."""
    for relative in (*files, *deletes):
        if any(part.lower() == ".git" for part in relative.parts):
            raise worktree.AttemptRefused(
                f"{relative} is inside git's own state, not a file of the artifact; leave it out"
            )
        _inside(clone_root, relative)
    for relative in deletes:
        target = clone_root / relative
        if target.is_file():
            target.unlink()
    for relative, content in files.items():
        destination = _inside(clone_root, relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content)


# ── PATH stripping ────────────────────────────────────────────────────────────

def _strip_worktree_path(env: dict[str, str], run_worktree_path: str) -> dict[str, str]:
    """Strip the run worktree's .venv/bin and node_modules/.bin from PATH."""
    wt = Path(run_worktree_path)
    exclude = {str(wt / ".venv" / "bin"), str(wt / "node_modules" / ".bin")}
    entries = env.get("PATH", "").split(os.pathsep)
    cleaned = [e for e in entries if e not in exclude]
    return {**env, "PATH": os.pathsep.join(cleaned)}


# ── sweep ─────────────────────────────────────────────────────────────────────

def sweep_dead_probes(workdir: Path) -> None:
    """Kill orphaned probe processes and clean up their directories.

    Only sweeps probes whose owner.pid is dead.  A concurrent probe whose owner
    is still alive is left alone.
    """
    refactory_dir = Path(workdir) / ".refactory"
    if not refactory_dir.is_dir():
        return
    for entry in sorted(refactory_dir.iterdir()):
        if not entry.name.startswith("probe-") or not entry.is_dir():
            continue
        owner_file = entry / "owner.pid"
        if not owner_file.is_file():
            shutil.rmtree(entry, ignore_errors=True)
            continue
        try:
            owner_pid = int(owner_file.read_text().strip())
        except (ValueError, OSError):
            shutil.rmtree(entry, ignore_errors=True)
            continue
        if _pid_alive(owner_pid):
            continue
        # Owner is dead — kill any test process group, then clean up
        pgid_file = entry / "pgid"
        if pgid_file.is_file():
            try:
                pgid = int(pgid_file.read_text().strip())
                os.killpg(pgid, signal.SIGKILL)
            except (ValueError, OSError, ProcessLookupError):
                pass
        shutil.rmtree(entry, ignore_errors=True)

    # Sweep leaked slot/run-probe-*.json records
    slot_dir = refactory_dir / "slot"
    if slot_dir.is_dir():
        for slot_file in sorted(slot_dir.iterdir()):
            if not slot_file.name.startswith("run-probe-"):
                continue
            try:
                record = json.loads(slot_file.read_text())
                pid = record.get("owner_pid")
                if pid and not _pid_alive(int(pid)):
                    slot_file.unlink(missing_ok=True)
            except (json.JSONDecodeError, ValueError, OSError):
                slot_file.unlink(missing_ok=True)


def _pid_alive(pid: int) -> bool:
    """Whether a process with the given PID is still running."""
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False
    except OSError:
        return False


# ── prepared clone ────────────────────────────────────────────────────────────

@contextmanager
def prepared_clone(
    workdir: Path,
    *,
    emission: str | None = None,
    deadline: float,
    test_ids: list[str] | None = None,
) -> Iterator[tuple[Path, dict[str, str], Worktree]]:
    """Context manager: create an isolated throwaway clone, prepare it, yield it, clean up.

    Yields (clone_root, env, clone_found) where env has PATH, HOME etc set
    for running commands in the clone.
    """
    workdir = Path(workdir)
    probe_id = uuid.uuid4().hex[:12]
    probe_dir = workdir / ".refactory" / f"probe-{probe_id}"
    clone_dir = probe_dir / "repo"
    home_dir = probe_dir / "home"
    dsh_home_dir = probe_dir / "dsh_home"

    # Record owner PID
    probe_dir.mkdir(parents=True, exist_ok=True)
    (probe_dir / "owner.pid").write_text(str(os.getpid()))

    try:
        # Find the run worktree
        found = worktree.recorded(workdir)
        if found is None:
            raise ValueError("no worktree recorded for this run; probe requires a worktree")
        wt_path = Path(found.path)
        if not wt_path.is_dir():
            raise ValueError(f"run worktree {wt_path} does not exist")
        head = worktree.git(wt_path, "rev-parse", "HEAD").strip()
        if head != found.head:
            raise ValueError(
                f"worktree HEAD {head[:12]} differs from recorded {found.head[:12]}; "
                "refusing probe to avoid stale state"
            )

        # Handle repeated-emission case
        if emission is not None:
            emission_path = workdir / emission
            if emission_path.is_file():
                emission_content = emission_path.read_text()
                if _emission_matches_last(workdir, emission_content):
                    raise _RepeatedEmission(
                        "this emission has already been checked; "
                        "see check-record.json for the result"
                    )

        # Clone
        start = time.monotonic()
        target = found.target
        subprocess.run(
            ["git", "clone", "--no-local", "--depth", "1",
             "--branch", found.branch, target, str(clone_dir)],
            check=True, capture_output=True, timeout=min(60, deadline),
        )

        # Remove origin
        subprocess.run(
            ["git", "-C", str(clone_dir), "remote", "remove", "origin"],
            check=True, capture_output=True, timeout=10,
        )

        # Hooks off
        subprocess.run(
            ["git", "-C", str(clone_dir), "config", "core.hooksPath", "/dev/null"],
            check=True, capture_output=True, timeout=5,
        )

        # Apply emission if provided
        clone_base = worktree.git(clone_dir, "rev-parse", "HEAD").strip()
        clone_head = clone_base
        if emission is not None:
            emission_path = workdir / emission
            if emission_path.is_file():
                from refactory_dsh.repo_change import apply_emission
                emission_content = emission_path.read_text()
                files_dict, deletes, _rev, _repeated = apply_emission(emission_content, found, workdir)
                if files_dict or deletes:
                    _apply_emission_to_clone(clone_dir, files_dict, deletes)
                    # Stage and commit
                    all_paths = [str(p) for p in files_dict]
                    if all_paths:
                        worktree.git(clone_dir, "add", "-f", "--", *all_paths)
                    for d in deletes:
                        dp = clone_dir / d
                        if dp.exists():
                            worktree.git(clone_dir, "rm", "-q", "-f", "--", str(d))
                    worktree.git(
                        clone_dir, *NO_HOOKS, "-c", "commit.gpgsign=false",
                        "commit", "-q", "--no-verify", "--allow-empty",
                        "-m", "probe candidate",
                        env={**os.environ, **COMMITTER},
                    )
                    clone_head = worktree.git(clone_dir, "rev-parse", "HEAD").strip()

        # Create synthetic worktree record
        probe_branch = f"{BRANCH_PREFIX}probe-{probe_id}"
        clone_found = Worktree(
            target=target,
            path=str(clone_dir),
            branch=probe_branch,
            base=clone_base,
            head=clone_head,
        )
        slot_dir = workdir / ".refactory" / "slot"
        slot_dir.mkdir(parents=True, exist_ok=True)
        record_path = slot_dir / f"run-probe-{probe_id}.json"
        from dataclasses import asdict
        _write_json(record_path, {**asdict(clone_found), "owner_pid": os.getpid()})

        # Prepare environment using the factored helper
        home_dir.mkdir(parents=True, exist_ok=True)
        dsh_home_dir.mkdir(parents=True, exist_ok=True)

        elapsed = time.monotonic() - start
        remaining = deadline - elapsed
        if remaining <= 0:
            raise TimeoutError("probe deadline exceeded during clone")

        from refactory_dsh.repo_change import prepare_check_environment
        from refactory_dsh.run import _read_facts
        facts = _read_facts(workdir)

        clone_found, env, problems, prep_fail = prepare_check_environment(
            clone_found, facts,
            amend=False, commit_lockfiles=False,
            run=workdir, home=home_dir, timeout=remaining,
        )
        if problems:
            raise ValueError(f"probe environment preparation failed: {'; '.join(problems)}")

        # Strip run worktree paths and set probe-specific env vars
        env = _strip_worktree_path(env, found.path)
        env["DSH_HOME"] = str(dsh_home_dir)
        env["LEFTHOOK"] = "0"
        env["HUSKY"] = "0"

        yield clone_dir, env, clone_found

    except _RepeatedEmission as e:
        raise
    finally:
        # Clean up: remove probe directory
        if probe_dir.is_dir():
            shutil.rmtree(probe_dir, ignore_errors=True)
        # Clean up slot record
        slot_path = workdir / ".refactory" / "slot" / f"run-probe-{probe_id}.json"
        slot_path.unlink(missing_ok=True)


class _RepeatedEmission(Exception):
    """The emission matches the last materialised one."""


class _FactsProxy:
    """Minimal proxy to pass facts to prepare_commands (which reads ctx.facts)."""
    def __init__(self, facts: dict[str, Any]) -> None:
        self.facts = facts

    def knob(self, name: str, default: Any = None) -> Any:
        return default


# ── run_probe ─────────────────────────────────────────────────────────────────

def run_probe(
    workdir: Path,
    emission_path: str,
    test_ids: list[str],
    timeout: float = 90,
) -> ProbeResult:
    """Run named tests on the candidate emission in an isolated clone.

    Returns a ProbeResult with a digest of the output (not raw stdout).
    """
    workdir = Path(workdir)

    # Build the targeted command
    facts_path = workdir / "facts.json"
    if facts_path.is_file():
        facts = json.loads(facts_path.read_text())
    else:
        facts = {}
    check_command = str(facts.get("check") or "")
    if not check_command:
        return ProbeResult(status="error", refusal="no check command found in facts")

    command, refusal = build_probe_command(check_command, test_ids)
    if refusal:
        return ProbeResult(status="refused", refusal=refusal)

    deadline = timeout
    try:
        with prepared_clone(workdir, emission=emission_path, deadline=deadline, test_ids=test_ids) as (clone_root, env, clone_found):
            remaining = max(10, deadline - 30)  # reserve some time for cleanup
            # Record process group
            probe_dir = clone_root.parent
            result = sandbox.run_command(
                command, workdir=clone_root, timeout_s=remaining,
                env=env, home=Path(env.get("HOME", str(probe_dir / "home"))),
                exact_env=True,
            )
            output = "\n".join(filter(None, [result.stdout, result.stderr]))
            if result.timed_out:
                return ProbeResult(
                    status="timed_out",
                    digest="the probe timed out before the tests finished",
                    raw_tail=output[-500:] if output else "",
                )
            digest = digest_check_output(output)
            formatted = format_digest(digest)
            return ProbeResult(
                status="pass" if result.ok else "fail",
                digest=formatted[:2000],
                raw_tail=output[-500:] if output else "",
            )
    except _RepeatedEmission as e:
        return ProbeResult(status="refused", refusal=str(e))
    except worktree.AttemptRefused as e:
        return ProbeResult(status="refused", refusal=str(e))
    except TimeoutError as e:
        return ProbeResult(status="timed_out", digest=str(e))
    except Exception as e:
        return ProbeResult(status="error", refusal=f"{type(e).__name__}: {e}")


# ── CLI entry point ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Sandboxed builder probe")
    parser.add_argument("--workdir", required=True, help="Run directory (absolute)")
    parser.add_argument("--emission", default="emit.txt", help="Emission file name")
    parser.add_argument("--test", nargs="+", required=True, dest="test_ids", help="Test IDs to run")
    parser.add_argument("--timeout", type=float, default=90, help="Timeout in seconds")

    args = parser.parse_args()
    result = run_probe(
        Path(args.workdir),
        args.emission,
        args.test_ids,
        timeout=args.timeout,
    )
    print(json.dumps(result.to_dict()))
