"""Fail while the tracked work list still has unfinished items.

This exists because the agent driving this work repeatedly stopped with items
outstanding. A list in a prompt is advice; a failing test is a gate. Any run of
the suite reports the remaining work, and the work is only done when this passes.

It is deliberately part of the normal suite rather than a separate command, so it
cannot be forgotten, and it names each outstanding item so the failure is
actionable rather than a count.

Every item must also name its issue and the command that verifies it, and a done
item must record the evidence observed and the commit. That is what stops "done"
from meaning "nearly done, described optimistically".
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

TODO = Path(__file__).resolve().parent.parent / ".work" / "todo.json"

#: The test runners a verify command may use. `pytest` covers the Python side;
#: `pnpm`/`vitest` cover the DSH side, which is where GUI work verifies.
RUNNERS = frozenset({"pytest", "pnpm", "vitest", "npx"})


def _items() -> list[dict]:
    return json.loads(TODO.read_text())["items"]


def test_the_work_list_exists_and_is_well_formed() -> None:
    items = _items()
    assert items, "the work list is empty; that is not the same as finished"
    for item in items:
        assert item["status"] in {"todo", "doing", "done", "deferred"}, item
        assert item["title"], item


def test_every_item_names_its_issue() -> None:
    for item in _items():
        assert isinstance(item.get("issue"), int) and item["issue"] > 0, (
            f"{item['id']} does not name the issue it implements"
        )


def test_every_item_names_the_command_that_verifies_it() -> None:
    for item in _items():
        assert item.get("verify", "").strip(), (
            f"{item['id']} does not say how it is verified, so nothing can check it"
        )


def test_every_verify_command_names_files_that_exist() -> None:
    """A recorded command that cannot run is not evidence.

    Two items named `test_edit.py` and `test_design.py`, which never existed —
    the tests live in `test_edit_design.py`. Nothing caught it, because the gate
    only required the field to be non-empty. Reading the paths back is what makes
    "the command that verifies it" a claim about this repository.
    """
    # The recorded commands are relative to the repository root, which holds
    # `refactory/`; TODO lives at `<repo>/refactory/.work/todo.json`.
    root = TODO.parent.parent.parent
    for item in _items():
        words = item["verify"].split()
        # A GUI item verifies with vitest, a Python item with pytest. What matters
        # is that the runner is one of them and that the files it names exist.
        assert words[0] in RUNNERS, (
            f"{item['id']}: verify must run {sorted(RUNNERS)}, not {words[0]!r}"
        )
        paths = [
            word
            for word in words[1:]
            if not word.startswith("-") and "/" in word and "." in word.rsplit("/", 1)[-1]
        ]
        assert paths, f"{item['id']}: verify names no test file"
        for path in paths:
            assert (root / path).exists(), (
                f"{item['id']}: verify names {path}, which does not exist"
            )


def test_a_done_item_records_the_evidence_and_the_commit() -> None:
    """Done means a command was run and its result was seen."""
    for item in _items():
        if item["status"] != "done":
            continue
        assert item.get("evidence", "").strip(), (
            f"{item['id']} is marked done with no recorded evidence"
        )
        assert item.get("commit", "").strip(), (
            f"{item['id']} is marked done with no commit recording the change"
        )


def test_a_deferred_item_says_why_and_where_it_went() -> None:
    """`deferred` is not a way to stop working; it is a hand-off.

    Work that cannot be finished in this session — it needs a capability the
    session does not have — has to leave a reason and a destination, or
    deferring becomes a quieter way to abandon something.
    """
    for item in _items():
        if item["status"] != "deferred":
            continue
        assert item.get("deferred_reason", "").strip(), (
            f"{item['id']} is deferred with no reason"
        )
        assert item.get("deferred_to", "").strip(), (
            f"{item['id']} is deferred with nowhere to go: name the issue or session "
            "that will pick it up"
        )


def test_every_tracked_item_is_done() -> None:
    outstanding = [
        item for item in _items() if item["status"] not in {"done", "deferred"}
    ]
    if outstanding:
        detail = "\n".join(
            f"  [{i['status']:5s}] #{i['issue']} {i['id']}: {i['title']}"
            for i in outstanding
        )
        pytest.fail(f"{len(outstanding)} tracked item(s) still open:\n{detail}")


def test_nothing_is_left_in_progress() -> None:
    """'doing' is a transient state, not a resting place."""
    doing = [i["id"] for i in _items() if i["status"] == "doing"]
    assert not doing, f"items left mid-flight: {doing}"
