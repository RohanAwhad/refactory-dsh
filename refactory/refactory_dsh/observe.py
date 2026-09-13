"""What a reader needs to show a run: progress while it happens, a diff of a
candidate against its parent, and a manifest of what to render.

The outer loop stays a Python process. What it must not be is invisible: a run that
can only be read when it finishes is a run nobody watches, and a candidate that
cannot be compared with the one it came from is a number with no explanation.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from refactory_dsh.history import JOURNAL
from refactory_dsh.nn import Graph

#: One line per step, appended as the run goes.
PROGRESS = "progress.jsonl"

#: What a reader should render, with the paths and types to render them as.
MANIFEST = "manifest.json"

#: The order a reader shows them in, and how.
_ARTIFACTS: tuple[tuple[str, str, str], ...] = (
    ("report", "report.md", "text/markdown"),
    ("summary", "report.json", "application/json"),
    ("gradient", "gradient.json", "application/json"),
    ("loss", "loss.json", "application/json"),
    ("config", "config.json", "application/json"),
    ("state", "state.json", "application/json"),
    ("progress", PROGRESS, "application/x-ndjson"),
    ("rejections", "rejections.jsonl", "application/x-ndjson"),
    ("strategy", "strategy.log", "text/plain"),
)


def diff_states(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """What changed between two mode states: knobs, nodes, and edges.

    A candidate is a state dict, so this is the whole answer to "what did this
    candidate do". Without it a leaderboard is a list of numbers whose differences
    have to be inferred from an edit trail.
    """
    return {
        "knobs": _knob_diff(before.get("knobs") or {}, after.get("knobs") or {}),
        "nodes": _node_diff(before.get("graph") or {}, after.get("graph") or {}),
        "edges": _edge_diff(before.get("graph") or {}, after.get("graph") or {}),
    }


def _knob_diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    changed: dict[str, Any] = {}
    for name in sorted(set(before) | set(after)):
        was = (before.get(name) or {}).get("value")
        now = (after.get(name) or {}).get("value")
        if name not in before:
            changed[name] = {"added": True, "after": now}
        elif name not in after:
            changed[name] = {"removed": True, "before": was}
        elif was != now:
            changed[name] = {"before": was, "after": now}
    return changed


def _node_diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    was = (before.get("nodes") or {})
    now = (after.get("nodes") or {})
    added = sorted(set(now) - set(was))
    removed = sorted(set(was) - set(now))
    changed: list[dict[str, Any]] = []
    for node_id in sorted(set(was) & set(now)):
        for field in sorted(set(was[node_id]) | set(now[node_id])):
            old, new = was[node_id].get(field), now[node_id].get(field)
            if old != new:
                changed.append({"id": node_id, "field": field, "before": old, "after": new})
    return {"added": added, "removed": removed, "changed": changed}


def _edge_diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    def keys(graph: dict[str, Any]) -> set[tuple[str, str, str]]:
        return {
            (e["source"], e["target"], e.get("condition") or "")
            for e in graph.get("edges") or ()
        }

    was, now = keys(before), keys(after)
    return {
        "added": [list(edge) for edge in sorted(now - was)],
        "removed": [list(edge) for edge in sorted(was - now)],
    }


def candidate_diff(run_root: Path, candidate_id: str) -> dict[str, Any]:
    """A candidate's state against its parent's, as the run left them on disk."""
    run_root = Path(run_root)
    child = _candidate_state(run_root, candidate_id)
    if child is None:
        raise FileNotFoundError(f"no candidate {candidate_id!r} under {run_root}")
    parent_id = child.get("parent")
    if not parent_id:
        return {"candidate": candidate_id, "parent": None, "seed": True, "diff": {}}
    parent = _candidate_state(run_root, parent_id)
    if parent is None:
        return {"candidate": candidate_id, "parent": parent_id, "seed": False, "diff": {}}
    return {
        "candidate": candidate_id,
        "parent": parent_id,
        "seed": False,
        "diff": diff_states(parent.get("state") or {}, child.get("state") or {}),
    }


def _candidate_state(run_root: Path, candidate_id: str) -> dict[str, Any] | None:
    """A candidate's state, from the journal or from its own directory."""
    journal = run_root / JOURNAL
    if not journal.exists():
        return None
    for line in journal.read_text().splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("event") == "candidate" and record.get("id") == candidate_id:
            return {"state": record.get("state"), "parent": record.get("parent")}
    return None


def artifact_manifest(run_root: Path) -> dict[str, Any]:
    """The run's readable outputs, for a reader that knows nothing about the loop.

    A port names an artifact and its media type, which is what a GUI renders and
    what a downstream mode reads. Inferred from file writes it would be a guess.
    """
    run_root = Path(run_root)
    artifacts = [
        {"name": name, "path": filename, "media_type": media_type,
         "exists": (run_root / filename).exists()}
        for name, filename, media_type in _ARTIFACTS
    ]
    analyses = sorted((run_root / "analysis").glob("*.md")) if (run_root / "analysis").is_dir() else []
    artifacts.append({
        "name": "analysis",
        "path": "analysis/",
        "media_type": "text/markdown",
        "exists": bool(analyses),
        "files": [f"analysis/{path.name}" for path in analyses],
    })
    candidates = sorted((run_root / "runs").glob("*/candidate.json")) if (run_root / "runs").is_dir() else []
    artifacts.append({
        "name": "candidates",
        "path": "runs/",
        "media_type": "application/json",
        "exists": bool(candidates),
        "files": [f"runs/{path.parent.name}/candidate.json" for path in candidates],
    })
    return {"run": str(run_root), "artifacts": artifacts}


def progress(run_root: Path, *, after: int = 0) -> list[dict[str, Any]]:
    """Every step recorded since line ``after``, so a reader can tail the run."""
    path = Path(run_root) / PROGRESS
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    for index, line in enumerate(path.read_text().splitlines()):
        if index < after or not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def render_diff(diff: dict[str, Any]) -> str:
    """The diff as markdown, for the report and for a reader."""
    if not diff:
        return "_(the seed: there is nothing to compare it with)_"
    lines: list[str] = []
    knobs, nodes, edges = diff["knobs"], diff["nodes"], diff["edges"]
    if knobs:
        lines.append("Parameters:")
        for name, change in knobs.items():
            if "added" in change or "removed" in change:
                lines.append(f"- {name}: {'added' if 'added' in change else 'removed'}")
            else:
                lines.append(f"- {name}: {change['before']} → {change['after']}")
    if nodes["added"]:
        lines.append(f"Nodes added: {', '.join(nodes['added'])}")
    if nodes["removed"]:
        lines.append(f"Nodes removed: {', '.join(nodes['removed'])}")
    for change in nodes["changed"]:
        lines.append(f"- node {change['id']}.{change['field']} changed")
    if edges["added"]:
        lines.append(f"Edges added: {len(edges['added'])}")
    if edges["removed"]:
        lines.append(f"Edges removed: {len(edges['removed'])}")
    return "\n".join(lines) if lines else "_(no change)_"


def graph_summary(state: dict[str, Any]) -> str:
    """The graph's size, so a reader can size up a candidate without loading it."""
    graph_state = state.get("graph") or {}
    if not graph_state.get("start"):
        return "no graph"
    graph = Graph.from_state(graph_state)
    return f"{len(graph.nodes)} node(s), {len(graph.edges)} edge(s)"
