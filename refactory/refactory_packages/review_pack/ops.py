"""The operations the interactive review mode calls.

A small, real pipeline rather than a fixture: materialize the task, write a draft,
check it with a deterministic rule, let a person approve it, then publish or record
the rejection. ``approve`` is answered by a person through the driving agent; this
module never invents that answer, and a headless run refuses at it.

Every operation is a plain function of the run context: it reads the files it
declares, writes the files it declares, and returns the facts it declares.
"""

from __future__ import annotations

import json
from typing import Any

from refactory_dsh.nn import Verdict

TASK = "task.json"
DRAFT = "draft.md"
FINAL = "final.md"
REJECTED = "rejected.md"

#: A draft shorter than this is not worth a person's time; the fn gate reloops.
MIN_WORDS = 8


def task_input(ctx: Any) -> dict[str, Any]:
    """Materialize the run's task, so the draft has a subject."""
    topic = str(ctx.task.get("topic") or ctx.task.get("brief")
                or ctx.knob("topic", "a short note") or "a short note")
    ctx.write_text(TASK, json.dumps({"topic": topic}, indent=2))
    return {"topic": topic}


def draft(ctx: Any) -> dict[str, Any]:
    """Write one draft, its length set by the ``rounds`` knob."""
    topic = json.loads(ctx.read_text(TASK))["topic"]
    rounds = int(ctx.knob("rounds", 3))
    points = "\n".join(f"- point {n} about {topic}" for n in range(1, rounds + 1))
    text = f"# {topic}\n\n{points}\n"
    ctx.write_text(DRAFT, text)
    return {"draft_words": len(text.split())}


def check(ctx: Any) -> str:
    """The deterministic gate: is the draft worth showing a person?"""
    words = len(ctx.read_text(DRAFT).split())
    if words >= MIN_WORDS:
        return "proceed"
    return Verdict("reloop", f"the draft has {words} words; a person is shown at least {MIN_WORDS}")


def publish(ctx: Any) -> dict[str, Any]:
    """Publish the approved draft."""
    text = ctx.read_text(DRAFT)
    ctx.write_text(FINAL, f"approved by the person\n\n{text}")
    return {"published_chars": len(text)}


def reject(ctx: Any) -> dict[str, Any]:
    """Record that the person did not approve, and publish nothing."""
    ctx.write_text(REJECTED, "the person did not approve this draft\n")
    return {"rejected": True}
