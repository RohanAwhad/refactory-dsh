"""Shared run preparation for both the walker and DSH runtimes.

One function both runtimes call: take ownership, invoke ``invocations.prepare``
(which archives the old invocation, seeds the ledger into the stage, folds it
into the lowering, and installs the result), check the install and profile, and
write ``run-config.json``.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable


def prepare_run(
    state: dict[str, Any],
    workdir: Path,
    *,
    invocation_name: str,
    mode_id: str,
    intent: str,
    transform: Callable,
    runtime: str,
    command: str,
    resume_command: str,
    fast: bool = False,
    worker_backend: str = "dsh",
    resource_limits: dict[str, Any] | None = None,
    project: Path | None = None,
) -> Path:
    """Prepare a run directory for either runtime.

    Order:
    1. Install and profile check (before anything is archived).
    2. ``invocations.prepare`` — archives the old invocation, seeds the ledger
       into the stage, folds it from there, and installs the result.
    3. Write ``run-config.json``.

    Returns the installed graphs directory.
    """
    # 1. Install and profile check — before invocations.prepare moves anything,
    #    so a failure leaves nothing archived without rollback.
    #    DSH has its own checks at interactive.py run() (~253-255); they run on
    #    both launch and resume, so moving them here would drop the resume check.
    #    The walker needs its own check here.
    if runtime == "walker":
        from refactory_dsh.paths import require_build

        require_build()
        if worker_backend == "dsh":
            from refactory_dsh.dsh_profile import model_home, profile_dir

            headless_dir = profile_dir("headless", home=model_home())
            if not (headless_dir / "package.json").exists():
                raise ValueError(
                    f"the DSH headless profile does not exist at {headless_dir}; "
                    "create it with `dsh profile create headless` and add the "
                    "graph bundle"
                )

    # 2. Archive and stage.
    from refactory_dsh import invocations

    graphs = invocations.prepare(
        state, workdir, name=invocation_name, transform=transform,
        resource_limits=resource_limits, intent=intent,
    )

    # 3. Write run-config.json.
    from refactory_dsh.recovery import configuration_fingerprints

    config: dict[str, Any] = {
        "mode": mode_id,
        "runtime": runtime,
        "command": command,
        "resume_command": resume_command,
        "fast": fast,
        "project": str(Path(project).resolve()) if project else None,
        "knobs": [],
        "model_context": {},
        "resource_limits": resource_limits,
        "configuration_fingerprints": configuration_fingerprints(
            workdir, mode_id, Path(project) if project else Path(".")
        ) if project else {},
    }
    config_path = workdir / ".refactory/run-config.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(config, indent=2, default=str))

    return graphs


def print_gate_stop(gate_id: str, workdir: Path, *, graph: Any = None) -> None:
    """Print the gate-stop output to stderr: prompt, verdicts, steer and resume.

    The ``{"error": ...}`` JSON line is first so ``console._forward`` keeps
    working.
    """
    import sys

    print(json.dumps({"error": f"gate {gate_id!r} is waiting for a person"}),
          file=sys.stderr)
    # Read the gate's notes (prompt) and edges from the graph.
    prompt = ""
    verdict_labels: list[str] = []
    if graph is not None:
        node = graph.nodes.get(gate_id)
        if node is not None:
            from refactory_dsh.nn.executor import render, RunContext

            ctx = RunContext(workdir)
            # Load facts for rendering.
            facts_path = workdir / "facts.json"
            if facts_path.exists():
                try:
                    ctx.facts = json.loads(facts_path.read_text())
                except (json.JSONDecodeError, OSError):
                    pass
            prompt = render(node.notes, ctx)
            verdict_labels = sorted(
                {e.condition for e in graph.out_edges(gate_id) if e.condition}
            )
    if "halt" not in [v.lower() for v in verdict_labels]:
        verdict_labels.append("halt")
    verdicts_str = ", ".join(verdict_labels)
    print(f"refactory: {gate_id} is waiting for a person.", file=sys.stderr)
    if prompt:
        for line in prompt.splitlines():
            print(f"  {line}", file=sys.stderr)
    print(f"  Accepted verdicts: {verdicts_str}", file=sys.stderr)
    print(f"  Answer it (add --note \"...\" to say why):", file=sys.stderr)
    print(f"    refactory steer --run {workdir} --verdict {gate_id}=<verdict> [--note ...]", file=sys.stderr)
    # Read resume command from run-config.json.
    resume_cmd = ""
    config_path = workdir / ".refactory/run-config.json"
    if config_path.exists():
        try:
            cfg = json.loads(config_path.read_text())
            resume_cmd = cfg.get("resume_command", "")
        except (json.JSONDecodeError, OSError):
            pass
    if resume_cmd:
        print(f"  Then continue the run:", file=sys.stderr)
        print(f"    {resume_cmd}", file=sys.stderr)
