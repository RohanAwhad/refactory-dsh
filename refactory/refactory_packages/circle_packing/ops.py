"""The operations the circle-packing mode calls.

Every operation is a plain function of the run context: it reads files from the
work directory, writes files it declares, and returns the facts it declares. No
subprocess, no shell, no sandbox — the graph the executor walks is the whole
program.

The search is a population of circle placements. ``seed_run`` produces one with
the task's own baseline heuristic; ``generate`` produces one per iteration;
``evaluate`` scores it with the task's own rule; ``accept`` keeps it if it beats
the parent. The outer loop optimizes the *generator's* settings and the shape of
this pipeline, not the placements themselves.
"""

from __future__ import annotations

import json
import math
import random
from typing import Any

from refactory_dsh.nn import Verdict

#: The task instance. Fixed, exactly as the task defines it: the mode is a
#: solver, so it must not be able to change the problem it is scored on.
CIRCLES = 26
RADII = tuple(round(0.05 + 0.005 * i, 6) for i in range(CIRCLES))

#: The baseline heuristic's grid, matching the task's own initial solution. The
#: seed is fixed so that moving the ``grid`` knob is a genuine improvement over
#: the starting point rather than a restatement of it.
SEED_GRID = 10

TASK_FILE = "task.json"
BUDGET_FILE = "budget_state.json"
POPULATION = "population.json"
BEST = "best_solution.json"
PARENT = "selected_parent.json"
CANDIDATE = "candidate.json"
EVAL = "eval_result.json"


# ── the algorithm ──────────────────────────────────────────────────────────
def greedy_pack(
    radii: tuple[float, ...] | list[float],
    *,
    grid: int,
    reverse: bool,
    jitter: float = 0.0,
    rng: random.Random | None = None,
) -> list[list[float]]:
    """Place circles largest-first at the position furthest from everything placed.

    ``jitter`` offsets each grid position by up to half a cell, which reaches
    placements a pure grid cannot. It is the difference between resampling the
    same answer and exploring.
    """
    n = len(radii)
    grid = max(2, int(grid))
    order = sorted(range(n), key=lambda i: radii[i], reverse=reverse)
    centers: list[list[float]] = [[float(radii[i]), float(radii[i])] for i in range(n)]
    step = 1.0 / max(grid - 1, 1)
    placed: list[tuple[float, float, float]] = []

    for index in order:
        r = float(radii[index])
        low, high = r, 1.0 - r
        best_position = (low, low)
        best_gap = float("-inf")
        for gx in range(grid):
            for gy in range(grid):
                x = low + (high - low) * gx / max(grid - 1, 1)
                y = low + (high - low) * gy / max(grid - 1, 1)
                if jitter and rng is not None:
                    x += rng.uniform(-0.5, 0.5) * step * jitter
                    y += rng.uniform(-0.5, 0.5) * step * jitter
                    x = min(max(x, low), high)
                    y = min(max(y, low), high)
                gap = float("inf")
                for px, py, pr in placed:
                    candidate_gap = math.hypot(x - px, y - py) - r - pr
                    if candidate_gap < gap:
                        gap = candidate_gap
                    if gap < best_gap:
                        break
                if gap > best_gap:
                    best_gap = gap
                    best_position = (x, y)
        centers[index] = [best_position[0], best_position[1]]
        placed.append((best_position[0], best_position[1], r))

    return centers


def score_centers(
    centers: list[list[float]], radii: tuple[float, ...] | list[float] = RADII
) -> tuple[float, dict[str, Any]]:
    """The task's own scoring rule: the radii of the circles that fit."""
    n = len(radii)
    if not centers or len(centers) != n:
        return 0.0, {"error": f"expected {n} centers, got {len(centers) if centers else 0}"}

    accepted: list[tuple[float, float, float]] = []
    overlaps = 0
    boundary_violations = 0
    for i in range(n):
        x, y = float(centers[i][0]), float(centers[i][1])
        r = float(radii[i])
        if x - r < -1e-9 or x + r > 1.0 + 1e-9 or y - r < -1e-9 or y + r > 1.0 + 1e-9:
            boundary_violations += 1
            continue
        overlapped = False
        for px, py, pr in accepted:
            if math.hypot(x - px, y - py) < r + pr - 1e-9:
                overlapped = True
                overlaps += 1
                break
        if not overlapped:
            accepted.append((x, y, r))

    score = sum(r for _, _, r in accepted)
    return round(score, 6), {
        "coverage": round(len(accepted) / n, 4),
        "overlaps": overlaps,
        "boundary_violations": boundary_violations,
        "placed": len(accepted),
        "total": n,
    }


# ── helpers ────────────────────────────────────────────────────────────────
def _rng(ctx: Any) -> random.Random:
    """A generator seeded by the mode's seed and how much budget is spent.

    Seeding from the budget rather than from wall clock keeps a run reproducible
    while still giving each iteration of the ladder a different sample.
    """
    seed = int(ctx.knob("seed", 7))
    state = ctx.read_json(BUDGET_FILE, {}) or {}
    return random.Random(f"{seed}:{state.get('spent', 0)}")


