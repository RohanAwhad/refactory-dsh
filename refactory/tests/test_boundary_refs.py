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
    """boundary.json's references list contains exactly three entries."""
    refs = _load_references()
    assert len(refs) == 3, (
        f"expected exactly 3 references, got {len(refs)}:\n"
        + "\n".join(f"  {r['source']} -> {r['target']}" for r in refs)
    )

    # Assert exact source/target pairs.  Each remaining reference and why:
    #   1. spine wf_open error message mentioning console open command (until the split)
    #   2. invocations.py DELIVER_ENTRY_POINTS names the product callable (until the split)
    #   3. registry discovery imports every refactory_packages.* (until the split)
    pairs = {(r["source"], r["target"]) for r in refs}
    expected = {
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
    """The four references removed by earlier PRs and this one must not reappear."""
    refs = _load_references()
    pairs = {(r["source"], r["target"]) for r in refs}

    removed = [
        ("refactory/refactory_dsh/lowering.py", "refactory_dsh.create_ops"),
        ("refactory/refactory_dsh/lowering.py", "refactory_dsh.fast_ops"),
        ("refactory/refactory_dsh/supervisor.py", "refactory_dsh.interactive"),
        ("packages/bundle/graph/cordis.patch.yml", "refactory_dsh.console"),
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
