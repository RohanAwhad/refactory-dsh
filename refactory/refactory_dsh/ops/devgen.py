"""Deterministic candidate generator for the fast outer-loop development mode.

The LLM ``generate`` node in ``circle_packing_loop`` costs 10-35 minutes per
inner run because it makes real model calls. That is too slow to develop or
exercise the outer loop against. This op is the drop-in replacement: it writes
``candidate.py`` from the resolved knobs and nothing else, so an inner run is
deterministic, free and takes seconds.

The generated program packs circles by decreasing radius over a square grid of
``knob:grid`` resolution, keeping the best-separated position for each circle.
The score therefore rises with the grid resolution, which gives the outer loop a
real signal to optimize rather than a constant.

Two knobs are consumed:

- ``grid`` (int)  - grid resolution per axis; higher packs better and is slower.
- ``order`` (str)  - ``desc`` places large circles first, ``asc`` the reverse.

Knob transport matches every other SRF op: the resolved values arrive as the
``SRF_KNOBS`` JSON object, set by the science runner from ``--knobs``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

CANDIDATE = Path("candidate.py")

#: Knobs this op reads from SRF_KNOBS (see factory.workflow.opt_knobs).
KNOBS = {
    "grid": {
        "kind": "threshold",
        "default": 10,
        "bounds": [4, 8, 12, 16, 24],
        "expandable": True,
        "expansion_hint": "a finer packing grid places more circles but scans more positions",
        "description": "packing grid resolution per axis",
    },
    "order": {
        "kind": "prompt",
        "default": "desc",
        "bounds": ["desc", "asc"],
        "expandable": False,
        "description": "whether the largest or smallest circles are placed first",
    },
}

_TEMPLATE = '''"""Generated circle-packing candidate (deterministic)."""
import math


def solve(n: int, radii: list[float]) -> list[tuple[float, float]]:
    grid = {grid}
    reverse = {reverse}

    order = sorted(range(n), key=lambda i: radii[i], reverse=reverse)
    centers: list[tuple[float, float]] = [(0.0, 0.0)] * n
    placed: list[tuple[float, float, float]] = []

    for idx in order:
        r = radii[idx]
        best_pos = (r, r)
        best_gap = float("-inf")

        for gx in range(grid):
            for gy in range(grid):
                x = r + (1.0 - 2.0 * r) * gx / max(grid - 1, 1)
                y = r + (1.0 - 2.0 * r) * gy / max(grid - 1, 1)

                gap = float("inf")
                for px, py, pr in placed:
                    dist = math.sqrt((x - px) ** 2 + (y - py) ** 2)
                    current = dist - r - pr
                    if current < gap:
                        gap = current
                    if gap < -1e-9:
                        break

                if gap > best_gap:
                    best_gap = gap
                    best_pos = (x, y)

        centers[idx] = best_pos
        placed.append((best_pos[0], best_pos[1], r))

    return centers
'''


def _knobs() -> dict[str, object]:
    return json.loads(os.environ.get("SRF_KNOBS", "{}") or "{}")


def generate() -> None:
    """Write ``candidate.py`` from the resolved ``grid`` and ``order`` knobs."""
    knobs = _knobs()
    grid = max(2, int(knobs.get("grid", 10)))  # type: ignore[arg-type]
    order = str(knobs.get("order", "desc"))
    CANDIDATE.write_text(
        _TEMPLATE.format(grid=grid, reverse=order != "asc"),
    )
    print(json.dumps({"grid": grid, "order": order, "wrote": str(CANDIDATE)}))


_COMMANDS = {"generate": generate}


def main() -> None:
    import sys

    if len(sys.argv) < 2 or sys.argv[1] not in _COMMANDS:
        print(f"usage: python -m refactory_dsh.ops.devgen <{'|'.join(_COMMANDS)}>", file=sys.stderr)
        sys.exit(2)
    _COMMANDS[sys.argv[1]]()


if __name__ == "__main__":
    main()
