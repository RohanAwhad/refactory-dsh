"""The operations the annealing-packing mode calls.

Every operation is a plain function of the run context: it reads files from the
work directory, writes files it declares, and returns the facts it declares. The
graph the executor walks is the whole program.

The search is a stochastic hill-climb over placements. ``seed_placement`` starts
from the task's own greedy packer; each iteration ``perturb_circle`` moves one
circle by a small random offset, and ``accept_or_revert`` keeps the move when the
task score does not fall. The instance is fixed in this module, so the mode can
never change the problem it is scored on.
"""

from __future__ import annotations

import random
from typing import Any

from refactory_packages.circle_packing.ops import RADII, greedy_pack, score_centers

#: The baseline heuristic's grid. The seed placement is the same one the
#: reference package starts from, so the hill-climb improves a known point.
SEED_GRID = 10

CURRENT = "current.json"
CANDIDATE = "candidate.json"
BEST = "best.json"
PROGRESS = "progress.json"
HISTORY = "history.json"
TRACE = "score_trace.json"
RESULT = "best_result.json"


def _score(centers: list[list[float]]) -> tuple[float, dict[str, Any]]:
    """The task's own scoring rule, applied to the fixed instance."""
    return score_centers(centers, RADII)


def seed_placement(ctx: Any) -> dict[str, Any]:
    """Seed the working and best placement with the greedy packer.

    All four state files are rewritten, so a repeated run in the same work
    directory starts from the same state.
    """
    centers = greedy_pack(RADII, grid=SEED_GRID, reverse=True)
    score, _metrics = _score(centers)
    ctx.write_json(CURRENT, {"centers": centers, "score": score})
    ctx.write_json(BEST, {"centers": centers, "score": score, "iteration": 0})
    ctx.write_json(HISTORY, [{"iteration": 0, "score": score, "accepted": True, "best": score}])
    ctx.write_json(PROGRESS, {"iteration": 0, "last_score": score})
    return {"seed_score": score}


def perturb_circle(ctx: Any) -> dict[str, Any]:
    """Move exactly one circle by a uniform offset of up to ``step``.

    The RNG is seeded from ``(seed, iteration)`` rather than from its sequential
    position, so the move stream is independent of any node a structural edit
    inserts, and two runs of the same state reproduce.
    """
    current = ctx.read_json(CURRENT) or {"centers": [[r, r] for r in RADII], "score": 0.0}
    progress = ctx.read_json(PROGRESS, {}) or {}
    iteration = int(progress.get("iteration", 0)) + 1
    rng = random.Random(f"{int(ctx.knob('seed', 7))}:{iteration}")
    index = rng.randrange(len(RADII))
    step = float(ctx.knob("step", 0.02))

    centers = [[float(x), float(y)] for x, y in current["centers"]]
    radius = RADII[index]
    dx = rng.uniform(-step, step)
    dy = rng.uniform(-step, step)
    x = min(max(centers[index][0] + dx, radius), 1.0 - radius)
    y = min(max(centers[index][1] + dy, radius), 1.0 - radius)
    centers[index] = [x, y]

    score, metrics = _score(centers)
    ctx.write_json(
        CANDIDATE,
        {
            "centers": centers,
            "score": score,
            "circle": index,
            "offset": [dx, dy],
            "metrics": metrics,
        },
    )
    ctx.write_json(PROGRESS, {"iteration": iteration, "last_score": score})
    return {"candidate_score": score}


def accept_or_revert(ctx: Any) -> dict[str, Any]:
    """Keep the candidate when its task score does not fall.

    ``best.json`` moves only on a strict improvement, and every move is appended
    to ``history.json`` whether or not it was kept.
    """
    current = ctx.read_json(CURRENT) or {"centers": [], "score": 0.0}
    candidate = ctx.read_json(CANDIDATE) or {"centers": [], "score": 0.0}
    best = ctx.read_json(BEST) or current
    iteration = int((ctx.read_json(PROGRESS, {}) or {}).get("iteration", 0))
    candidate_score = float(candidate.get("score", 0.0))

    accepted = candidate_score >= float(current.get("score", 0.0))
    if accepted:
        current = {"centers": candidate["centers"], "score": candidate_score}
        ctx.write_json(CURRENT, current)
    if candidate_score > float(best.get("score", float("-inf"))):
        best = {
            "centers": candidate["centers"],
            "score": candidate_score,
            "iteration": iteration,
        }
        ctx.write_json(BEST, best)

    history = ctx.read_json(HISTORY, []) or []
    history.append(
        {
            "iteration": iteration,
            "score": candidate_score,
            "accepted": accepted,
            "best": float(best["score"]),
        }
    )
    ctx.write_json(HISTORY, history)
    return {
        "accepted": accepted,
        "current_score": float(current["score"]),
        "best_score_so_far": float(best["score"]),
    }


def iterations_gate(ctx: Any) -> str:
    """Gate: run the move while iterations remain, then proceed to the result."""
    iteration = int((ctx.read_json(PROGRESS, {}) or {}).get("iteration", 0))
    ctx.set_fact("iterations_run", iteration)
    return "loop" if iteration < int(ctx.knob("iterations", 200)) else "proceed"


def finish(ctx: Any) -> dict[str, Any]:
    """Publish the best score and the best placement the run kept."""
    best = ctx.read_json(BEST) or {"centers": [], "score": 0.0}
    progress = ctx.read_json(PROGRESS, {}) or {}
    payload = {
        "best_score": float(best.get("score", 0.0)),
        "best_centers": best.get("centers", []),
        "iterations": int(progress.get("iteration", 0)),
    }
    ctx.write_json(RESULT, payload)
    return {"best_score": payload["best_score"], "best_centers": payload["best_centers"]}


def observe_score_moves(ctx: Any) -> dict[str, Any]:
    """Insertable: record how the score moved during the run.

    It reads only ``history.json``, which ``seed_placement`` and
    ``accept_or_revert`` write, and tolerates being spliced into other positions
    because ``history.json`` defaults to an empty list.
    """
    history = ctx.read_json(HISTORY, []) or []
    scores = [float(entry.get("score", 0.0)) for entry in history]
    start = scores[0] if scores else 0.0
    end = scores[-1] if scores else 0.0
    delta = round(end - start, 6)
    ctx.write_json(
        TRACE,
        {
            "samples": len(scores),
            "start": start,
            "end": end,
            "best": max(scores) if scores else 0.0,
            "delta": delta,
            "improvements": sum(1 for entry in history if entry.get("accepted")),
            "scores": scores,
        },
    )
    return {"score_delta": delta}
