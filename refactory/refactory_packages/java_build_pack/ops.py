"""Operations for the Java build harness: the deterministic half of one pass."""

from __future__ import annotations

from typing import Any


def read_item(ctx: Any) -> dict[str, Any]:
    """Publish the current item's entry point, so the build agent's prompt can name it.

    The item travels on disk as `current_item.json`. Only the agent node's prompt is
    rendered from facts, so this node is what turns the item into the `main_class` and
    `main_args` the prompt interpolates.
    """
    item = ctx.read_json("current_item.json", {}) or {}
    facts = {
        "item_id": str(item.get("id") or ""),
        "main_class": str(item.get("main_class") or ""),
        "main_args": str(item.get("main_args") or ""),
    }
    ctx.write_json("item.json", facts)
    return facts


def finish(ctx: Any) -> dict[str, Any]:
    """Record that this run produced a build script, so the run has a terminal fact."""
    script = ctx.path("build.sh")
    present = script.exists()
    ctx.write_json("build_record.json", {
        "script": "build.sh",
        "present": present,
        "bytes": script.stat().st_size if present else 0,
    })
    return {"built": present}
