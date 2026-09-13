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
import time
from pathlib import Path

# ── refactory outer-loop components (imported, never reimplemented) ────────
from factory.cycle_analyzer import CycleRecord, ScienceRunAnalyzer
from factory.outer_loop.engine import SwarmEngine
from factory.outer_loop.evaluator import EvaluationOutcome, SwarmEvaluator
from factory.outer_loop.models import EvalResult, SwarmConfig
from factory.outer_loop.autoresearch_optimizer import AutoresearchOptimizer
from factory.outer_loop.mutations import WeightedRandomStrategy
from factory.outer_loop.optimizers import OptimizerRegistry

# Importing the reasoning optimizer registers it under its name. It is kept out
# of the base optimizers module so that module stays free of model dependencies.
import factory.outer_loop.autoresearch_optimizer  # noqa: F401  (registration side effect)
import factory.outer_loop.file_bridge_optimizer  # noqa: F401  (registration side effect)
from factory.outer_loop.population import MAPElitesArchive, Population
from factory.outer_loop.similarity import NoveltyFilter, compute_features
from factory.workflow.primitives import Workflow

from refactory_dsh import knobs, packages, report, steering, trace
from refactory_dsh.model import DshModelClient
from refactory_dsh.paths import dsh_root, node_bin, python, srf_graphs, venv_bin
from refactory_dsh.trace import FileTraceSource

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
    key = trace.workflow_key(workflow)
    run_root = Path(project_dir) / ".factory" / "outer_loop" / "runs" / key
    graphs_dir = run_root / "graphs"
    graphs_dir.mkdir(parents=True, exist_ok=True)
    (graphs_dir / f"{mode}.graph.json").write_text(json.dumps(workflow.to_dict(), indent=2))
    workdir = run_root / "work"
    workdir.mkdir(parents=True, exist_ok=True)

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
    started = time.time()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, cwd=DSH_ROOT, timeout=timeout)
    except subprocess.TimeoutExpired:
        return EvaluationOutcome(
            result=EvalResult(
                score=0.0,
                details={"error": "dsh_inner_loop_timeout", "workdir": str(workdir)},
            ),
            cycle_record=_timeout_record(workdir, mode, timeout),
        )
    score = _read_best_score(proc.stdout, workdir, mode)
    facts, record = trace.collect(
        work_dir=workdir,
        project_dir=Path(project_dir),
        workflow=workflow,
        parent=None,
        mode=mode,
        task=task,
        budget=budget,
        knobs=dict(workflow.knob_values),
        exit_code=proc.returncode,
        duration_s=time.time() - started,
        stderr=proc.stderr,
    )
    json_path, md_path = trace.write(facts, Path(project_dir))
    return EvaluationOutcome(
        result=EvalResult(
            score=score,
            benchmark_score=score,
            details={
                "workdir": str(workdir),
                # The engine replaces `score` with its composite fitness
                # (0.6*benchmark + 0.2*hygiene + cost/complexity terms), so the
                # task's own score is kept here. Without it the archive's numbers
                # are not comparable with the inner run's.
                "raw_score": score,
                "trace": str(md_path),
                "trace_facts": str(json_path),
                "mode": mode,
                "task": task,
                "budget": budget,
                "knobs": dict(workflow.knob_values),
                "exit_code": proc.returncode,
                "duration_s": round(time.time() - started, 3),
                "stderr": proc.stderr[-2000:],
            },
        ),
        cycle_record=record,
    )


def _timeout_record(workdir: Path, mode: str, timeout: int) -> CycleRecord:
    """A cycle record for a run that was killed before it produced anything.

    Distinguishing "ran and scored badly" from "never ran" is load-bearing: a
    timeout and a genuine low score are otherwise the same number. The record
    marks ``stuck_detected`` and carries the reason in ``eval_details``.
    """
    return CycleRecord(
        cycle_number=1,
        mode=mode,
        started_at=None,
        ended_at=None,
        duration_s=float(timeout),
        score_start=None,
        score_end=None,
        score_delta=None,
        score_trajectory=[],
        experiments=[],
        kept=0,
        reverted=0,
        errored=0,
        total_cost_usd=0.0,
        stuck_detected=True,
        eval_artifacts=[],
        node_trace={},
        eval_details={
            "error": "dsh_inner_loop_timeout",
            "timeout_s": timeout,
            "workdir": str(workdir),
        },
    )


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

