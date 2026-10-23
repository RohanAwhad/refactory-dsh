"""Score the LiveMath harness on one part of the split, outside the outer loop.

    python -m refactory_packages.livemath_pack.evaluate --part test --shards 25 \\
        --out runs/baseline --provider anthropic --model claude-opus-4-6 --effort high

This is how a baseline is measured: the same mode, Task and walker the outer loop uses,
on the items of one part. The items are dealt round-robin into shards and each shard is
one ordinary run of the mode in a directory of its own, pointed at its items through the
run's `items.json` source. The shards run in parallel processes, because a data node
runs its items one after another and a 125-item part would otherwise take hours.

`summary.json` in `--out` records the accuracy, every item's verdict, the response that
was graded, and each shard's errors. A shard that fails is reported, not dropped.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from . import data
from .tasks import RESPONSE

#: The DSH patch that turns every tool of the solver's agent off.
NO_TOOLS = Path(__file__).resolve().parent / "no-tools.yml"


def _public(item: dict[str, Any]) -> dict[str, Any]:
    """The fields a pass may see: never the correct choice."""
    return {"id": item["id"], "part": item["part"], "question": item["question"],
            "choices": item["choices"]}


def _route(args: argparse.Namespace) -> dict[str, str]:
    return {"worker_backend": args.worker_backend, "provider": args.provider,
            "model": args.model, "effort": args.effort,
            "permission_mode": args.permission_mode}


def run_shard(shard_dir: str, route: dict[str, str], skill: str | None) -> dict[str, Any]:
    """One run of the mode over the items written to `shard_dir`."""
    from refactory_dsh.evaluators import WalkerEvaluator
    from refactory_dsh.model import DshModelClient

    from .mode import SolvePack

    workdir = Path(shard_dir)
    mode = SolvePack()
    if skill is not None:
        state = mode.state_dict()
        state["knobs"]["skill"]["value"] = skill
        mode.load_state_dict(state)
    client = DshModelClient(**{key: value for key, value in route.items() if value})
    started = time.time()
    try:
        result = WalkerEvaluator(model=client, project=workdir).evaluate(
            mode, mode.state_dict(), workdir / "run")
    except Exception as exc:  # a shard that cannot run is a result, not a crash
        return {"shard": workdir.name, "exit_ok": False, "error": f"{type(exc).__name__}: {exc}",
                "item_scores": [], "duration_s": round(time.time() - started, 1)}
    return {"shard": workdir.name, "exit_ok": result.exit_ok, "error": result.error,
            "item_scores": result.facts.get("item_scores", []),
            "model_calls": result.model_calls, "duration_s": round(time.time() - started, 1)}


def _responses(shard_dir: Path) -> dict[str, str]:
    """Each item's graded response, read from the responses the shard recorded."""
    path = shard_dir / "run" / "responses.jsonl"
    found: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                entry = json.loads(line)
                found[str(entry.get("id"))] = str(entry.get("response") or "")
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--part", choices=data.PARTS, required=True)
    parser.add_argument("--limit", type=int, default=0, help="first N items of the part")
    parser.add_argument("--shards", type=int, default=1, help="parallel runs")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--skill-file", type=Path, help="skill text; default is the mode's")
    parser.add_argument("--no-skill", action="store_true", help="run with an empty skill")
    parser.add_argument("--worker-backend", default="dsh")
    parser.add_argument("--provider", default="")
    parser.add_argument("--model", default="")
    parser.add_argument("--effort", default="")
    parser.add_argument("--permission-mode", default="")
    parser.add_argument("--no-tools", action="store_true",
                        help="give the DSH solver no tools, as SkillOpt's call has none")
    args = parser.parse_args(argv)
    if args.no_tools:
        from refactory_dsh.model import EXTRA_PATCHES_ENV

        os.environ[EXTRA_PATCHES_ENV] = os.pathsep.join(
            part for part in (os.environ.get(EXTRA_PATCHES_ENV, ""), str(NO_TOOLS)) if part)

    items = [item for item in data.items() if item["part"] == args.part]
    if args.limit:
        items = items[: args.limit]
    skill = "" if args.no_skill else (args.skill_file.read_text() if args.skill_file else None)
    shards = max(1, min(args.shards, len(items)))
    args.out.mkdir(parents=True, exist_ok=True)
    if any(args.out.iterdir()):
        print(json.dumps({"error": f"{args.out} is not empty; use a new directory"}), file=sys.stderr)
        return 2

    from refactory_dsh.items import write_source

    dirs = []
    for index in range(shards):
        shard_dir = args.out / f"shard-{index:03d}"
        chunk = items[index::shards]
        shard_dir.mkdir(parents=True)
        source = shard_dir / "items.jsonl"
        source.write_text("".join(json.dumps(_public(item)) + "\n" for item in chunk))
        write_source(shard_dir / "run", source_path=str(source), source_format="jsonl")
        dirs.append(shard_dir)

    route = _route(args)
    started = time.time()
    results: list[dict[str, Any]] = []
    with ProcessPoolExecutor(max_workers=shards) as pool:
        futures = [pool.submit(run_shard, str(path), route, skill) for path in dirs]
        for future in as_completed(futures):
            outcome = future.result()
            results.append(outcome)
            done = sum(len(r["item_scores"]) for r in results)
            print(json.dumps({"shard": outcome["shard"], "exit_ok": outcome["exit_ok"],
                              "items_done": done, "of": len(items),
                              "error": outcome["error"]}), flush=True)

    responses: dict[str, str] = {}
    for path in dirs:
        responses.update(_responses(path))
    verdicts = {str(entry["id"]): entry for r in results for entry in r["item_scores"]}
    scored = [verdicts[item["id"]] for item in items if item["id"] in verdicts]
    correct = sum(1 for entry in scored if entry.get("passed"))
    summary = {
        "part": args.part,
        "route": route,
        "skill": "empty" if skill == "" else ("file" if skill is not None else "mode default"),
        "tools": "none" if args.no_tools else "dsh default",
        "items": len(items),
        "graded": len(scored),
        "correct": correct,
        "accuracy": correct / len(items) if items else None,
        "accuracy_of_graded": correct / len(scored) if scored else None,
        "wall_s": round(time.time() - started, 1),
        "shards": sorted(results, key=lambda r: r["shard"]),
        "per_item": [
            {"id": item["id"], "passed": bool(verdicts.get(item["id"], {}).get("passed")),
             "graded": item["id"] in verdicts, "response": responses.get(item["id"], "")}
            for item in items
        ],
    }
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({key: summary[key] for key in
                      ("part", "items", "graded", "correct", "accuracy", "wall_s")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
