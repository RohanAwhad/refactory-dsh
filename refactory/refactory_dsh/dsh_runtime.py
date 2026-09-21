"""Evaluate a candidate on DSH instead of the Python walker.

The walker is a debug runtime: it makes the outer loop fast, and fast iteration is
what found every bug so far. DSH is the runtime that ships. Both score a mode the
same way, and the equivalence tests are what keep that true.

The boundary is deliberate and is the whole point:

- **DSH produces facts on disk.** The lowered graph's nodes run and leave a
  `facts.json` behind, because the IR's nodes do not have facts.
- **Python turns the facts into a number.** `score()` stays a method on the class,
  out of the graph, so no proposal can reach it.

That is why `fact_produced` is the runtime boundary contract and not only a safety
check: it says "when the run ends, these facts exist", which is exactly what is
needed to score a run that DSH executed.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from refactory_dsh import lowering
from refactory_dsh.nn import RunResult
from refactory_dsh.paths import REPO_ROOT, node_bin, venv_bin
from refactory_dsh.run import FACTS

#: What the CEO is asked to do. A deterministic mode needs the CEO only to advance
#: the cursor; the bundle mounts the spine with `executeCommands: true`, so the
#: nodes themselves run in-process.
TASK = "Call wf_next repeatedly until it reports done. Do nothing else."

#: Wall-clock ceiling for one inner run on DSH.
DEFAULT_TIMEOUT_S = 900


@dataclass
class DshRun:
    """What a DSH evaluation produced."""

    facts: dict[str, Any]
    workdir: Path
    returncode: int
    stdout: str = ""
    stderr: str = ""
    duration_s: float = 0.0
    best_score: float | None = None


def run_on_dsh(
    state: dict[str, Any],
    workdir: Path,
    *,
    name: str = "mode",
    timeout_s: int = DEFAULT_TIMEOUT_S,
    repo_root: Path | None = None,
) -> DshRun:
    """Lower ``state``, run it on DSH, and read back the facts it produced."""
    import time

    from refactory_dsh import dsh_profile

    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    problem = dsh_profile.requirement()
    if problem is not None:
        # Checked here rather than left to the child: DSH's own error is about a
        # missing profile, which reads as a packaging bug. The profile is machine
        # state, and this is the one place that knows the command that creates it.
        return DshRun(facts={}, workdir=workdir, returncode=2, stderr=problem)

    graphs = workdir / "graphs"
    # The steering layer, on the runtime that ships. A run a person steered has a
    # ledger in its work directory; folding it in here is what makes the CEO's agent
    # nodes carry what was said, and what a resumed run replays.
    from refactory_dsh import steering

    lowering.write_mode_graph(
        state,
        graphs,
        name=name,
        transform=lambda workflow: steering.for_run(workflow, workdir),
    )
    # The IR cannot carry the facts a node produces, so they travel in the run
    # directory and the runner reads them from there.
    lowering.write_declarations(state, workdir)

    command = [
        str(node_bin()), "--import", "tsx/esm", "apps/cli/src/bin.ts",
        "--profile", "graph", "run",
        "--mode", name,
        "--task", TASK,
        "--budget", "1",
        "--graphs", str(graphs),
        "--workdir", str(workdir),
        "--python-bin", str(venv_bin()),
        "--knobs", json.dumps(lowering.knob_environment(state)[lowering.KNOBS_ENV]),
    ]
    started = time.time()
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            cwd=str(repo_root or REPO_ROOT),
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired as exc:
        return DshRun(
            facts={},
            workdir=workdir,
            returncode=-1,
            stderr=f"dsh timed out after {timeout_s}s",
            duration_s=time.time() - started,
        )

    payload = _last_json_object(result.stdout)
    facts_path = workdir / FACTS
    facts: dict[str, Any] = {}
    if facts_path.exists():
        try:
            loaded = json.loads(facts_path.read_text())
            facts = loaded if isinstance(loaded, dict) else {}
        except json.JSONDecodeError:
            facts = {}
    return DshRun(
        facts=facts,
        workdir=workdir,
        returncode=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
        duration_s=round(time.time() - started, 4),
        best_score=(
            float(payload["best_score"])
            if isinstance(payload.get("best_score"), (int, float))
            else None
        ),
    )


def _last_json_object(text: str) -> dict[str, Any]:
    """The runner prints one pretty-printed object; take the last one it printed."""
    for start in range(len(text) - 1, -1, -1):
        if text[start] != "{":
            continue
        try:
            loaded = json.loads(text[start:])
        except json.JSONDecodeError:
            continue
        if isinstance(loaded, dict):
            return loaded
    return {}


def evaluate_on_dsh(
    mode: Any, state: dict[str, Any], workdir: Path, *, timeout_s: int = DEFAULT_TIMEOUT_S
) -> RunResult:
    """One forward pass on DSH, scored by the mode's own `score()`."""
    name = f"{type(mode).__module__.split('.')[-2]}-{mode.name}"
    run = run_on_dsh(state, workdir, name=name, timeout_s=timeout_s)
    if run.returncode != 0 or not run.facts:
        detail = run.stderr.strip().splitlines()[-1] if run.stderr.strip() else "no output"
        return RunResult(
            facts=run.facts,
            duration_s=run.duration_s,
            exit_ok=False,
            error=f"dsh run failed (exit {run.returncode}): {detail[:300]}",
            workdir=run.workdir,
        )
    result = RunResult(facts=run.facts, duration_s=run.duration_s, workdir=run.workdir)
    try:
        result.score = float(mode.score(result))
    except Exception as exc:
        result.exit_ok = False
        result.error = f"score() raised {type(exc).__name__}: {exc}"
    return result