def fitness_weights(settings: dict) -> dict:
    """Composite-fitness weights, from steering when set.

    The default is the shipped weighting, so a run that does not steer is
    unchanged. Setting ``{"benchmark": 1.0}`` searches on the task metric alone,
    which is what a benchmark run usually wants: the default adds a constant
    share for hygiene, cost and complexity regardless of task performance.
    """
    raw = settings.get("fitness_weights")
    if not isinstance(raw, dict) or not raw:
        return {
            "benchmark": 0.6, "hygiene": 0.2, "cost": 0.1, "complexity": 0.1
        }
    out = {"benchmark": 0.6, "hygiene": 0.2, "cost": 0.1, "complexity": 0.1}
    for key, value in raw.items():
        if isinstance(value, (int, float)) and value >= 0:
            out[str(key)] = float(value)
    return out


def build_config() -> SwarmConfig:
    """The swarm configuration. `designer_count=0` keeps the loop deterministic
    (no DesignerAgent LLM calls); the eight operators are driven by
    `WeightedRandomStrategy` with refactory's shipped weights."""
    settings = steering.load(Path.cwd())
    # An epoch is one pass and a mini-batch is what it evaluates, which is the
    # vocabulary the rest of the loop uses. The population defaults to the batch
    # size, and the budget to epochs x batch, so the trap where the starting
    # population consumed everything and no generated candidate was ever tried
    # cannot arise from the defaults.
    epochs = int(os.environ.get("REFACTORY_EPOCHS", "5"))
    batch_size = int(os.environ.get("REFACTORY_BATCH_SIZE", "2"))
    population = int(os.environ.get("REFACTORY_POPULATION", str(batch_size)))
    budget = int(os.environ.get("REFACTORY_BUDGET", str(max(epochs * batch_size, population + 1))))
    if budget <= population:
        # Every evaluation goes on the starting population, so the search can
        # propose all it likes and never try any of it -- while still reporting a
        # new generation. Say so before the run rather than after.
        print(json.dumps({
            "warning": "budget_starves_offspring",
            "budget": budget, "population": population,
            "epochs": epochs, "batch_size": batch_size,
            "detail": "the budget is consumed evaluating the starting population, "
                      "so no generated candidate can be evaluated; set "
                      "REFACTORY_BUDGET above REFACTORY_POPULATION",
        }), file=sys.stderr)
    return SwarmConfig(
        benchmark="srf",
        budget=budget,
        population_size=population,
        tournament_size=int(
            settings.get("tournament_size")
            or os.environ.get("REFACTORY_TOURNAMENT", "3")
        ),
        designer_count=0,
        training_instances=[DEFAULT_TASK],
        plateau_window=3,
        plateau_threshold=float(os.environ.get("REFACTORY_PLATEAU_THRESHOLD", "0.01")),
        fitness_weights=fitness_weights(settings),
        diversity_floor=float(os.environ.get("REFACTORY_DIVERSITY_FLOOR", "0.2")),
        early_stop_unchanged=int(os.environ.get("REFACTORY_EARLY_STOP", "3")),
    )


def build_engine(config: SwarmConfig, evaluator_fn, project_dir: Path | None = None):
    """Assemble the production `SwarmEngine` with a pluggable inner loop.

    The optimizer is chosen by name from `REFACTORY_OPTIMIZER` (default
    ``random``). A reasoning optimizer additionally needs a model client, which
    is supplied here so the upstream optimizer stays free of transport concerns.
    An unknown name fails loud rather than silently falling back: running the
    wrong optimizer invalidates every result of the run.
    """
    # Refuse to search a mode with nothing to optimize, and report a surface that
    # has shrunk since the mode was created.
    knobs.require_surface(resolve_mode(Path(project_dir) if project_dir else None))
    evaluator = SwarmEvaluator(config, evaluator_fn=evaluator_fn, project_dir=project_dir)
    novelty = NoveltyFilter(min_edit_distance=3)
    settings = steering.load(Path(project_dir or Path.cwd()))
    # Steering may switch the optimizer between generations; the environment
    # variable stays the run-level default so a preset still decides where a run
    # starts.
    name = str(settings.get("optimizer") or os.environ.get("REFACTORY_OPTIMIZER", "random")).strip() or "random"
    if name not in OptimizerRegistry.names():
        raise SystemExit(
            f"unknown optimizer {name!r}; known: {OptimizerRegistry.names()}"
        )
    strategy = WeightedRandomStrategy(weights=steering.operator_weights(settings))
    if name == "autoresearch":
        # The reasoning optimizer needs a model; the transport lives here so the
        # upstream optimizer stays free of it.
        optimize = OptimizerRegistry.create(name, client=DshModelClient())
    elif name == "random":
        optimize = OptimizerRegistry.create(name, strategy=strategy)
    else:
        optimize = OptimizerRegistry.create(name)
    if optimize is None:  # pragma: no cover - guarded by the name check above
        raise SystemExit(f"optimizer {name!r} could not be constructed")
    frozen = steering.frozen_nodes(settings)
    if frozen:
        config.frozen_node_ids = sorted(set(config.frozen_node_ids) | frozen)
    return SwarmEngine(
        config=config,
        evaluator=evaluator,
        strategy=strategy,
        novelty_filter=novelty,
        project_dir=project_dir,
        optimizer=optimize,
        trace_source=FileTraceSource(Path(project_dir or Path.cwd())),
        frozen_knobs=steering.frozen_knobs(settings),
    )


