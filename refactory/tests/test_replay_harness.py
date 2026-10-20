"""R2 tests: the replay harness scores a verifier on evidence-log entries."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from refactory_dsh.replay_harness import ReplayResult, ReplaySummary, replay_all, replay_one

FIXTURES_PATH = Path(__file__).resolve().parent / "fixtures" / "evidence_log_entries.json"


def test_fixture_entries_have_required_fields():
    entries = json.loads(FIXTURES_PATH.read_text())
    assert len(entries) >= 3
    for entry in entries:
        for field in ("id", "brief", "gap_description", "method", "diff",
                      "expected_finding_pattern"):
            assert field in entry, f"entry {entry.get('id', '?')} missing {field}"


def test_replay_one_found():
    entry = {
        "id": "EL-001",
        "brief": "Add a --timeout flag to the CLI that caps how long a command runs",
        "gap_description": "negative timeout values are silently accepted",
        "method": "M1",
        "diff": "--- a/cli.py\n+++ b/cli.py\n@@ -10,0 +11,5 @@\n+    parser.add_argument('--timeout', type=int, default=30)\n",
        "expected_finding_pattern": "negative timeout",
    }

    def verifier(brief, diff):
        return ["BLOCKING: cli.py:11: negative timeout values accepted"]

    result = replay_one(entry, verifier)
    assert result.found is True
    assert result.noise_count == 0
    assert result.entry_id == "EL-001"
    assert result.method == "M1"


def test_replay_one_missed():
    entry = {
        "id": "EL-002",
        "brief": "Rename the export_data function to export_records everywhere",
        "gap_description": "a call site in utils/legacy.py still uses export_data",
        "method": "M3",
        "diff": "--- a/core.py\n+++ b/core.py\n@@ -5 +5 @@\n-def export_data(items):\n+def export_records(items):\n",
        "expected_finding_pattern": "export_data",
    }

    def verifier(brief, diff):
        return []  # missed the gap

    result = replay_one(entry, verifier)
    assert result.found is False
    assert result.noise_count == 0


def test_replay_one_noise():
    entry = {
        "id": "EL-001",
        "brief": "Add a --timeout flag",
        "gap_description": "negative timeout values",
        "method": "M1",
        "diff": "...",
        "expected_finding_pattern": "negative timeout",
    }

    def verifier(brief, diff):
        return [
            "BLOCKING: cli.py:11: negative timeout values accepted",
            "BLOCKING: cli.py:15: spurious noise finding",
        ]

    result = replay_one(entry, verifier)
    assert result.found is True
    assert result.noise_count == 1


def test_replay_all_counts():
    entries = json.loads(FIXTURES_PATH.read_text())

    def mock_verifier(brief, diff):
        if "timeout" in brief.lower():
            return [
                "BLOCKING: cli.py:11: negative timeout values accepted",
                "BLOCKING: cli.py:15: spurious noise finding",
            ]
        if "rename" in brief.lower():
            return ["BLOCKING: utils/legacy.py:3: still uses export_data"]
        return []  # miss

    summary = replay_all(entries, mock_verifier)
    assert summary.found == 2
    assert summary.missed == 1
    assert summary.noise == 1  # noise finding on entry 0
    assert summary.total_entries == 3


def test_per_method_breakdown():
    entries = json.loads(FIXTURES_PATH.read_text())

    def mock_verifier(brief, diff):
        if "timeout" in brief.lower():
            return ["BLOCKING: negative timeout"]
        return []

    summary = replay_all(entries, mock_verifier)
    assert "M1" in summary.per_method
    assert "M3" in summary.per_method
    assert "M13" in summary.per_method


def test_replay_summary_shape():
    entries = json.loads(FIXTURES_PATH.read_text())
    summary = replay_all(entries, lambda b, d: [])
    assert isinstance(summary, ReplaySummary)
    assert summary.total_entries == len(entries)
    assert summary.found + summary.missed == summary.total_entries
    assert len(summary.results) == summary.total_entries
    for r in summary.results:
        assert isinstance(r, ReplayResult)