def _task(ctx: Any) -> dict[str, Any]:
    return {"name": "circle_packing", "n": CIRCLES, "radii": list(RADII)}


def _individual(identifier: str, centers: list[list[float]], origin: str) -> dict[str, Any]:
    score, metrics = score_centers(centers)
    return {
        "id": identifier,
        "score": score,
        "centers": centers,
        "origin": origin,
        "metrics": metrics,
    }


def _population(ctx: Any) -> dict[str, Any]:
    return ctx.read_json(POPULATION, {"individuals": [], "spent": 0}) or {
        "individuals": [],
        "spent": 0,
    }


def _best_of(individuals: list[dict[str, Any]]) -> dict[str, Any] | None:
    return max(individuals, key=lambda item: item["score"]) if individuals else None


def _spend(ctx: Any) -> int:
    state = ctx.read_json(BUDGET_FILE, {"spent": 0, "budget": 0}) or {}
    state["spent"] = int(state.get("spent", 0)) + 1
    ctx.write_json(BUDGET_FILE, state)
    return state["spent"]


# ── the pipeline ───────────────────────────────────────────────────────────
def task_input(ctx: Any) -> dict[str, Any]:
    """Write the task instance and open the budget."""
    task = _task(ctx)
    ctx.write_json(TASK_FILE, task)
    ctx.write_json(BUDGET_FILE, {"spent": 0, "budget": int(ctx.knob("budget", 3))})
    return {"circles": CIRCLES}


def seed_run(ctx: Any) -> dict[str, Any]:
    """Seed the population with the task's own baseline heuristic."""
    task = _task(ctx)
    centers = greedy_pack(task["radii"], grid=SEED_GRID, reverse=True)
    seed = _individual("seed", centers, "seed")
    ctx.write_json(POPULATION, {"individuals": [seed], "spent": 0})
    ctx.write_json(BEST, seed)
    ctx.write_json("genealogy.json", {})
    ctx.write_json("accepted_history.json", [{"id": "seed", "score": seed["score"]}])
    ctx.write_json("rejection_history.json", [])
    return {"seed_score": seed["score"]}


def select_parent(ctx: Any) -> dict[str, Any]:
    """Choose the individual the next candidate is derived from."""
    individuals = _population(ctx)["individuals"]
    if not individuals:
        raise ValueError("the population is empty; seed_run must run before select_parent")
    mode = str(ctx.knob("selector", "best"))
    rng = _rng(ctx)
    if mode == "uniform":
        chosen = rng.choice(individuals)
    elif mode == "tournament":
        contenders = rng.sample(individuals, min(3, len(individuals)))
        chosen = max(contenders, key=lambda item: item["score"])
    else:
        chosen = max(individuals, key=lambda item: item["score"])
    ctx.write_json(PARENT, chosen)
    return {"parent_id": chosen["id"], "parent_score": chosen["score"]}


def generate(ctx: Any) -> dict[str, Any]:
    """Produce the next candidate from the resolved generator settings."""
    task = _task(ctx)
    parent = ctx.read_json(PARENT) or {}
    grid = int(ctx.knob("grid", SEED_GRID))
    reverse = str(ctx.knob("order", "desc")) != "asc"
    centers = greedy_pack(task["radii"], grid=grid, reverse=reverse)
    identifier = f"g{grid}{'d' if reverse else 'a'}-{int(ctx.knob('seed', 7))}-{len(parent)}"
    candidate = {
        "id": identifier,
        "centers": centers,
        "params": {"grid": grid, "order": "desc" if reverse else "asc"},
        "parent": parent.get("id"),
    }
    ctx.write_json(CANDIDATE, candidate)
    return {"candidate_id": identifier}


def explain_candidate(ctx: Any) -> dict[str, Any]:
    """An agent node: say why the current candidate is or is not an improvement.

    Insertable, not part of the seed pipeline. It exists so that a structural
    edit can bring a model call into the inner loop, which is the one thing a
    purely deterministic operation cannot do.
    """
    candidate = ctx.read_json(CANDIDATE) or {}
    parent = ctx.read_json(PARENT) or {}
    return {"critique": str(candidate.get("id")), "parent_id": parent.get("id")}


def evaluate_candidate(ctx: Any) -> dict[str, Any]:
    """Score the candidate with the task's rule and spend one unit of budget."""
    task = _task(ctx)
    candidate = ctx.read_json(CANDIDATE)
    if not candidate:
        raise ValueError("there is no candidate to evaluate")
    score, metrics = score_centers(candidate["centers"], task["radii"])
    ctx.write_json(EVAL, {"id": candidate["id"], "score": score, "metrics": metrics})
    ctx.append_text("budget.jsonl", json.dumps({"id": candidate["id"], "score": score}) + "\n")
    _spend(ctx)
    return {"candidate_score": score}