def resolve_mode(project_dir: Path | None = None) -> Path:
    """The inner mode to optimize, by package-qualified name or by path.

    ``REFACTORY_MODE`` names a registered mode, which is how a mode created here
    is normally selected: a name the catalogue can resolve, not a path someone has
    to remember. ``REFACTORY_TARGET_GRAPH`` remains as a developer override for a
    graph that is not registered.
    """
    name = os.environ.get("REFACTORY_MODE", "").strip()
    if name:
        project = Path(project_dir or Path.cwd())
        resolved = packages.resolve(project, name)
        if resolved is None:
            available = ", ".join(packages.enabled_ids(project)) or "none registered"
            raise SystemExit(f"unknown or disabled mode {name!r}; available: {available}")
        return Path(resolved)
    return Path(os.environ.get("REFACTORY_TARGET_GRAPH", str(DEFAULT_TARGET)))


def load_base_workflow() -> Workflow:
    """Load the seed workflow from the selected mode's graph."""
    return Workflow.from_dict(json.loads(resolve_mode().read_text()))


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


def _write_analysis(engine, generation: int) -> str | None:
    """Persist a reasoning optimizer's diagnosis for this generation.

    The diagnosis explains *why* the candidates were proposed, which is the part
    a user needs in order to steer the search. It is written as an artifact so
    the GUI can render it and so it survives the run.
    """
    diagnosis = getattr(engine.optimizer, "last_diagnosis", "")
    if not diagnosis:
        return None
    out_dir = Path(".factory") / "outer_loop" / "analysis"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"generation-{generation}.md"
    path.write_text(
        f"# Generation {generation} — optimizer diagnosis\n\n"
        f"Optimizer: `{engine.optimizer.name}`\n\n{diagnosis}\n"
    )
    return str(path)


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
    annotated = trace.annotate_from_population(Path.cwd())
    analysis_path = _write_analysis(engine, generation + 1)
    print(json.dumps({
        "generation": generation + 1,
        "best_score": summary.best_score,
        "mean_score": summary.mean_score,
        "traces_annotated": annotated,
        "offspring_evaluated": summary.offspring_evaluated,
        "rejected_invalid": summary.rejected_invalid,
        "analysis": analysis_path,
        "diversity": summary.diversity,
        "mutations_applied": [m.operator.value for m in summary.mutations_applied],
        "novel_count": summary.novel_count,
        "rejected_duplicates": summary.rejected_duplicates,
    }))


def check_converge() -> None:
    """Scheduler: end the epoch, then report PROCEED or RELOOP.

    An epoch is one pass over a mini-batch. The epoch counter advances here, so
    the boundary is a recorded fact rather than something inferred from the
    generation counter, and a run that exhausts its epochs stops for a reason the
    report can state.
    """
    state = _graph_state()
    epochs = int(os.environ.get("REFACTORY_EPOCHS", "5"))
    epoch = int(state.get("epoch", 0)) + 1
    state["epoch"] = epoch
    _save_graph_state(state)
    if epoch >= epochs:
        print("PROCEED")
        print(json.dumps({"scheduler": "epochs_exhausted", "epoch": epoch, "epochs": epochs}),
              file=sys.stderr)
        return
    _check_converge_engine()


def _check_converge_engine() -> None:
    """The swarm's own termination test, used inside the epoch budget."""
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
# ── Decomposed graph steps ─────────────────────────────────────────────────
# The monolithic `evolve` runs evaluate → reflect → propose → apply in one call.
# These ops expose the same steps individually so the outer-loop graph can drive
# them as separate nodes, with a model-owned `propose` node in between. They call
# the engine's public step methods, so both flows enforce identical rules.

GRAPH_STATE = ROOT / "graph_state.json"


