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
                      "expected_finding_pattern", "pre_fix_commit"):
            assert field in entry, f"entry {entry.get('id', '?')} missing {field}"


def test_replay_one_found():
    entry = {
        "id": "2",
        "brief": "Make the design gate reject rule-site lines that have no [changed] or [left] marker",
        "gap_description": "A rule site with no marker counted as changed",
        "method": "M1",
        "pre_fix_commit": "918151f1e4",
        "diff": "--- a/refactory/refactory_dsh/design_ops.py\n+++ b/refactory/refactory_dsh/design_ops.py\n",
        "expected_finding_pattern": "marker",
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
        "diff": "--- a/refactory/refactory_dsh/interactive.py\n+++ b/refactory/refactory_dsh/interactive.py\n",
        "expected_finding_pattern": "empty",
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
        "diff": "...",
        "expected_finding_pattern": "marker",
    }

    def verifier(brief, diff, repo_path=None):
        return [
            "BLOCKING: design_ops.py:245: a site with no marker is accepted",
            "BLOCKING: design_ops.py:250: spurious noise finding",
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
                "BLOCKING: design_ops.py:250: spurious noise finding",
            ]
        if "base commit" in brief.lower():
            return ["BLOCKING: base_check.py:580: partial parse results accepted"]
        return []  # miss

    summary = replay_all(entries, mock_verifier)
    assert summary.found == 2
    assert summary.missed == 1
    assert summary.noise == 1  # noise finding on entry 2
    assert summary.total_entries == 3


def test_per_method_breakdown():
    entries = json.loads(FIXTURES_PATH.read_text())

    def mock_verifier(brief, diff, repo_path=None):
        if "design gate" in brief.lower():
            return ["BLOCKING: marker issue"]
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
