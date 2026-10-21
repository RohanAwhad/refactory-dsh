"""Product-side entry point for the ``refactory.feedback_refill`` entry-point group.

Re-derives missing feedback keys from persisted gate files or from prepare-time
initial values (``feedback-initial.json``).  Called by the core
``agent_delivery.fill_feedback`` function, which resolves this module through
``importlib.metadata.entry_points``.

The core (``agent_delivery``, ``worker_node``, ``nn.executor``) never imports this
module directly.  It reaches it only through the entry-point group, which keeps the
core/product import boundary intact.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable


def refill(workdir: Path, keys: Iterable[str], values: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Re-derive missing feedback keys from persisted files.

    For each declared key absent from *values*, attempts re-derivation in order:

    1. **Producing gate's persisted file** — for ``validation``: takes the newer of
       ``check-record.json`` and ``validation.txt`` (by file mtime).  For ``qa_gate``:
       ``reviews/qa-gate.txt``.  For ``overwatch_review``: ``reviews/overwatch-gate.txt``.
       For ``repair_feedback``: ``repair.txt``.
    2. **feedback-initial.json** (the prepare-time recorded values).
    3. **Refuse** with a message naming the missing key (only when both above are missing).
    """
    workdir = Path(workdir)
    updated = dict(values)
    refilled: list[str] = []

    initial_path = workdir / "feedback-initial.json"
    initial: dict[str, Any] = {}
    if initial_path.is_file():
        try:
            initial = json.loads(initial_path.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    _GATE_FILES: dict[str, list[Path]] = {
        "validation": [workdir / "check-record.json", workdir / "validation.txt"],
        "qa_gate": [workdir / "reviews" / "qa-gate.txt"],
        "overwatch_review": [workdir / "reviews" / "overwatch-gate.txt"],
        "repair_feedback": [workdir / "repair.txt"],
    }

    for key in keys:
        if key in updated and updated[key]:
            continue
        # 1. Try producing gate's file
        value = _derive_from_gate(workdir, key, _GATE_FILES.get(key, []))
        if value is not None:
            updated[key] = value
            refilled.append(key)
            continue
        # 2. Try feedback-initial.json
        if key in initial:
            updated[key] = initial[key]
            refilled.append(key)
            continue
        # 3. Refuse
        raise ValueError(
            f"feedback key {key!r} is unfilled and cannot be re-derived: no gate file "
            f"and no initial record exist. This typically happens when a redo clears "
            f"the fact before any gate has run."
        )

    return updated, refilled


def _derive_from_gate(workdir: Path, key: str, paths: list[Path]) -> str | None:
    """Try to derive a key's value from its producing gate's file(s)."""
    if key == "validation":
        return _derive_validation(workdir)
    for path in paths:
        if path.is_file():
            try:
                return path.read_text().strip() or None
            except OSError:
                pass
    return None


def _derive_validation(workdir: Path) -> str | None:
    """Re-derive validation from the newer of check-record.json and validation.txt."""
    cr_path = workdir / "check-record.json"
    vt_path = workdir / "validation.txt"

    cr_mtime = cr_path.stat().st_mtime if cr_path.is_file() else 0.0
    vt_mtime = vt_path.stat().st_mtime if vt_path.is_file() else 0.0

    if cr_mtime == 0.0 and vt_mtime == 0.0:
        return None

    if cr_mtime >= vt_mtime and cr_path.is_file():
        try:
            record = json.loads(cr_path.read_text())
            # Return the stored validation string directly
            stored = record.get("validation")
            if stored:
                return stored
        except (json.JSONDecodeError, OSError):
            pass

    if vt_path.is_file():
        try:
            text = vt_path.read_text().strip()
            if text:
                return text
        except OSError:
            pass

    return None