def _graph_state() -> dict:
    if GRAPH_STATE.exists():
        try:
            return json.loads(GRAPH_STATE.read_text())
        except json.JSONDecodeError:
            return {}
    return {}


def _save_graph_state(state: dict) -> None:
    GRAPH_STATE.parent.mkdir(parents=True, exist_ok=True)
    GRAPH_STATE.write_text(json.dumps(state, indent=2))


def _live_engine():
    """Build an engine over the persisted population, with its cross-process state."""
    config = build_config()
    population = Population.load(POP_DIR)
    engine = build_engine(config, _pick_evaluator(), project_dir=Path.cwd())
    _rehydrate_engine(engine, population)
    return config, population, engine



# ── PyTorch-shaped loop ────────────────────────────────────────────────────
# The loop is an optimization loop, so it is named like one:
#   init      - initialise the parameters (build the starting population)
#   forward   - run the mode once per candidate in the batch
#   loss      - turn those runs into a number
#   backward  - compute the gradient from the traces
#   optimizer - the agent that decides the update
#   step      - apply it
#   scheduler - decide whether the epoch is over
#   report    - the visibility surface
# `forward` and `loss` are separate because a forward pass produces artifacts and
# the loss turns them into the signal the rest of the loop consumes; fusing them
# is what left the gradient implicit in the first place.

BATCH = OUTER if False else ROOT  # batch bookkeeping lives with the run state


def loss() -> None:
    """Read the batch's scores and write the loss the rest of the loop consumes."""
    state = _graph_state()
    traces = _batch_traces(state)
    scores = [
        (t.get("outcome") or {}).get("score")
        for t in traces
        if isinstance((t.get("outcome") or {}).get("score"), (int, float))
    ]
    best = max(scores) if scores else None
    mean = (sum(scores) / len(scores)) if scores else None
    previous = state.get("best_loss")
    payload = {
        "epoch": state.get("epoch", 0),
        "batch": len(traces),
        "scores": scores,
        "mean": mean,
        "best": best,
        "best_ever": max([s for s in [previous, best] if isinstance(s, (int, float))] or [0.0])
        if previous is not None or best is not None
        else None,
        "improved": bool(
            isinstance(best, (int, float)) and isinstance(previous, (int, float)) and best > previous
        ),
    }
    (ROOT / "loss.json").write_text(json.dumps(payload, indent=2, default=str))
    state["best_loss"] = payload["best_ever"]
    _save_graph_state(state)
    print(json.dumps({"step": "loss", **{k: payload[k] for k in ("epoch", "batch", "best", "best_ever", "improved")}}))


def _batch_traces(state: dict) -> list[dict]:
    """The traces produced since the last epoch boundary."""
    from refactory_dsh.trace import TRACES_DIR, INDEX  # noqa: F401 - layout owner

    since = float(state.get("batch_since", 0.0))
    traces = Path(".factory") / "outer_loop" / "traces"
    if not traces.is_dir():
        return []
    out: list[dict] = []
    for path in sorted(traces.glob("*.json")):
        if path.stem == "index" or path.stat().st_mtime < since:
            continue
        try:
            loaded = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if isinstance(loaded, dict):
            out.append(loaded)
    return out


def _append_rejections(rejections: list[dict]) -> None:
    """Persist refusals so the visibility surface can explain them.

    Rejections live only in engine memory for the process that made them, so a
    run that refused a hundred candidates would otherwise look identical to one
    that proposed none.
    """
    if not rejections:
        return
    ROOT.mkdir(parents=True, exist_ok=True)
    with (ROOT / "rejections.jsonl").open("a") as handle:
        for row in rejections:
            handle.write(json.dumps(row, default=str) + "\n")


def report_cmd() -> None:
    """Write the run's visibility report (also refreshed by `apply`)."""
    json_path, md_path = report.write(Path.cwd())
    print(json.dumps({"step": "report", "json": str(json_path), "markdown": str(md_path)}))


def evaluate() -> None:
    """Evaluate only the individuals no earlier step has evaluated."""
    config, population, engine = _live_engine()
    state = _graph_state()
    already = set(state.get("evaluated", []))
    # Everything traced from here on belongs to this mini-batch; `loss` reads the
    # batch by mtime rather than by trying to track ids across processes.
    state["batch_since"] = time.time()
    _save_graph_state(state)
    count = engine.evaluate_population(
        population, str(Path.cwd()), list(config.training_instances), skip_ids=already
    )
    population.save(POP_DIR)
    _save_engine_state(engine, generation=int(state.get("generation", 0)))
    state["evaluated"] = sorted(already | {ind.id for ind in population.individuals})
    _save_graph_state(state)
    trace.annotate_from_population(Path.cwd())
    print(json.dumps({"step": "evaluate", "evaluated": count,
                      "population_size": population.size,
                      "budget_consumed": engine.budget.consumed}))


