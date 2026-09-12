"""The outer loop's visibility surface.

A user steering a search needs the same things a person tuning an optimizer
needs, not a log: is it learning, what is blocking it, which lever is paying off,
where has it not looked. This module derives those from artifacts the loop
already writes, and emits one report the GUI can render and a person can read.

Nothing here is model-authored. Every number is computed from the population, the
archive, the trace facts and the rejection log, so the surface stays trustworthy
even when a reasoning optimizer is producing the candidates.
"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from pathlib import Path

from refactory_dsh.trace import INDEX, TRACES_DIR

OUTER = Path(".factory") / "outer_loop"
GRID_SOURCE = "MAP-Elites archive"


def _load(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return default


def _traces(project_dir: Path) -> list[dict]:
    """Every trace in the run, read from disk rather than from the index.

    The index is written when a trace is first created; a re-keyed or repaired
    trace would be missed by an index-only read, and a candidate silently
    dropping out of the report is exactly the kind of gap this surface exists to
    prevent.
    """
    traces = project_dir / TRACES_DIR
    if not traces.is_dir():
        return []
    facts: list[dict] = []
    for path in sorted(traces.glob("*.json")):
        if path.stem == INDEX.removesuffix(".json"):
            continue
        loaded = _load(path, None)
        if isinstance(loaded, dict):
            facts.append(loaded)
    return facts


def _rejections(project_dir: Path) -> list[dict]:
    path = project_dir / OUTER / "rejections.jsonl"
    if not path.exists():
        return []
    rows = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def _leaderboard(traces: list[dict]) -> list[dict]:
    """Per-operator attempts, accepts and score deltas.

    This is the "more of this, less of that" input: it says which lever has
    actually moved the score rather than which one has been used most.
    """
    stats: dict[str, dict[str, float]] = defaultdict(
        lambda: {"attempts": 0, "accepted": 0, "delta_sum": 0.0, "delta_n": 0,
                 "best": None, "run_failures": 0}
    )
    for facts in traces:
        outcome = facts.get("outcome") or {}
        run = facts.get("run") or {}
        operator = (facts.get("proposal") or {}).get("operator") or "seed"
        row = stats[operator]
        row["attempts"] += 1
        # A candidate that never produced a score is a different fact from one
        # that scored badly: the first is evidence against the edit that produced
        # it, and a structural edit can keep every declared artifact and still
        # fail to run at all. Counting it here is what makes that visible.
        if outcome.get("stuck") or (run.get("exit_code") not in (0, None)):
            row["run_failures"] += 1
        if outcome.get("verdict") == "keep":
            row["accepted"] += 1
        delta = outcome.get("delta")
        if isinstance(delta, (int, float)):
            row["delta_sum"] += delta
            row["delta_n"] += 1
        score = outcome.get("score")
        if isinstance(score, (int, float)) and (row["best"] is None or score > row["best"]):
            row["best"] = score
    out = []
    for operator, row in stats.items():
        out.append(
            {
                "operator": operator,
                "attempts": int(row["attempts"]),
                "accepted": int(row["accepted"]),
                "accept_rate": row["accepted"] / row["attempts"] if row["attempts"] else 0.0,
                "mean_delta": (row["delta_sum"] / row["delta_n"]) if row["delta_n"] else None,
                "best_score": row["best"],
                "run_failures": int(row["run_failures"]),
            }
        )
    out.sort(key=lambda r: (r["mean_delta"] is None, -(r["mean_delta"] or 0.0)))
    return out


def _knob_attribution(traces: list[dict]) -> list[dict]:
    """Which values of which knob produced which scores."""
    by_knob: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for facts in traces:
        score = (facts.get("outcome") or {}).get("score")
        if not isinstance(score, (int, float)):
            continue
        knobs = (facts.get("shape") or {}).get("knob_values") or {}
        for name, value in knobs.items():
            by_knob[name][str(value)].append(score)
    rows = []
    for name, values in sorted(by_knob.items()):
        rows.append(
            {
                "knob": name,
                "values": [
                    {"value": v, "n": len(scores), "best": max(scores),
                     "mean": sum(scores) / len(scores)}
                    for v, scores in sorted(values.items(), key=lambda kv: -max(kv[1]))
                ],
            }
        )
    return rows


def _rationale_outcomes(traces: list[dict]) -> list[dict]:
    """What the optimizer predicted versus what happened.

    The point of recording predictions is being able to check them: a proposal
    whose stated expectation did not hold is information about the optimizer, not
    just about the candidate.
    """
    rows = []
    for facts in traces:
        proposal = facts.get("proposal") or {}
        after = proposal.get("after") or {}
        if not after.get("prediction"):
            continue
        outcome = facts.get("outcome") or {}
        rows.append(
            {
                "candidate": facts.get("key"),
                "operator": proposal.get("operator"),
                "rationale": (proposal.get("rationale") or "")[:240],
                "prediction": after.get("prediction"),
                "confidence": after.get("confidence"),
                "score": outcome.get("score"),
                "delta": outcome.get("delta"),
                "verdict": outcome.get("verdict"),
            }
        )
    return rows


def build(project_dir: Path) -> dict:
    """Assemble the whole visibility surface for a run."""
    project_dir = Path(project_dir)
    state = _load(project_dir / OUTER / "state.json", {})
    population = _load(project_dir / OUTER / "population" / "population.json", [])
    grid = _load(project_dir / OUTER / "archive" / "grid.json", {})
    traces = _traces(project_dir)
    rejections = _rejections(project_dir)

    scores = [i.get("score") for i in population if isinstance(i.get("score"), (int, float))]
    best = max(scores) if scores else 0.0
    best_ever = max([*scores, *[s for s in state.get("score_trajectory", []) if isinstance(s, (int, float))]] or [0.0])

    reject_reasons: dict[str, int] = defaultdict(int)
    for row in rejections:
        for reason in row.get("reasons", []):
            reject_reasons[reason] += 1

    durations = [
        (f.get("run") or {}).get("duration_s")
        for f in traces
        if isinstance((f.get("run") or {}).get("duration_s"), (int, float))
    ]

    from refactory_dsh import steering

    settings = steering.load(project_dir)
    active_optimizer = settings.get("optimizer") or os.environ.get("REFACTORY_OPTIMIZER", "random")
    return {
        "generation": state.get("generation", 0),
        "steering": {
            "optimizer": active_optimizer,
            "operator_weights": steering.operator_weights(settings),
            "frozen_knobs": sorted(steering.frozen_knobs(settings)),
            "frozen_nodes": sorted(steering.frozen_nodes(settings)),
            "tournament_size": settings.get("tournament_size"),
            "note": "edit .factory/outer_loop/steering.json to change these; applied from the next step",
        },
        "budget": {
            "consumed": state.get("budget_consumed", 0),
            "total_cost_usd": state.get("total_cost_usd", 0.0),
            "inner_seconds_total": round(sum(durations), 1),
            "inner_seconds_mean": round(sum(durations) / len(durations), 1) if durations else 0.0,
        },
        "learning": {
            "score_trajectory": state.get("score_trajectory", []),
            "best_now": best,
            "best_ever": best_ever,
            # The archive holds composite fitness; the traces hold the task's own
            # score. Both are reported because they are not the same number and
            # comparing them across the two sources is misleading.
            "best_task_score": max(
                [
                    (f.get("outcome") or {}).get("score")
                    for f in traces
                    if isinstance((f.get("outcome") or {}).get("score"), (int, float))
                ]
                or [0.0]
            ),
            "score_scale_note": "best_now is composite fitness; best_task_score is the inner task metric",
            "mean_now": (sum(scores) / len(scores)) if scores else 0.0,
            "population": len(population),
        },
        "coverage": {
            "source": GRID_SOURCE,
            "occupied_cells": len(grid),
            "population": len(population),
            "note": "occupied cells are distinct feature coordinates the search has kept",
        },
        "operator_leaderboard": _leaderboard(traces),
        "knob_attribution": _knob_attribution(traces),
        "rejection_reasons": sorted(
            ({"reason": r, "count": c} for r, c in reject_reasons.items()),
            key=lambda r: -r["count"],
        ),
        "rejected_total": len(rejections),
        "proposals": _rationale_outcomes(traces),
        "traces": len(traces),
    }


def render(data: dict) -> str:
    """Render the surface as Markdown for a person to read."""
    b, learn, cov = data["budget"], data["learning"], data["coverage"]
    lines = [
        f"# Outer loop — generation {data['generation']}",
        "",
        "## Steering in force",
        f"- optimizer **{data['steering']['optimizer']}** · "
        f"frozen knobs {data['steering']['frozen_knobs'] or 'none'} · "
        f"frozen nodes {data['steering']['frozen_nodes'] or 'none'}",
        f"- operator weights `{json.dumps(data['steering']['operator_weights'])}`",
        "",
        "## Is it learning?",
        f"- best now **{learn['best_now']}** (composite fitness) · "
        f"best task score **{learn['best_task_score']}** · "
        f"mean now {learn['mean_now']:.4g} · population {learn['population']}",
        f"- best ever {learn['best_ever']}",
        f"- score trajectory {learn['score_trajectory']}",
        "",
        "## What is it costing?",
        f"- budget consumed {b['consumed']} · reported cost ${b['total_cost_usd']}",
        f"- inner-loop time {b['inner_seconds_total']}s total, {b['inner_seconds_mean']}s per candidate",
        "",
        "## Where has it looked?",
        f"- {cov['occupied_cells']} occupied archive cells across {cov['population']} individuals",
        "",
        "## Which lever is paying off?",
        "",
        "| operator | attempts | accepted | accept rate | mean Δ | best | never ran |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in data["operator_leaderboard"]:
        mean = "—" if row["mean_delta"] is None else f"{row['mean_delta']:+.4g}"
        best = "—" if row["best_score"] is None else f"{row['best_score']:.4g}"
        lines.append(
            f"| {row['operator']} | {row['attempts']} | {row['accepted']} | "
            f"{row['accept_rate']:.0%} | {mean} | {best} | {row['run_failures']} |"
        )

    if data["knob_attribution"]:
        lines += ["", "## Which knob values scored well?", ""]
        for knob in data["knob_attribution"]:
            values = ", ".join(
                f"{v['value']} (n={v['n']}, best {v['best']:.4g})" for v in knob["values"][:6]
            )
            lines.append(f"- **{knob['knob']}**: {values}")

    if data["rejection_reasons"]:
        lines += ["", "## Why were candidates refused before evaluation?", ""]
        lines += [
            f"- {r['count']}× {r['reason']}" for r in data["rejection_reasons"][:8]
        ]
        lines.append(f"- {data['rejected_total']} refusals in total (no inner run spent on them)")

    if data["proposals"]:
        lines += ["", "## Proposal versus outcome", ""]
        for row in data["proposals"][-6:]:
            delta = "—" if row["delta"] is None else f"{row['delta']:+.4g}"
            lines.append(
                f"- [{row['operator']}] {row['rationale']}"
                f"\n    - predicted: {row['prediction']} (confidence {row['confidence']})"
                f"\n    - actual: score {row['score']} ({delta}), {row['verdict']}"
            )
    return "\n".join(lines) + "\n"


def write(project_dir: Path) -> tuple[Path, Path]:
    """Compute the surface and persist both renderings."""
    project_dir = Path(project_dir)
    data = build(project_dir)
    out = project_dir / OUTER
    out.mkdir(parents=True, exist_ok=True)
    json_path = out / "report.json"
    md_path = out / "report.md"
    json_path.write_text(json.dumps(data, indent=2, default=str))
    md_path.write_text(render(data))
    return json_path, md_path
