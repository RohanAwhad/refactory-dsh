"""Replay harness: score a verifier against the evidence log.

For each entry with a pre-fix commit, run the verifier with only that entry's
brief, diff and repository, and report how many known gaps it finds and how many
findings were noise.

Usage:
    python -m refactory_dsh.replay_harness [--evidence PATH] [--output PATH] [--repo PATH]

A post-build verifier does not cover gaps found at a design gate (which have no
diff to verify).
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


@dataclass
class ReplayResult:
    entry_id: str
    method: str
    found: bool          # True if the verifier identified the known gap
    noise_count: int     # Number of findings that don't match the known gap
    findings: list[str]  # Raw findings from the verifier


@dataclass
class ReplaySummary:
    total_entries: int
    found: int           # Entries whose gap the verifier identified
    missed: int          # Entries whose gap the verifier did not identify
    noise: int           # Total noise findings across all entries
    per_method: dict[str, dict[str, int]] = field(default_factory=dict)
    results: list[ReplayResult] = field(default_factory=list)


def replay_one(entry: dict, verifier_fn: Callable) -> ReplayResult:
    """Replay one evidence-log entry through the verifier.

    ``entry`` must have: id, brief, gap_description, method, diff,
    expected_finding_pattern.

    ``verifier_fn(brief, diff, repo_path) -> list[str]`` returns blocking findings.
    ``repo_path`` is the path to a worktree checked out at the pre-fix commit,
    or None when no worktree is available (e.g. in unit tests).

    Scoring: ``found`` is True when any finding contains the
    ``expected_finding_pattern`` (case-insensitive substring match).
    ``noise_count`` counts findings that do NOT match the pattern.
    """
    findings = verifier_fn(entry["brief"], entry["diff"], entry.get("repo_path"))
    pattern = entry["expected_finding_pattern"].lower()
    matched = any(pattern in f.lower() for f in findings)
    noise = sum(1 for f in findings if pattern not in f.lower())
    return ReplayResult(
        entry_id=entry["id"],
        method=entry["method"],
        found=matched,
        noise_count=noise,
        findings=findings,
    )


def replay_all(entries: list[dict], verifier_fn: Callable) -> ReplaySummary:
    """Replay all entries and aggregate into a summary."""
    results: list[ReplayResult] = []
    per_method: dict[str, dict[str, int]] = {}

    for entry in entries:
        result = replay_one(entry, verifier_fn)
        results.append(result)
        method = result.method
        if method not in per_method:
            per_method[method] = {"found": 0, "missed": 0, "noise": 0}
        if result.found:
            per_method[method]["found"] += 1
        else:
            per_method[method]["missed"] += 1
        per_method[method]["noise"] += result.noise_count

    found = sum(1 for r in results if r.found)
    missed = sum(1 for r in results if not r.found)
    noise = sum(r.noise_count for r in results)

    return ReplaySummary(
        total_entries=len(entries),
        found=found,
        missed=missed,
        noise=noise,
        per_method=per_method,
        results=results,
    )


if __name__ == "__main__":
    import argparse
    import subprocess
    import tempfile

    parser = argparse.ArgumentParser(
        prog="python -m refactory_dsh.replay_harness",
        description="Score a verifier against the evidence log.",
    )
    parser.add_argument("--evidence", default="evidence_log_entries.json",
                        help="path to the evidence log entries JSON file")
    parser.add_argument("--output", default="verifier_baseline.json",
                        help="path to write the summary output")
    parser.add_argument("--repo", default=".",
                        help="repository to create worktrees from pre-fix commits")
    args = parser.parse_args()

    entries = json.loads(Path(args.evidence).read_text())
    replayable = [e for e in entries if e.get("pre_fix_commit")]
    skipped = [e for e in entries if not e.get("pre_fix_commit")]
    print(f"Loaded {len(entries)} entries; {len(replayable)} replayable, "
          f"{len(skipped)} skipped (no pre_fix_commit)")

    repo = Path(args.repo).resolve()
    worktrees_created: list[Path] = []

    def _create_worktree(commit: str) -> Path | None:
        """Create a temporary git worktree at the given commit."""
        try:
            tmpdir = tempfile.mkdtemp(prefix="replay-wt-")
            subprocess.run(
                ["git", "-C", str(repo), "worktree", "add", "--detach",
                 tmpdir, commit],
                check=True, capture_output=True, text=True,
            )
            worktrees_created.append(Path(tmpdir))
            return Path(tmpdir)
        except subprocess.CalledProcessError:
            return None

    def _remove_worktrees() -> None:
        """Remove all temporary git worktrees."""
        for wt in worktrees_created:
            try:
                subprocess.run(
                    ["git", "-C", str(repo), "worktree", "remove", "--force",
                     str(wt)],
                    capture_output=True, text=True,
                )
            except (subprocess.CalledProcessError, OSError):
                pass

    # Set up worktrees for replayable entries
    for entry in replayable:
        commit = entry["pre_fix_commit"]
        wt = _create_worktree(commit)
        if wt is None:
            print(f"  {entry['id']}: commit {commit} is not reachable, skipping")
            entry["repo_path"] = None
        else:
            entry["repo_path"] = str(wt)

    # Default stub verifier for CLI use; real usage supplies a model-backed fn
    def stub_verifier(brief: str, diff: str, repo_path: str | None = None) -> list[str]:
        return []

    try:
        summary = replay_all(replayable, stub_verifier)
    finally:
        _remove_worktrees()

    output = {
        "total_entries": summary.total_entries,
        "found": summary.found,
        "missed": summary.missed,
        "noise": summary.noise,
        "per_method": summary.per_method,
    }
    Path(args.output).write_text(json.dumps(output, indent=2) + "\n")
    print(f"Wrote summary to {args.output}: found={summary.found}, "
          f"missed={summary.missed}, noise={summary.noise}")
