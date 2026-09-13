"""Fail while the tracked work list still has unfinished items.

This exists because the agent driving this work repeatedly stopped with items
outstanding. A list in a prompt is advice; a failing test is a gate. Any run of
the suite reports the remaining work, and the work is only done when this passes.

It is deliberately part of the normal suite rather than a separate command, so it
cannot be forgotten, and it names each outstanding item so the failure is
actionable rather than a count.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

TODO = Path(__file__).resolve().parent.parent / ".work" / "todo.json"


def _items() -> list[dict]:
    return json.loads(TODO.read_text())["items"]


def test_the_work_list_exists_and_is_well_formed() -> None:
    items = _items()
    assert items, "the work list is empty; that is not the same as finished"
    for item in items:
        assert item["status"] in {"todo", "doing", "done"}, item
        assert item["title"], item


def test_every_tracked_item_is_done() -> None:
    outstanding = [i for i in _items() if i["status"] != "done"]
    if outstanding:
        detail = "\n".join(f"  [{i['status']:5s}] {i['id']}: {i['title']}" for i in outstanding)
        pytest.fail(f"{len(outstanding)} tracked item(s) still open:\n{detail}")


def test_nothing_is_left_in_progress() -> None:
    """'doing' is a transient state, not a resting place."""
    doing = [i["id"] for i in _items() if i["status"] == "doing"]
    assert not doing, f"items left mid-flight: {doing}"
