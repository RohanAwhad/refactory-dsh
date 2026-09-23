"""The operations the counting modes call.

Every operation is a plain function of the run context: it reads files from the
work directory, writes files it declares, and returns the facts it declares. No
subprocess, no shell, no sandbox -- the graph the executor walks is the whole
program.

Both modes count up from zero toward a target, one increment per visit to the
``step`` node, and both publish ``closeness``: how near the count the run
actually reached is to the target. The only difference between the modes is how
an increment is drawn: ``step_random`` seeds a generator from ``(seed, step
index)`` and draws uniformly in ``1..step``; ``step_fixed`` adds exactly
``step``.
"""

from __future__ import annotations

import random
from typing import Any

#: The shared run files. ``start`` opens state and trace, ``step`` rewrites both,
#: ``check`` reads state, and ``finish`` writes the result.
STATE = "state.json"
TRACE = "trace.json"
RESULT = "result.json"

#: The file the insertable ``record_trace`` block writes.
SCORE_TRACE = "score_trace.json"

#: The count every run opens at.
START_COUNT = 0

#: Bounds a step increment needs when a read-back state is malformed. They mirror
#: the declared knob bounds so a spliced-in step still advances the count.
MIN_STEP = 1
MAX_STEP = 4

#: The target used when ``state.json`` does not carry one.
DEFAULT_TARGET = 5

#: The seed used when the ``seed`` knob is absent, which is the ``fixed_step`` case.
DEFAULT_SEED = 7


# ── helpers ────────────────────────────────────────────────────────────────
def _step_size(ctx: Any) -> int:
    """The mode's ``step`` knob, clamped to the declared bounds.

    The clamp only bites on a state an edit rewrote; the declared knob is already
    bounded by ``V9``.
    """
    size = int(ctx.knob("step", MIN_STEP))
    return max(MIN_STEP, min(MAX_STEP, size))


def _advance(
    ctx: Any,
    state: dict[str, Any],
    trace: dict[str, Any],
    index: int,
    increment: int,
) -> dict[str, Any]:
    """Add one increment to the count and append it to the trace.

    Both step operations return through here, so the state file, the trace file,
    and the two run facts stay in one place.
    """
    value = int(state.get("value", START_COUNT)) + increment
    increments = [int(i) for i in (trace.get("increments") or [])]
    counts = [int(c) for c in (trace.get("counts") or [])]
    if not counts:
        counts = [int(state.get("value", START_COUNT))]
    increments.append(increment)
    counts.append(value)
    target = int(state.get("target", DEFAULT_TARGET))
    ctx.write_json(STATE, {"value": value, "target": target, "steps": index})
    ctx.write_json(TRACE, {"increments": increments, "counts": counts})
    return {"value": value, "steps": index}


# ── the pipeline ───────────────────────────────────────────────────────────
def start(ctx: Any) -> dict[str, Any]:
    """Open the count at zero, record the target, and begin the empty trace."""
    target = int(ctx.knob("target", DEFAULT_TARGET))
    value = START_COUNT
    ctx.write_json(STATE, {"value": value, "target": target, "steps": 0})
    ctx.write_json(TRACE, {"increments": [], "counts": [value]})
    return {"value": value, "steps": 0}


def step_random(ctx: Any) -> dict[str, Any]:
    """Add one random increment, drawn uniformly from ``1..step``.

    The generator is seeded from ``(seed, step index)`` rather than from its
    sequential position, so the increment stream does not shift when an edit
    inserts a node and two runs of the same state reproduce exactly.
    """
    state = ctx.read_json(STATE, {}) or {}
    trace = ctx.read_json(TRACE, {}) or {}
    index = int(state.get("steps", 0)) + 1
    size = _step_size(ctx)
    seed = int(ctx.knob("seed", DEFAULT_SEED))
    rng = random.Random(f"{seed}:{index}")
    increment = rng.randint(MIN_STEP, size)
    return _advance(ctx, state, trace, index, increment)


def step_fixed(ctx: Any) -> dict[str, Any]:
    """Add exactly ``step`` to the count."""
    state = ctx.read_json(STATE, {}) or {}
    trace = ctx.read_json(TRACE, {}) or {}
    index = int(state.get("steps", 0)) + 1
    return _advance(ctx, state, trace, index, _step_size(ctx))


def check(ctx: Any) -> str:
    """Gate: retry while the count is below the target, then proceed."""
    state = ctx.read_json(STATE, {}) or {}
    done = int(state.get("value", START_COUNT)) >= int(state.get("target", DEFAULT_TARGET))
    ctx.set_fact("done", done)
    return "proceed" if done else "retry"


def finish(ctx: Any) -> dict[str, Any]:
    """Publish the count the run reached and how close it landed to the target.

    ``closeness`` is ``1 / (1 + abs(final_count - target))``: bounded in ``(0, 1]``,
    exactly ``1`` on a bullseye, and falling monotonically with distance, so an
    overshoot cannot beat a hit the way a raw count would.
    """
    state = ctx.read_json(STATE, {}) or {}
    final_count = int(state.get("value", START_COUNT))
    target = int(state.get("target", DEFAULT_TARGET))
    steps_taken = int(state.get("steps", 0))
    closeness = 1.0 / (1.0 + abs(final_count - target))
    ctx.write_json(
        RESULT,
        {
            "final_count": final_count,
            "steps_taken": steps_taken,
            "closeness": closeness,
        },
    )
    return {
        "final_count": final_count,
        "steps_taken": steps_taken,
        "closeness": closeness,
    }


def record_trace(ctx: Any) -> dict[str, Any]:
    """Insertable: fold the per-step counts into a score trace.

    It reads only ``trace.json``, which ``start`` and both step operations write,
    so it tolerates being spliced into any position on the path. It does not
    change the count and therefore cannot move the score.
    """
    trace = ctx.read_json(TRACE, {}) or {}
    increments = [int(i) for i in (trace.get("increments") or [])]
    counts = [int(c) for c in (trace.get("counts") or [])]
    spread = (max(counts) - min(counts)) if counts else 0
    ctx.write_json(
        SCORE_TRACE,
        {
            "steps": len(increments),
            "increments": increments,
            "counts": counts,
            "total": counts[-1] if counts else 0,
            "spread": spread,
        },
    )
    return {"score_trace_steps": len(increments), "score_trace_spread": spread}