def accept_or_reject(ctx: Any) -> dict[str, Any]:
    """Keep the candidate when it beats its parent, and record either way."""
    candidate = ctx.read_json(CANDIDATE) or {}
    evaluation = ctx.read_json(EVAL) or {}
    parent = ctx.read_json(PARENT) or {}
    population = _population(ctx)
    mode = str(ctx.knob("acceptance_mode", "strict"))

    score = float(evaluation.get("score", 0.0))
    parent_score = float(parent.get("score", float("-inf")))
    if mode == "off":
        accepted = True
    elif mode == "lenient":
        accepted = score >= parent_score - 1e-9
    else:
        accepted = score > parent_score

    individuals = population["individuals"]
    if accepted and not any(item["id"] == candidate["id"] for item in individuals):
        individuals.append(_individual(candidate["id"], candidate["centers"], "generated"))
        ctx.append_text(
            "accepted_history.jsonl",
            json.dumps({"id": candidate["id"], "score": score}) + "\n",
        )
    elif not accepted:
        history = ctx.read_json("rejection_history.json", []) or []
        history.append({"id": candidate["id"], "score": score, "parent": parent.get("id")})
        ctx.write_json("rejection_history.json", history)

    best = _best_of(individuals)
    population["spent"] = int((ctx.read_json(BUDGET_FILE, {}) or {}).get("spent", 0))
    ctx.write_json(POPULATION, population)
    ctx.write_json(BEST, best)
    genealogy = ctx.read_json("genealogy.json", {}) or {}
    genealogy[candidate["id"]] = parent.get("id")
    ctx.write_json("genealogy.json", genealogy)
    ctx.write_json("accepted_history.json", [
        {"id": item["id"], "score": item["score"]} for item in individuals
    ])
    ctx.append_text("candidates.jsonl", json.dumps({"id": candidate["id"]}) + "\n")
    ctx.append_text(
        "evaluations.jsonl", json.dumps({"id": candidate["id"], "score": score}) + "\n"
    )
    return {"accepted": accepted, "best_score_so_far": best["score"] if best else 0.0}


def robust_select_parent(ctx: Any) -> dict[str, Any]:
    """Insertable: pick a parent, restarting from a fresh sample when stalled.

    Unlike ``select_parent``, this spends one unit of budget to inject a jittered
    individual when the last few candidates were all rejected. It is the shape of
    fix a search should be able to propose: the pipeline was re-evaluating the
    same deterministic answer, and nothing in it could ever vary the sample.
    """
    population = _population(ctx)
    individuals = population["individuals"]
    if not individuals:
        raise ValueError("the population is empty; seed_run must run before this node")
    rejections = ctx.read_json("rejection_history.json", []) or []
    staleness = len(rejections)
    if staleness >= 2:
        task = _task(ctx)
        rng = _rng(ctx)
        fresh = _individual(
            f"restart{staleness}",
            greedy_pack(task["radii"], grid=SEED_GRID, reverse=True, jitter=1.0, rng=rng),
            "restart",
        )
        if not any(item["id"] == fresh["id"] for item in individuals):
            individuals.append(fresh)
        _spend(ctx)
        population["individuals"] = individuals
        ctx.write_json(POPULATION, population)
    chosen = max(individuals, key=lambda item: item["score"])
    ctx.write_json(PARENT, chosen)
    return {"parent_id": chosen["id"], "parent_score": chosen["score"]}


def observe(ctx: Any) -> dict[str, Any]:
    """Insertable: record how varied the population is."""
    individuals = _population(ctx)["individuals"]
    scores = [item["score"] for item in individuals]
    spread = (max(scores) - min(scores)) if scores else 0.0
    ctx.write_json("observations.json", {
        "population": len(individuals),
        "best": max(scores) if scores else 0.0,
        "spread": round(spread, 6),
        "origins": sorted({item.get("origin", "?") for item in individuals}),
    })
    return {"population": len(individuals), "spread": round(spread, 6)}


def budget_check(ctx: Any) -> str:
    """Gate: reloop while budget remains, then proceed."""
    state = ctx.read_json(BUDGET_FILE, {"spent": 0, "budget": 0}) or {}
    spent = int(state.get("spent", 0))
    ctx.set_fact("spent", spent)
    return "reloop" if spent < int(state.get("budget", 0)) else "proceed"


def retry_gate(ctx: Any) -> str:
    """Insertable gate: retry an evaluation that produced no score."""
    attempts = int(ctx.facts.get("eval_attempts", 0)) + 1
    ctx.set_fact("eval_attempts", attempts)
    evaluation = ctx.read_json(EVAL, {}) or {}
    failed = not evaluation or float(evaluation.get("score", 0.0)) <= 0.0
    if failed and attempts <= 2:
        return Verdict("retry", f"the evaluation produced no score (retry {attempts} of 2)")
    return "proceed"


def finish(ctx: Any) -> dict[str, Any]:
    """Close the run and publish the number the outer loop maximizes."""
    population = _population(ctx)
    best = _best_of(population["individuals"]) or {"id": "none", "score": 0.0}
    state = ctx.read_json(BUDGET_FILE, {}) or {}
    ctx.write_json(BEST, best)
    ctx.write_json("best_score.json", {
        "best_score": best["score"],
        "id": best["id"],
        "spent": int(state.get("spent", 0)),
    })
    return {
        "best_score": float(best["score"]),
        "best_id": str(best["id"]),
        "spent": int(state.get("spent", 0)),
        "population": len(population["individuals"]),
    }
