"""The faithful outer loop: refactory's `SwarmEngine` (full 8-operator MAP-Elites)
driven through DSH.

This module does **not** reimplement the outer loop. It imports refactory's
production components verbatim — `SwarmEngine`, `SwarmEvaluator`,
`apply_random_mutation` (all eight `MutationType` operators), `MAPElitesArchive`
(`compute_features` grid placement), `OuterLoopReflector`, and tournament
selection (`MAPElitesArchive.sample_parent`). The single DSH-specific piece is
the *inner loop* (`EvaluatorFn`): it serialises a candidate `Workflow` to a
`graph.json` and launches `dsh --profile science run` to score it.

Inner loops are pluggable via `REFACTORY_SCORER`:

- `dsh` (default): the real inner loop — run the candidate mode through DSH on
  the SRF task and read `best_score`. Works for any inner loop (SRF, chess, …)
  because the candidate is just a refactory `Workflow` handed to the DSH spine.
- `surrogate`: a deterministic, dependency-free scorer over `compute_features`
  + `knob_values`, used by the test harness so every operator/archive/reflection
  path can be exercised without spawning a CEO run per candidate.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

# ── refactory outer-loop components (imported, never reimplemented) ────────
from factory.outer_loop.engine import SwarmEngine
from factory.outer_loop.evaluator import SwarmEvaluator
from factory.outer_loop.models import EvalResult, SwarmConfig
from factory.outer_loop.population import MAPElitesArchive, Population
from factory.outer_loop.similarity import NoveltyFilter, compute_features
from factory.workflow.primitives import Workflow

from refactory_dsh.paths import dsh_root, node_bin, python, srf_graphs, venv_bin

# ── Paths (resolved once through the single dependency boundary) ───────────
ROOT = Path(".factory/outer_loop")
POP_DIR = ROOT / "population"
ARCHIVE_DIR = ROOT / "archive"
STATE = ROOT / "state.json"
CONFIG = ROOT / "config.json"
RESULT = ROOT / "result.json"
EVENTS = ROOT / "events.jsonl"

NODE_BIN = node_bin()
DSH_ROOT = str(dsh_root())
VENV_BIN = str(venv_bin())
DEFAULT_GRAPHS_DIR = str(srf_graphs())
DEFAULT_TARGET = Path(DEFAULT_GRAPHS_DIR) / "gepa.graph.json"
DEFAULT_TASK = "circle_packing"


# ── Inner loop #1: DSH (production) ────────────────────────────────────────
def _read_best_score(stdout: str, workdir: Path, mode: str) -> float:
    """Read the best_score the DSH runner reported, from stdout or the state file."""
    # The runner emits one pretty-printed JSON object containing `best_score`.
    for m in re.finditer(r'"best_score"\s*:\s*([0-9]+(?:\.[0-9]+)?)', stdout):
        return float(m.group(1))
    # Fallback: the mode's ops persist the state file the runner itself reads.
    for name in (f"{mode}_state.json",):
        path = workdir / name
        if path.exists():
            try:
                data = json.loads(path.read_text())
                if isinstance(data.get("best_score"), (int, float)):
                    return float(data["best_score"])
            except (json.JSONDecodeError, OSError):
                pass
    return 0.0


def dsh_evaluate(workflow: Workflow, project_dir: str, instances: list[str]) -> EvalResult:
    """Inner loop: run the candidate `Workflow` through DSH and return its score.

    `instances` are benchmark training instances (task names). SRF's gepa has a
    single task, `circle_packing`; a multi-task inner loop would evaluate each
    instance and aggregate, but the contract is one `EvalResult` per workflow.
    """
    task = instances[0] if instances else DEFAULT_TASK
    mode = "gepa"  # keep so the runner's STATE_FILES lookup finds gepa_state.json
    tmp = Path(tempfile.mkdtemp(prefix="dsh-eval-"))
    graphs_dir = tmp / "graphs"
    graphs_dir.mkdir(parents=True)
    (graphs_dir / f"{mode}.graph.json").write_text(json.dumps(workflow.to_dict(), indent=2))
    workdir = tmp / "work"
    workdir.mkdir(parents=True)

    budget = os.environ.get("REFACTORY_EVAL_BUDGET", "1")
    timeout = int(os.environ.get("REFACTORY_DSH_TIMEOUT", "900"))
    model = os.environ.get("REFACTORY_DSH_MODEL", "")
    knobs = json.dumps(dict(workflow.knob_values))
    cmd = [
        NODE_BIN, "--import", "tsx/esm", "apps/cli/src/bin.ts",
        "--profile", "science", "run",
        "--mode", mode, "--task", task,
        "--budget", budget, "--knobs", knobs,
        "--workdir", str(workdir), "--graphs", str(graphs_dir),
        "--python-bin", VENV_BIN,
    ]
    if model:
        cmd += ["--model", model]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, cwd=DSH_ROOT, timeout=timeout)
    except subprocess.TimeoutExpired:
        return EvalResult(score=0.0, details={"error": "dsh_inner_loop_timeout"})
    score = _read_best_score(proc.stdout, workdir, mode)
    return EvalResult(score=score, benchmark_score=score)


# ── Inner loop #2: deterministic surrogate (tests only) ────────────────────
def surrogate_evaluate(workflow: Workflow, project_dir: str, instances: list[str]) -> EvalResult:
    """Deterministic scorer: a stable function of graph features + knob values.

    Distinct mutations land at distinct scores, so selection, archive placement,
    reflection and tournament pressure all operate on real signal without any
    DSH subprocess or LLM call.
    """
    features = compute_features(workflow)
    knob_sum = sum(
        float(v) for v in workflow.knob_values.values() if isinstance(v, (int, float))
    )
    prompt_len = sum(
        len(str(getattr(n, "prompt", "") or "")) for n in workflow.nodes.values()
    )
    score = sum(features) * 0.25 + knob_sum * 0.35 + len(workflow.nodes) * 0.5 + prompt_len * 0.001
    return EvalResult(score=round(score, 4), benchmark_score=round(score, 4))


def _pick_evaluator():
    scorer = os.environ.get("REFACTORY_SCORER", "dsh")
    if scorer == "surrogate":
        return surrogate_evaluate
    if scorer == "dsh":
        return dsh_evaluate
    raise ValueError(f"unknown REFACTORY_SCORER: {scorer}")


# ── Config / engine construction ───────────────────────────────────────────
def build_config() -> SwarmConfig:
    """The swarm configuration. `designer_count=0` keeps the loop deterministic
    (no DesignerAgent LLM calls); the eight operators are driven by
    `WeightedRandomStrategy` with refactory's shipped weights."""
    return SwarmConfig(
        benchmark="srf",
        budget=int(os.environ.get("REFACTORY_BUDGET", "40")),
        population_size=int(os.environ.get("REFACTORY_POPULATION", "8")),
        tournament_size=int(os.environ.get("REFACTORY_TOURNAMENT", "3")),
        designer_count=0,
        training_instances=[DEFAULT_TASK],
        plateau_window=3,
        plateau_threshold=float(os.environ.get("REFACTORY_PLATEAU_THRESHOLD", "0.01")),
        diversity_floor=float(os.environ.get("REFACTORY_DIVERSITY_FLOOR", "0.2")),
        early_stop_unchanged=int(os.environ.get("REFACTORY_EARLY_STOP", "3")),
    )


