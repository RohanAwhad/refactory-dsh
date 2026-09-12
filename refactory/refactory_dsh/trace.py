"""Per-candidate trace facts for the outer loop.

One inner run produces one *trace*: what the candidate was, what the run did,
what it scored, what failed, and where the raw evidence lives. This module
collects that mechanically and writes it beside the run.

Two layers, deliberately separate:

- **Facts** (this module) are extracted, never authored. A model that summarizes
  failures can also invent or omit them, and every downstream decision is
  downstream of the facts, so they must be exact and reproducible.
- **Analysis** is model-authored and lives in the node that reads this summary.
  ``trace-summary.md`` carries the facts and a clearly marked section for the
  analysis to be appended to.

The summary is the entry point, not a cage: it links to every raw artifact so an
analyzing agent can drill into the evidence when the summary is not enough.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from factory.cycle_analyzer import CycleRecord, ScienceRunAnalyzer
from factory.workflow.primitives import Workflow

TRACES_DIR = Path(".factory") / "outer_loop" / "traces"
INDEX = "index.json"
ANALYSIS_HEADING = "## Analysis (model-authored)"


def workflow_key(workflow: Workflow) -> str:
    """Content-addressed id for a candidate workflow, stable across runs."""
    blob = json.dumps(workflow.to_dict(), sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:12]


def _shape(workflow: Workflow, parent: Workflow | None) -> dict[str, object]:
    """Node/edge counts, node ids, and the structural delta against the parent."""
    nodes = set(workflow.nodes)
    edges = {(e.source, e.target) for e in workflow.edges}
    shape: dict[str, object] = {
        "name": workflow.name,
        "nodes": len(nodes),
        "edges": len(edges),
        "node_ids": sorted(nodes),
        "knob_values": dict(workflow.knob_values),
    }
    if parent is not None:
        parent_nodes = set(parent.nodes)
        parent_edges = {(e.source, e.target) for e in parent.edges}
        shape["diff_vs_parent"] = {
            "nodes_added": sorted(nodes - parent_nodes),
            "nodes_removed": sorted(parent_nodes - nodes),
            "edges_added": sorted(f"{s}->{t}" for s, t in edges - parent_edges),
            "edges_removed": sorted(f"{s}->{t}" for s, t in parent_edges - edges),
            "knobs_changed": {
                k: {"from": parent.knob_values.get(k), "to": v}
                for k, v in workflow.knob_values.items()
                if parent.knob_values.get(k) != v
            },
        }
    return shape


def _observations(record: CycleRecord, run: dict[str, object]) -> list[str]:
    """Factual aggregations over the run. No interpretation, only counted facts."""
    notes: list[str] = []
    if record.stuck_detected:
        notes.append(
            f"run did not produce a score (error={run.get('error')!r}); "
            "this is a wiring or budget failure, not a weak candidate"
        )
    if not record.experiments:
        notes.append("no accept/reject records were written by the run")
    errors = [e for e in record.experiments if e.verdict == "error"]
    reverts = [e for e in record.experiments if e.verdict == "revert"]
    keeps = [e for e in record.experiments if e.verdict == "keep"]
    notes.append(
        f"verdicts: kept={len(keeps)} reverted={len(reverts)} errored={len(errors)} "
        f"keep_rate={record.keep_rate:.2f}"
    )
    if keeps and reverts:
        best_keep = max((e.score_after or 0.0) for e in keeps)
        best_revert = max((e.score_after or 0.0) for e in reverts)
        notes.append(
            f"best kept score={best_keep} vs best reverted score={best_revert}"
        )
    if record.consecutive_reverts:
        notes.append(f"{record.consecutive_reverts} trailing rejection(s)")
    if record.plateau_detected:
        notes.append("run reported a plateau (stagnation counter >= 3)")
    return notes


def collect(
    *,
    work_dir: Path,
    project_dir: Path,
    workflow: Workflow,
    parent: Workflow | None,
    mode: str,
    task: str,
    budget: str,
    knobs: dict[str, object],
    exit_code: int | None,
    duration_s: float,
    stderr: str,
) -> tuple[dict[str, object], CycleRecord | None]:
    """Extract the trace facts for one inner run, with its cycle record.

    Returns the facts and the ``CycleRecord`` the run produced (``None`` when the
    run wrote no readable record). Writes nothing; :func:`write` persists the
    facts. ``work_dir`` is the run's own artifact directory, ``project_dir`` is
    the outer-loop project the trace index lives under.
    """
    record = ScienceRunAnalyzer(work_dir, mode=mode).latest()
    run: dict[str, object] = {
        "mode": mode,
        "task": task,
        "budget": budget,
        "knobs": knobs,
        "exit_code": exit_code,
        "duration_s": round(duration_s, 3),
        "work_dir": str(work_dir),
        "stderr_tail": stderr[-2000:],
    }
    if record is None:
        return {
            "key": workflow_key(workflow),
            "proposal": None,
            "parent_key": workflow_key(parent) if parent else None,
            "workflow": workflow.to_dict(),
            "shape": _shape(workflow, parent),
            "run": run,
            "outcome": None,
            "observations": ["the run wrote no readable cycle record"],
            "artifacts": [],
        }, None

    latest_score = record.score_end
    verdict = "unknown"
    error: str | None = None
    parent_score: float | None = None
    if record.experiments:
        last = record.experiments[-1]
        verdict = last.verdict
        parent_score = last.score_before
    for exp in record.experiments:
        if exp.verdict == "error":
            error = "one or more experiments errored"
            break

    artifacts = sorted(str(p) for p in work_dir.iterdir() if p.is_file()) if work_dir.is_dir() else []
    return {
        "key": workflow_key(workflow),
        "proposal": None,
        "parent_key": workflow_key(parent) if parent else None,
        "workflow": workflow.to_dict(),
        "shape": _shape(workflow, parent),
        "run": run,
        "outcome": {
            "score": latest_score,
            "parent_score": parent_score,
            "delta": (
                latest_score - parent_score
                if isinstance(latest_score, (int, float))
                and isinstance(parent_score, (int, float))
                else None
            ),
            "verdict": verdict,
            "error": error,
            "metrics": (record.eval_details or {}).get("metrics", {}),
            "kept": record.kept,
            "reverted": record.reverted,
            "errored": record.errored,
            "keep_rate": record.keep_rate,
            "score_trajectory": record.score_trajectory,
            "stuck": record.stuck_detected,
        },
        "observations": _observations(record, run),
        "artifacts": artifacts,
    }, record


def _link(path: str) -> str:
    return f"[{Path(path).name}](file://{path})"


def render(facts: dict[str, object]) -> str:
    """Render the facts as Markdown, with a links section and an analysis slot."""
    run = facts["run"]
    outcome = facts.get("outcome") or {}
    shape = facts["shape"]
    lines = [
        f"# Trace — candidate `{facts['key']}`",
        "",
        "Facts below are extracted mechanically from the run's artifacts. "
        "They are not model-authored; the analysis section at the end is.",
        "",
        "## Proposal",
        f"- optimizer / operator: `{(facts.get('proposal') or {}).get('operator', 'seed')}`",
        f"- target: `{(facts.get('proposal') or {}).get('target_node', '—')}`",
        f"- rationale: {(facts.get('proposal') or {}).get('rationale', '—')}" or "—",
        f"- parent: `{facts.get('parent_key') or '—'}`",
        "",
        "## Run",
        f"- mode `{run['mode']}` · task `{run['task']}` · budget {run['budget']} · exit {run['exit_code']}",
        f"- duration {run['duration_s']}s · knobs `{json.dumps(run['knobs'])}`",
        f"- work dir `{run['work_dir']}`",
        "",
        "## Graph",
        f"- {shape['nodes']} nodes, {shape['edges']} edges",
        f"- knobs `{json.dumps(shape['knob_values'])}`",
    ]
    diff = shape.get("diff_vs_parent")
    if diff:
        lines += [
            f"- nodes added: {diff['nodes_added'] or 'none'}",
            f"- nodes removed: {diff['nodes_removed'] or 'none'}",
            f"- edges added: {diff['edges_added'] or 'none'}",
            f"- edges removed: {diff['edges_removed'] or 'none'}",
            f"- knobs changed: `{json.dumps(diff['knobs_changed'])}`",
        ]

    if outcome:
        lines += [
            "",
            "## Outcome",
            f"- score **{outcome.get('score')}** vs parent {outcome.get('parent_score')} "
            f"(delta {outcome.get('delta')})",
            f"- verdict **{outcome.get('verdict')}** · kept {outcome.get('kept')} · "
            f"reverted {outcome.get('reverted')} · errored {outcome.get('errored')}",
            f"- metrics `{json.dumps(outcome.get('metrics', {}))}`",
            f"- score trajectory {outcome.get('score_trajectory')}",
        ]
        if outcome.get("error"):
            lines.append(f"- error: {outcome['error']}")
        if outcome.get("stuck"):
            lines.append("- **the run produced no score at all** (wiring or budget failure)")

    lines += ["", "## Observations", *(f"- {note}" for note in facts["observations"])]

    lines += ["", "## Raw evidence"]
    lines += [f"- {_link(p)}" for p in facts.get("artifacts", [])] or ["- none"]

    if run.get("stderr_tail"):
        lines += ["", "## stderr (tail)", "```", str(run["stderr_tail"]).strip(), "```"]

    lines += ["", ANALYSIS_HEADING, "", "_(not yet written)_", ""]
    return "\n".join(lines)


def annotate_from_population(project_dir: Path) -> int:
    """Join each trace with the proposal that produced it.

    The evaluator sees only the candidate workflow, never the ``Individual``
    that carries the mutation record and the parent link, so the trace is
    complete only after the generation's population is on disk. This fills in
    ``proposal`` and re-renders the summaries it changes.

    Returns the number of traces updated. Missing or unreadable population files
    are not an error: a run may have been interrupted before the population was
    saved, and the trace facts collected so far remain valid.
    """
    population_path = project_dir / ".factory" / "outer_loop" / "population" / "population.json"
    if not population_path.exists():
        return 0
    try:
        individuals = json.loads(population_path.read_text())
    except json.JSONDecodeError:
        return 0

    updated = 0
    by_id: dict[str, Workflow] = {}
    for individual in individuals:
        data = individual.get("workflow_data")
        if isinstance(data, dict) and individual.get("id"):
            try:
                by_id[str(individual["id"])] = Workflow.from_dict(data)  # type: ignore[arg-type]
            except Exception:  # noqa: BLE001 - a malformed candidate must not abort the join
                continue

    for individual in individuals:
        data = individual.get("workflow_data")
        if not isinstance(data, dict):
            continue
        try:
            workflow = Workflow.from_dict(data)  # type: ignore[arg-type]
            key = workflow_key(workflow)
        except Exception:  # noqa: BLE001 - a malformed candidate must not abort the join
            continue
        json_path = project_dir / TRACES_DIR / f"{key}.json"
        # Match by the workflow the trace actually recorded, not only by content
        # key: an evaluation can write its trace under a key computed before the
        # individual was persisted, and a candidate that never matches would be
        # permanently un-annotated -- missing its rationale and prediction from
        # the report and dropping out of the leaderboard. Matching by identity
        # also surfaces duplicates of the same candidate written under two keys,
        # which the report would otherwise count twice.
        found = _facts_for_workflow(project_dir, data)
        if found is None:
            continue
        facts, stale = found
        # Sweep duplicates before deciding whether anything changed: an
        # already-annotated candidate must still have its stale copies retired,
        # or the report keeps counting it more than once.
        for old in stale:
            if old != json_path:
                old.unlink(missing_ok=True)
                old.with_suffix(".md").unlink(missing_ok=True)
        record = individual.get("mutation_record") or {}
        proposal = {
            "operator": record.get("operator"),
            "target_node": record.get("target_node"),
            "before": record.get("before"),
            "after": record.get("after"),
            "rationale": record.get("rationale"),
        }
        parent_id = individual.get("parent_id")
        parent_wf = by_id.get(str(parent_id)) if parent_id else None
        shape = _shape(workflow, parent_wf)
        if (
            facts.get("proposal") == proposal
            and facts.get("parent_id") == parent_id
            and facts.get("shape") == shape
        ):
            continue
        facts["proposal"] = proposal if record else None
        facts["parent_id"] = parent_id
        facts["generation"] = individual.get("generation")
        facts["shape"] = shape
        json_path.write_text(json.dumps(facts, indent=2, default=str))
        (project_dir / TRACES_DIR / f"{key}.md").write_text(render(facts))
        updated += 1
    return updated


class FileTraceSource:
    """A ``factory.outer_loop.optimizers.TraceSource`` over the trace index.

    Lets an upstream optimizer read the gradient without knowing this package's
    file layout.
    """

    def __init__(self, project_dir: Path, max_bytes: int = 60_000) -> None:
        self._traces = Path(project_dir) / TRACES_DIR
        self._max_bytes = max_bytes

    def recent(self, limit: int) -> list[dict[str, object]]:
        """Return up to ``limit`` most recent trace facts, newest last.

        Traces are ordered by file mtime, which is the only ordering that
        survives the rewrite the annotation pass performs. Unreadable traces are
        skipped rather than aborting the read: a partially written file must
        degrade the gradient, not stop the loop.
        """
        index_path = self._traces / INDEX
        if not index_path.exists():
            return []
        try:
            index = json.loads(index_path.read_text())
        except json.JSONDecodeError:
            return []
        entries: list[tuple[float, str]] = []
        for key, entry in index.items():
            facts_path = entry.get("facts") if isinstance(entry, dict) else None
            path = Path(facts_path) if facts_path else self._traces / f"{key}.json"
            if path.exists():
                entries.append((path.stat().st_mtime, str(path)))
        entries.sort()
        facts_list: list[dict[str, object]] = []
        for _mtime, path in entries[-limit:]:
            try:
                loaded = json.loads(Path(path).read_text())
            except (json.JSONDecodeError, OSError):
                continue
            if isinstance(loaded, dict):
                if len(json.dumps(loaded, default=str)) > self._max_bytes:
                    loaded.pop("workflow", None)
                facts_list.append(loaded)
        return facts_list


def _facts_for_workflow(
    project_dir: Path, data: dict
) -> tuple[dict[str, object], list[Path]] | None:
    """Find every trace whose recorded workflow equals ``data``.

    A linear scan over a run's traces; runs hold tens, not thousands. All matches
    are returned so the caller keeps the canonical one and retires the rest.
    """
    traces = project_dir / TRACES_DIR
    if not traces.is_dir():
        return None
    matches: list[tuple[dict[str, object], Path]] = []
    for candidate in traces.glob("*.json"):
        if candidate.stem == INDEX.removesuffix(".json"):
            continue
        try:
            loaded = json.loads(candidate.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if isinstance(loaded, dict) and loaded.get("workflow") == data:
            matches.append((loaded, candidate))
    if not matches:
        return None
    matches.sort(key=lambda m: m[1].name)
    first, paths = matches[0]
    return first, [path for _loaded, path in matches]


def write(facts: dict[str, object], project_dir: Path) -> tuple[Path, Path]:
    """Persist the facts and their Markdown rendering; update the trace index."""
    traces = project_dir / TRACES_DIR
    traces.mkdir(parents=True, exist_ok=True)
    key = str(facts["key"])
    json_path = traces / f"{key}.json"
    md_path = traces / f"{key}.md"
    # The paths travel inside the facts so an analyzing agent can be handed the
    # summary and follow it to the raw evidence without knowing this layout.
    facts["paths"] = {"summary": str(md_path), "facts": str(json_path)}
    json_path.write_text(json.dumps(facts, indent=2, default=str))
    md_path.write_text(render(facts))

    index_path = traces / INDEX
    index = {}
    if index_path.exists():
        try:
            index = json.loads(index_path.read_text())
        except json.JSONDecodeError:
            index = {}
    outcome = facts.get("outcome") or {}
    index[key] = {
        "summary": str(md_path),
        "facts": str(json_path),
        "score": outcome.get("score"),
        "verdict": outcome.get("verdict"),
        "operator": (facts.get("proposal") or {}).get("operator"),
        "work_dir": facts["run"]["work_dir"],
    }
    index_path.write_text(json.dumps(index, indent=2, default=str))
    return json_path, md_path
