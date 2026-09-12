"""Search steering: the knobs the *user* turns, as distinct from the mode's own.

An optimizer's settings are the outer loop's hyperparameters — which optimizer is
active, how much of each operator to use, how greedy selection is, and what the
search must not touch. They live in ``.factory/outer_loop/steering.json`` so they
can be changed between generations without editing code or restarting anything,
and so the report can show what is currently in force.

Everything is optional. A missing file, a missing key, or an unreadable file
leaves that setting at its default: steering must never be able to stop a run.
"""

from __future__ import annotations

import json
from pathlib import Path

OUTER = Path(".factory") / "outer_loop"
STEERING = OUTER / "steering.json"

#: Operator weights the search starts from, matching the shipped defaults.
DEFAULT_OPERATOR_WEIGHTS: dict[str, float] = {
    "node_insert": 0.15,
    "node_remove": 0.10,
    "edge_redirect": 0.15,
    "parallelize": 0.10,
    "serialize": 0.05,
    "param_mutate": 0.10,
    "prompt_mutate": 0.10,
    "knob_mutate": 0.25,
}

DEFAULTS: dict[str, object] = {
    "optimizer": None,
    "operator_weights": {},
    "frozen_knobs": [],
    "frozen_nodes": [],
    "tournament_size": None,
    "mutation_rate": None,
    "novelty_min_edit_distance": None,
}


def load(project_dir: Path) -> dict[str, object]:
    """Read the steering settings, filling in defaults for anything absent."""
    settings = dict(DEFAULTS)
    path = Path(project_dir) / STEERING
    if not path.exists():
        return settings
    try:
        raw = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return settings
    if not isinstance(raw, dict):
        return settings
    for key in DEFAULTS:
        if key in raw and raw[key] is not None:
            settings[key] = raw[key]
    return settings


def operator_weights(settings: dict[str, object]) -> dict[str, float]:
    """Merge user weights over the defaults.

    Merging rather than replacing means steering one operator does not silently
    re-weight every other one to zero.
    """
    weights = dict(DEFAULT_OPERATOR_WEIGHTS)
    raw = settings.get("operator_weights")
    if isinstance(raw, dict):
        for name, value in raw.items():
            if isinstance(value, (int, float)) and value >= 0:
                weights[str(name)] = float(value)
    return weights


def frozen_knobs(settings: dict[str, object]) -> set[str]:
    raw = settings.get("frozen_knobs")
    return {str(k) for k in raw} if isinstance(raw, list) else set()


def frozen_nodes(settings: dict[str, object]) -> set[str]:
    raw = settings.get("frozen_nodes")
    return {str(n) for n in raw} if isinstance(raw, list) else set()


def write_default(project_dir: Path) -> Path:
    """Write a steering file with the defaults, so it is discoverable."""
    path = Path(project_dir) / STEERING
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({**DEFAULTS, "operator_weights": DEFAULT_OPERATOR_WEIGHTS,
                                "_comment": "Edit to steer the search; applied from the next step."},
                               indent=2))
    return path