def reflect() -> None:
    """Reflect on the generation, then write the gradient for the propose node.

    The request file is the whole interface between the deterministic steps and
    the agent node: it carries the trace summaries (with their links), the
    mechanical reflection and the knob surface, in the schema the agent must
    answer in.
    """
    config, population, engine = _live_engine()
    state = _graph_state()
    generation = int(state.get("generation", 0))
    engine.reflect_generation(population, generation)

    ctx = engine.build_proposal_context(generation, population)
    prompt = AutoresearchOptimizer(client=_NullClient()).build_prompt(ctx)
    request = Path(".factory") / "outer_loop" / "proposal-request.md"
    request.parent.mkdir(parents=True, exist_ok=True)
    request.write_text(
        f"# Proposal request — generation {generation}\n\n"
        f"Active optimizer: `{os.environ.get('REFACTORY_OPTIMIZER', 'random')}`\n\n{prompt}"
    )
    (request.parent / "gradient.json").write_text(
        json.dumps(
            {
                "generation": generation,
                "knob_surface": ctx.knob_surface,
                "archive": ctx.archive_stats,
                "traces": [
                    {"key": t.get("key"), "score": (t.get("outcome") or {}).get("score"),
                     "verdict": (t.get("outcome") or {}).get("verdict"),
                     "knobs": (t.get("shape") or {}).get("knob_values"),
                     "proposal": t.get("proposal"),
                     "observations": t.get("observations"),
                     "summary": (t.get("paths") or {}).get("summary")}
                    for t in ctx.traces
                ],
            },
            indent=2,
            default=str,
        )
    )
    print(json.dumps({"step": "reflect", "generation": generation,
                      "request": str(request), "traces": len(ctx.traces)}))


def apply() -> None:
    """Turn the agent's proposals into evaluated offspring and advance.

    Validation happens here, before any evaluation: a candidate that breaks the
    mode contract is recorded and dropped rather than costing an inner run.
    """
    config, population, engine = _live_engine()
    state = _graph_state()
    generation = int(state.get("generation", 0))
    instances = list(config.training_instances)

    engine.set_seed_contract(load_base_workflow())
    bridge = OptimizerRegistry.create("file-bridge")
    if bridge is None:  # pragma: no cover - registered at import
        raise SystemExit("file-bridge optimizer is not registered")
    proposals = bridge.propose(
        engine.build_proposal_context(generation, population), config.population_size
    )
    offspring, novel, duplicates, invalid, _applied = engine.validate_and_select(
        proposals, population, generation
    )
    evaluated = engine.evaluate_offspring(
        offspring, population, generation, instances, str(Path.cwd())
    )
    population.save(POP_DIR)
    state["generation"] = generation + 1
    state["evaluated"] = sorted({ind.id for ind in population.individuals})
    _save_graph_state(state)
    _save_engine_state(engine, generation=generation + 1)
    trace.annotate_from_population(Path.cwd())
    _append_rejections(engine.rejections)
    report.write(Path.cwd())
    print(json.dumps({
        "step": "apply", "generation": generation + 1, "proposed": len(proposals),
        "novel": novel, "rejected_duplicates": duplicates,
        "rejected_invalid": invalid, "evaluated": evaluated,
        "best_score": (population.best().score if population.best() else 0.0),
    }))


class _NullClient:
    """Placeholder client: `build_prompt` is pure, so no call is ever made."""

    def complete(self, prompt: str, *, system: str = "") -> str:  # pragma: no cover
        raise RuntimeError("the reflect step only builds prompts")




_COMMANDS = {
    "calibrate": calibrate, "evaluate": evaluate, "reflect": reflect,
    "apply": apply, "evolve": evolve, "check_converge": check_converge,
    "status": status, "run": run, "report": report_cmd,
    # PyTorch-shaped names for the same steps, so the loop reads as an
    # optimization loop: init / forward / loss / backward / optimizer / step /
    # scheduler / report.
    "init": calibrate, "forward": evaluate, "loss": loss,
    "backward": reflect, "step": apply, "scheduler": check_converge,
}


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in _COMMANDS:
        print(f"usage: python -m refactory_dsh.ops.outer_loop <{'|'.join(_COMMANDS)}>", file=sys.stderr)
        sys.exit(2)
    _COMMANDS[sys.argv[1]]()


if __name__ == "__main__":
    main()
