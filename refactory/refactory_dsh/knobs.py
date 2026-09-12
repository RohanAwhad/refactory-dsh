"""A mode's optimizable surface as a contract, not a per-generation guess.

The surface is derived (see ``factory.workflow.opt_knobs``), which is what makes
it honest. But deriving it fresh every generation has a failure mode: the graph
mutates, so the surface can *shrink* mid-search. A node removed by one candidate
takes its knobs with it, the optimizer's action space quietly narrows, and
nothing records that it happened.

So the surface is pinned once, when the mode is created, and every later
derivation is checked against that pin:

- **at creation** — derive, report anything declared-but-dead, and persist. This
  is the only moment the author can fix a knob that nothing reads.
- **at outer-loop start** — load the pin. A first run against a mode created
  before this existed derives and pins it then.
- **per generation** — re-derive only to check. A knob that disappeared is a
  finding, never a silent shrink.

An empty surface is refused rather than tolerated: a mode with nothing
optimizable cannot be optimized, and running a search over nothing is exactly the
failure that looked like success for months when the reflection stage was
silently disabled.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import structlog

from factory.workflow.opt_knobs import mode_parameters
from factory.workflow.primitives import Workflow

log = structlog.get_logger()

#: Persisted beside the mode, so the pin travels with the artifact it describes.
SUFFIX = ".knobs.json"


def _surface_path(mode_path: Path) -> Path:
    return Path(mode_path).with_suffix(SUFFIX)


def derive(workflow: Workflow) -> dict[str, Any]:
    """Derive the mode's optimizable surface and everything wrong with it."""
    params = mode_parameters(workflow)
    return {
        "knobs": [
            {
                "name": k.name, "kind": k.kind, "node_id": k.node_id,
                "default": k.default, "bounds": list(k.bounds),
                "expandable": k.expandable,
                "expansion_hint": k.expansion_hint,
                "description": k.description,
            }
            for k in params.knobs
        ],
        "dead": params.dead,
        "unverified": params.unverified,
        "problems": params.report(),
    }


def _read_surface(mode_path: Path) -> dict[str, Any]:
    """Derive the surface without touching the pin on disk."""
    workflow = Workflow.from_dict(json.loads(Path(mode_path).read_text()))
    return derive(workflow)


def _write_pin(mode_path: Path, surface: dict[str, Any]) -> None:
    _surface_path(mode_path).write_text(json.dumps(surface, indent=2, default=str))


def pin(mode_path: Path) -> dict[str, Any]:
    """Derive the surface and persist it beside the mode. Returns the pin."""
    mode_path = Path(mode_path)
    surface = _read_surface(mode_path)
    _write_pin(mode_path, surface)
    if surface["dead"]:
        # Loud, because this is the moment it is cheap to fix: a knob the mode
        # offers and the runtime ignores will move a search and change nothing.
        log.warning(
            "mode_declares_dead_knobs",
            mode=mode_path.name,
            dead=[d["name"] for d in surface["dead"]],
            action="remove the knob or wire its consumer",
        )
    if not surface["knobs"]:
        log.warning(
            "mode_has_no_optimizable_surface",
            mode=mode_path.name,
            action="the outer loop cannot optimize a mode with no knobs",
        )
    return surface


def load_pin(mode_path: Path) -> dict[str, Any] | None:
    """Read the pinned surface, or None when the mode has never been pinned."""
    path = _surface_path(mode_path)
    if not path.exists():
        return None
    try:
        loaded = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    return loaded if isinstance(loaded, dict) else None


def check(mode_path: Path) -> tuple[dict[str, Any], list[str]]:
    """Pin if needed, then compare the live surface against the pin.

    Returns the current surface and the findings. Findings are reported, never
    corrected: a knob that vanished because a node was removed is a real change
    to the mode, and silently accepting it would hide that the search space
    shrank under the optimizer.
    """
    mode_path = Path(mode_path)
    # Read the pin BEFORE deriving, and only write one when the mode has never
    # been pinned. Re-pinning here would compare the surface against itself and
    # report no findings however far the mode had drifted.
    pinned = load_pin(mode_path)
    current = _read_surface(mode_path)
    if pinned is None:
        _write_pin(mode_path, current)
        log.info("mode_surface_pinned", mode=mode_path.name, knobs=len(current["knobs"]))

    findings: list[str] = []
    if not current["knobs"]:
        findings.append(
            f"mode '{mode_path.name}' exposes no optimizable knobs; "
            "the outer loop has nothing to search"
        )
    # Dead knobs are reported every time: the optimizer would move them and
    # nothing would change. Unverified fields are not, because they are a
    # standing property of the codebase rather than drift in this mode -- the
    # pin records them once, at creation, where they can be resolved.
    for entry in current["dead"]:
        findings.append(
            f"'{entry['name']}' on node '{entry['node_id']}' is declared but not "
            f"consumed ({entry['description']})"
        )

    if pinned is not None:
        pinned_names = {k["name"] for k in pinned.get("knobs", [])}
        current_names = {k["name"] for k in current["knobs"]}
        for name in sorted(pinned_names - current_names):
            findings.append(
                f"knob '{name}' was available when the mode was created and is gone now; "
                "the optimizable surface has shrunk"
            )
        for name in sorted(current_names - pinned_names):
            findings.append(f"knob '{name}' is new since the mode was created")

    for finding in findings:
        log.warning("mode_surface_finding", mode=mode_path.name, finding=finding)
    return current, findings


def require_surface(mode_path: Path) -> dict[str, Any]:
    """Check the surface and fail loud when there is nothing to optimize.

    Raises ``SystemExit`` rather than returning an empty surface, because an
    outer loop over an empty action space runs, reports generations, and looks
    productive while doing nothing. That is the failure mode this whole module
    exists to prevent.
    """
    current, findings = check(mode_path)
    if not current["knobs"]:
        raise SystemExit(
            f"refusing to run: mode '{Path(mode_path).name}' exposes no optimizable "
            f"knobs. Findings: {'; '.join(findings) or 'none'}"
        )
    return current
