"""Test that boundary.json's references list contains exactly the expected entries.

This is independent of the completeness scan in test_boundary.py, which passes if
a code reference and its boundary entry both come back.  This exact-count test
catches re-introduction of removed references.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

_BOUNDARY = Path(__file__).resolve().parents[1] / "refactory_dsh" / "data" / "boundary.json"


def _load_references() -> list[dict]:
    boundary = json.loads(_BOUNDARY.read_text())
    return boundary["references"]


def test_references_exact():
    """boundary.json's references list contains exactly four entries."""
    refs = _load_references()
    assert len(refs) == 4, (
        f"expected exactly 4 references, got {len(refs)}:\n"
        + "\n".join(f"  {r['source']} -> {r['target']}" for r in refs)
    )

    # Assert exact source/target pairs
    pairs = {(r["source"], r["target"]) for r in refs}
    expected = {
        ("packages/bundle/graph/cordis.patch.yml", "refactory_dsh.console"),
        ("packages/workflow/workflow-spine/src/index.ts", "refactory_dsh.console"),
        ("refactory/refactory_dsh/invocations.py", "refactory_dsh.delivery"),
        ("refactory/refactory_dsh/registry.py", "refactory_packages"),
    }
    assert pairs == expected, (
        f"reference source/target mismatch.\n"
        f"  expected: {sorted(expected)}\n"
        f"  got: {sorted(pairs)}"
    )


def test_removed_references_do_not_return():
    """The three references removed by this PR must not reappear."""
    refs = _load_references()
    pairs = {(r["source"], r["target"]) for r in refs}

    removed = [
        ("refactory/refactory_dsh/lowering.py", "refactory_dsh.create_ops"),
        ("refactory/refactory_dsh/lowering.py", "refactory_dsh.fast_ops"),
        ("refactory/refactory_dsh/supervisor.py", "refactory_dsh.interactive"),
    ]
    for source, target in removed:
        assert (source, target) not in pairs, (
            f"removed reference {source} -> {target} has reappeared in boundary.json"
        )


def test_lowering_no_product_strings():
    """lowering.py must not contain any product module name strings."""
    lowering_path = Path(__file__).resolve().parents[1] / "refactory_dsh" / "lowering.py"
    source = lowering_path.read_text()
    forbidden = [
        "refactory_dsh.create_ops",
        "refactory_dsh.fast_ops",
    ]
    for s in forbidden:
        assert s not in source, (
            f"lowering.py still contains product string {s!r}"
        )


def test_supervisor_no_product_strings():
    """supervisor.py must not contain any product module name strings."""
    supervisor_path = Path(__file__).resolve().parents[1] / "refactory_dsh" / "supervisor.py"
    source = supervisor_path.read_text()
    # The old hardcoded module name must be gone
    assert "refactory_dsh.interactive" not in source, (
        "supervisor.py still contains product string 'refactory_dsh.interactive'"
    )