def build_engine(config: SwarmConfig, evaluator_fn, project_dir: Path | None = None):
    """Assemble the production `SwarmEngine` with a pluggable inner loop."""
    evaluator = SwarmEvaluator(config, evaluator_fn=evaluator_fn, project_dir=project_dir)
    novelty = NoveltyFilter(min_edit_distance=3)
    return SwarmEngine(
        config=config,
        evaluator=evaluator,
        novelty_filter=novelty,
        project_dir=project_dir,
    )


def load_base_workflow() -> Workflow:
    """Load the seed workflow (default: SRF's gepa mode) from its graph.json."""
    path = Path(os.environ.get("REFACTORY_TARGET_GRAPH", str(DEFAULT_TARGET)))
    return Workflow.from_dict(json.loads(path.read_text()))


# ── Persistent state for the interactive (per-generation) graph ───────────
def _save_engine_state(engine: SwarmEngine, generation: int) -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    POP_DIR.mkdir(parents=True, exist_ok=True)
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    engine.archive.save(ARCHIVE_DIR)
    STATE.write_text(json.dumps({
        "generation": generation,
        "score_trajectory": engine._score_trajectory,
        "initial_diversity": engine._initial_diversity,
        "top_ids_history": [sorted(s) for s in engine._top_ids_history],
        "budget_consumed": engine._budget.consumed,
        "total_cost_usd": engine._budget.total_cost_usd,
    }))


def _rehydrate_engine(engine: SwarmEngine, population: Population) -> None:
    """Restore the engine's cross-process private state from disk + population."""
    engine._archive = MAPElitesArchive.load(ARCHIVE_DIR)
    data = json.loads(STATE.read_text())
    engine._score_trajectory = data.get("score_trajectory", [])
    engine._initial_diversity = data.get("initial_diversity", 0.0)
    engine._top_ids_history = [frozenset(s) for s in data.get("top_ids_history", [])]
    engine._budget._consumed = int(data.get("budget_consumed", 0))
    engine._budget._cost_usd = float(data.get("total_cost_usd", 0.0))
    # NoveltyFilter has no disk form; re-seed it from everything still alive.
    seen: set[str] = set()
    for ind in list(engine._archive.all_individuals()) + population.individuals:
        if ind.id in seen:
            continue
        seen.add(ind.id)
        engine._novelty.add(Workflow.from_dict(ind.workflow_data))  # type: ignore[arg-type]


