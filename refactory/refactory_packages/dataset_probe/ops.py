from __future__ import annotations

from typing import Any


def record(ctx: Any) -> dict[str, Any]:
    """Append the current item's id, the way a per-item worker would.

    The run's own `min_items` travels with the record, because the Task's check reads
    what the pass wrote: a verdict has to be able to see the thing it is judging.
    """
    item = ctx.read_json("current_item.json", {}) or {}
    seen = ctx.read_json("seen.json", {"ids": []}) or {"ids": []}
    seen.setdefault("ids", []).append(str(item.get("id")))
    seen["min_items"] = int(ctx.knob("min_items", 1) or 1)
    ctx.write_json("seen.json", seen)
    return {"value": len(seen["ids"])}


def done(ctx: Any) -> dict[str, Any]:
    return {"finished": True}
