"""R2 tests: the replay harness scores a verifier on evidence-log entries."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from refactory_dsh.replay_harness import (
    ReplayResult, ReplaySummary, _finding_matches, replay_all, replay_one,
)

FIXTURES_PATH = Path(__file__).resolve().parent / "fixtures" / "evidence_log_entries.json"


def test_fixture_entries_have_required_fields():
    entries = json.loads(FIXTURES_PATH.read_text())
    assert len(entries) >= 3
    for entry in entries:
        for field in ("id", "brief", "gap_description", "method",
                      "pre_fix_commit", "expected_finding_file",
                      "expected_finding_terms"):
            assert field in entry, f"entry {entry.get('id', '?')} missing {field}"
        assert isinstance(entry["expected_finding_terms"], list)
        assert len(entry["expected_finding_terms"]) >= 1


def test_finding_matches_file_and_term():
    entry = {
        "expected_finding_file": "design_ops.py",
        "expected_finding_terms": ["marker", "unchanged"],
    }
    assert _finding_matches("BLOCKING: design_ops.py:245: unmarked marker sites", entry)
    assert not _finding_matches("BLOCKING: other.py:10: marker issue", entry)
    assert not _finding_matches("BLOCKING: design_ops.py:245: unrelated issue", entry)


def test_finding_matches_case_insensitive():
    entry = {
        "expected_finding_file": "Base_Check.py",
        "expected_finding_terms": ["Partial"],
    }
    assert _finding_matches("BLOCKING: base_check.py:580: partial parse accepted", entry)


def test_replay_one_found():
    entry = {
        "id": "2",
        "brief": "Make the design gate reject rule-site lines that have no [changed] or [left] marker",
        "gap_description": "A rule site with no marker counted as changed",
        "method": "M1",
        "pre_fix_commit": "918151f1e4",
        "expected_finding_file": "design_ops.py",
        "expected_finding_terms": ["marker", "unchanged"],
    }

    def verifier(brief, diff, repo_path=None):
        return ["BLOCKING: design_ops.py:245: a site with no marker is accepted"]

    result = replay_one(entry, verifier)
    assert result.found is True
    assert result.noise_count == 0
    assert result.entry_id == "2"
    assert result.method == "M1"


def test_replay_one_missed():
    entry = {
        "id": "21",
        "brief": "A mode that declares a required brief must refuse to start when the brief is missing or empty",
        "gap_description": "Run start accepted empty string as a valid brief",
        "method": "M13",
        "pre_fix_commit": "fcb2517800",
        "expected_finding_file": "interactive.py",
        "expected_finding_terms": ["empty", "blank", "missing"],
    }

    def verifier(brief, diff, repo_path=None):
        return []  # missed the gap

    result = replay_one(entry, verifier)
    assert result.found is False
    assert result.noise_count == 0


def test_replay_one_noise():
    entry = {
        "id": "2",
        "brief": "Make the design gate reject rule-site lines that have no [changed] or [left] marker",
        "gap_description": "unmarked sites accepted",
        "method": "M1",
        "pre_fix_commit": "918151f1e4",
        "expected_finding_file": "design_ops.py",
        "expected_finding_terms": ["marker", "unchanged"],
    }

    def verifier(brief, diff, repo_path=None):
        return [
            "BLOCKING: design_ops.py:245: a site with no marker is accepted",
            "BLOCKING: other.py:250: spurious noise finding",
        ]

    result = replay_one(entry, verifier)
    assert result.found is True
    assert result.noise_count == 1


def test_replay_all_counts():
    entries = json.loads(FIXTURES_PATH.read_text())

    def mock_verifier(brief, diff, repo_path=None):
        if "design gate" in brief.lower():
            return [
                "BLOCKING: design_ops.py:245: a site with no marker is accepted",
                "BLOCKING: other.py:250: spurious noise finding",
            ]
        if "base commit" in brief.lower():
            return ["BLOCKING: base_check.py:580: partial parse results accepted"]
        return []  # miss

    summary = replay_all(entries, mock_verifier)
    assert summary.found == 2
    assert summary.missed == 1
    assert summary.noise == 1  # noise finding on entry 2
    assert summary.total_entries == 3


def test_replay_all_skips_unreachable():
    """Entries with repo_path=None are skipped, not replayed."""
    entries = [
        {"id": "1", "brief": "x", "method": "M1", "pre_fix_commit": "abc",
         "repo_path": None,
         "expected_finding_file": "f.py", "expected_finding_terms": ["x"]},
        {"id": "2", "brief": "y", "method": "M3",
         "expected_finding_file": "g.py", "expected_finding_terms": ["y"]},
    ]

    call_count = [0]
    def verifier(brief, diff, repo_path=None):
        call_count[0] += 1
        return ["BLOCKING: g.py:1: y found"]

    summary = replay_all(entries, verifier)
    assert summary.skipped == 1
    assert call_count[0] == 1, "unreachable entry must not be replayed"
    assert summary.found == 1
    assert summary.missed == 0


def test_skipped_entries_appear_in_summary():
    """Skipped entries must appear in summary.results with skipped=True."""
    entries = [
        {"id": "skip1", "brief": "x", "method": "M1", "pre_fix_commit": "abc",
         "repo_path": None,
         "expected_finding_file": "f.py", "expected_finding_terms": ["x"]},
    ]
    summary = replay_all(entries, lambda b, d, r=None: [])
    assert len(summary.results) == 1
    assert summary.results[0].skipped is True
    assert summary.results[0].entry_id == "skip1"


def test_per_method_breakdown():
    entries = json.loads(FIXTURES_PATH.read_text())

    def mock_verifier(brief, diff, repo_path=None):
        if "design gate" in brief.lower():
            return ["BLOCKING: design_ops.py:245: marker issue"]
        return []

    summary = replay_all(entries, mock_verifier)
    assert "M1" in summary.per_method
    assert "M13" in summary.per_method


def test_replay_summary_shape():
    entries = json.loads(FIXTURES_PATH.read_text())
    summary = replay_all(entries, lambda b, d, r=None: [])
    assert isinstance(summary, ReplaySummary)
    assert summary.total_entries == len(entries)
    assert summary.found + summary.missed == summary.total_entries
    assert len(summary.results) == summary.total_entries
    for r in summary.results:
        assert isinstance(r, ReplayResult)


def test_cli_requires_model():
    """The CLI must refuse to run without --model."""
    result = subprocess.run(
        [sys.executable, "-m", "refactory_dsh.replay_harness",
         "--evidence", str(FIXTURES_PATH)],
        capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "--model is required" in result.stderr