# ── Commands ───────────────────────────────────────────────────────────────
def calibrate() -> None:
    """Seed generation 0 from the base workflow and persist the swarm state."""
    ROOT.mkdir(parents=True, exist_ok=True)
    config = build_config()
    CONFIG.write_text(json.dumps(config.model_dump(mode="json"), indent=2))
    base = load_base_workflow()
    engine = build_engine(config, _pick_evaluator(), project_dir=Path.cwd())
    population = engine.seed(base, config)
    population.save(POP_DIR)
    _save_engine_state(engine, generation=0)
    print(json.dumps({
        "generation": 0,
        "population_size": population.size,
        "seed_knobs": sorted(base.knob_values),
    }))


def evolve() -> None:
    """Run exactly one `SwarmEngine.evolve_generation` (evaluate → reflect →
    mutate → archive) and persist the advanced generation."""
    config = build_config()
    population = Population.load(POP_DIR)
    engine = build_engine(config, _pick_evaluator(), project_dir=Path.cwd())
    _rehydrate_engine(engine, population)
    generation = int(json.loads(STATE.read_text())["generation"])
    summary = engine.evolve_generation(population, generation, project_dir=str(Path.cwd()))
    population.save(POP_DIR)
    _save_engine_state(engine, generation=generation + 1)
    print(json.dumps({
        "generation": generation + 1,
        "best_score": summary.best_score,
        "mean_score": summary.mean_score,
        "diversity": summary.diversity,
        "mutations_applied": [m.operator.value for m in summary.mutations_applied],
        "novel_count": summary.novel_count,
        "rejected_duplicates": summary.rejected_duplicates,
    }))


def check_converge() -> None:
    """Deterministic gate: report PROCEED once the swarm has terminated."""
    config = build_config()
    population = Population.load(POP_DIR)
    engine = build_engine(config, _pick_evaluator(), project_dir=Path.cwd())
    _rehydrate_engine(engine, population)
    generation = int(json.loads(STATE.read_text())["generation"])
    print("PROCEED" if engine._should_terminate(generation) else "RELOOP")


def status() -> None:
    """Report the current best individual + archive state."""
    population = Population.load(POP_DIR)
    archive = MAPElitesArchive.load(ARCHIVE_DIR)
    best = archive.best() or population.best()
    print(json.dumps({
        "generation": int(json.loads(STATE.read_text())["generation"]),
        "archive_size": archive.size,
        "best_id": best.id if best else None,
        "best_score": best.score if best else 0.0,
        "best_features": list(best.features) if best else [],
        "population_size": population.size,
    }, indent=2))


def run() -> None:
    """Run the full faithful outer loop to convergence in one process."""
    ROOT.mkdir(parents=True, exist_ok=True)
    config = build_config()
    base = load_base_workflow()
    engine = build_engine(config, _pick_evaluator(), project_dir=Path.cwd())
    result = engine.run(base, project_dir=str(Path.cwd()))
    RESULT.write_text(json.dumps(result.model_dump(mode="json"), indent=2))
    print(json.dumps({
        "best_score": result.best_score,
        "holdout_score": result.holdout_score,
        "overfit_flag": result.overfit_flag,
        "generations_completed": result.generations_completed,
        "total_evaluations": result.total_evaluations,
        "archive_size": result.archive_size,
        "pareto_size": len(result.pareto_front),
        "convergence_reason": result.convergence_reason,
    }, indent=2))


# `evaluate` and `reflect` are subsumed by `evolve_generation` (refactory runs
# them inside one generation step). They remain as no-op aliases so the older
# decomposed graph does not error, and each records its state to disk.
def evaluate() -> None:
    """Alias: a generation's evaluation happens inside `evolve_generation`."""
    print(json.dumps({"note": "evaluation is part of evolve_generation; run evolve"}))


def reflect() -> None:
    """Alias: contrastive reflection happens inside `evolve_generation`."""
    print(json.dumps({"note": "reflection is part of evolve_generation; run evolve"}))


_COMMANDS = {
    "calibrate": calibrate, "evaluate": evaluate, "reflect": reflect,
    "evolve": evolve, "check_converge": check_converge, "status": status,
    "run": run,
}


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in _COMMANDS:
        print(f"usage: python -m refactory_dsh.ops.outer_loop <{'|'.join(_COMMANDS)}>", file=sys.stderr)
        sys.exit(2)
    _COMMANDS[sys.argv[1]]()


if __name__ == "__main__":
    main()
