"""Operations the executor tests resolve by name.

They live here rather than in the library because they exist to exercise the
executor, not to be an operation any real mode would call.
"""

from __future__ import annotations

from typing import Any


def write_number(ctx: Any) -> dict[str, Any]:
    value = int(ctx.knob("step", 1))
    ctx.write_json("number.json", {"value": value})
    return {"value": value}


def double(ctx: Any) -> dict[str, Any]:
    data = ctx.read_json("number.json", {"value": 0})
    doubled = int(data["value"]) * 2
    ctx.write_json("doubled.json", {"value": doubled})
    return {"doubled": doubled}


def undeclared_write(ctx: Any) -> None:
    ctx.write_json("surprise.json", {})


def always_retry(ctx: Any) -> str:
    return "retry"


def three_passes(ctx: Any) -> str:
    visits = int(ctx.facts.get("passes", 0)) + 1
    ctx.set_fact("passes", visits)
    return "retry" if visits < 3 else "proceed"


def bogus_verdict(ctx: Any) -> str:
    return "nonsense"


def boom(ctx: Any) -> None:
    raise ValueError("this op fails on purpose")


def write_best(ctx: Any) -> dict[str, Any]:
    return {"best": 42.0}
