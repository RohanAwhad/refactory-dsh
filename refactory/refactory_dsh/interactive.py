"""Run one mode with a person in the loop, on the runtime that ships.

Level 3. The outer loop *searches* a mode (level 2); this is a person **using** one.
The mode's graph is lowered with the run's intent ledger folded into every agent
node, the CEO drives it on DSH, and a `user` gate is put to the person at the
terminal by the runtime — not by the model, which cannot invent an answer it cannot
give.

    python -m refactory_dsh.interactive review_pack/review \
        --intent "draft a note about circle packing and let me approve it"

The answer is written into the run's ledger, so `console steer --run <workdir>`
shows it and a later run replays it instead of asking again. Nothing here changes
the mode: the graph is the author's, plus what the person said.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

from refactory_dsh import lowering, registry, steering
from refactory_dsh.dsh_runtime import TASK
from refactory_dsh.paths import REPO_ROOT, node_bin, venv_bin

#: Where an interactive run lives when no work directory is named.
DEFAULT_ROOT = Path(".factory/interactive")

#: Wall-clock ceiling is the person's, so there is none: the run waits for them.


def with_knobs(state: dict[str, Any], overrides: list[str]) -> dict[str, Any]:
    """One state with `NAME=VALUE` knob overrides applied.

    The CLI's spelling for something a person changes about *this* run: which
    package to create, what to check, how many attempts, and the inputs the mode
    operates on. Applied through `nn.edits.configure` rather than the editor's
    `set_knob`: a wrong name is still refused by name and an out-of-range value by
    range, but a *frozen* knob is settable — frozen means the search may not move it,
    not that the person running the mode may not say what it is. Refusing that made
    every input-taking mode unusable from the CLI (`--knob source_root=…` failed with
    "knob 'source_root' is frozen and cannot be moved").
    """
    from refactory_dsh import nn

    for pair in overrides:
        name, separator, value = str(pair).partition("=")
        name, value = name.strip(), value.strip()
        if not separator or not name:
            raise ValueError(f"--knob takes NAME=VALUE, not {pair!r}")
        state = nn.edits.configure(state, name, value)
    return state


def prepare(
    identifier: str,
    *,
    workdir: Path,
    intent: str = "",
    project: Path | None = None,
    knobs: list[str] | None = None,
) -> tuple[str, Path]:
    """Lower the mode for a person to use, and leave the run ready to start.

    Returns the graph name the profile loads and the directory it lives in. The
    ledger is seeded **before** the lowering, because the lowering is what folds it
    into the agent prompts; seeding afterwards would produce a run nobody steered.
    """
    project = Path(project or ".")
    if "/" not in identifier:
        raise ValueError(f"{identifier!r} is not a <package>/<mode> identifier")
    package, mode_name = identifier.split("/", 1)
    Mode = registry.resolve(project, identifier)[1]
    mode = Mode()
    state = with_knobs(mode.state_dict(), list(knobs or []))

    # Absolute, because this directory becomes DSH's session cwd and DSH refuses a
    # relative one: `interactive <mode>` with no `--workdir` used to fail before the
    # graph started, on the default root that is relative by design.
    workdir = Path(workdir).expanduser().resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    if intent.strip() and not steering.Ledger(workdir).exists():
        steering.Ledger.seed(workdir, intent)

    graphs = workdir / "graphs"
    name = f"{package}-{mode_name}"
    lowering.write_mode_graph(
        state,
        graphs,
        name=name,
        transform=lambda workflow: steering.for_run(workflow, workdir),
    )
    lowering.write_declarations(state, workdir)
    return name, graphs


def command(
    identifier: str,
    *,
    workdir: Path,
    graphs: Path,
    name: str,
    knobs: list[str] | None = None,
    project: Path | None = None,
    provider: str = "",
    model: str = "",
) -> list[str]:
    """The DSH run whose stdin is the person's terminal."""
    _, mode_name = identifier.split("/", 1)
    argv = [
        str(node_bin()), "--import", "tsx/esm", "apps/cli/src/bin.ts",
        "--profile", "graph", "run",
        "--mode", name,
        "--task", TASK,
        "--budget", "1",
        "--graphs", str(graphs),
        # DSH takes this as the session cwd and refuses a relative one.
        "--workdir", str(Path(workdir).expanduser().resolve()),
        "--python-bin", str(venv_bin()),
        # Already the JSON string `--knobs` wants; see `run_on_dsh`.
        "--knobs", lowering.knob_environment(
            with_knobs(
                registry.resolve(Path(project or "."), identifier)[1]().state_dict(),
                list(knobs or []),
            )
        )[lowering.KNOBS_ENV],
    ]
    # The graph profile takes these for the CEO agent it creates; the mode's own
    # agent nodes run through that same agent, so this is *the* model a run uses.
    if provider:
        argv += ["--provider", provider]
    if model:
        argv += ["--model", model]
    return argv


def run(
    identifier: str,
    *,
    workdir: Path | None = None,
    intent: str = "",
    project: Path | None = None,
    timeout_s: int | None = None,
    knobs: list[str] | None = None,
    approve: list[str] | None = None,
    provider: str = "",
    model: str = "",
) -> int:
    """Lower, then run the mode under the CEO with the terminal attached.

    ``approve`` states a gate's answer before the run starts. It is the same act as
    answering at the terminal, recorded in the same ledger, so a run nobody is
    sitting at can still be given a person's decision -- explicitly, by name.
    """
    if workdir is None:
        workdir = DEFAULT_ROOT / identifier.replace("/", "-")
    workdir = Path(workdir).expanduser().resolve()
    name, graphs = prepare(
        identifier, workdir=workdir, intent=intent, project=project, knobs=knobs,
    )
    for pair in list(approve or []):
        gate, separator, value = str(pair).partition("=")
        if not separator or not gate.strip() or not value.strip():
            raise ValueError(f"--approve takes GATE=VALUE, not {pair!r}")
        # After prepare(), so the intent seed and the verdicts are both in the ledger.
        steering.Ledger(workdir).verdict(gate.strip(), value.strip())
    argv = command(
        identifier, workdir=workdir, graphs=graphs, name=name, knobs=knobs, project=project,
        provider=provider, model=model,
    )
    # stdin is inherited on purpose: the graph profile's answerer reads the person's
    # answer from the terminal, and a captured stdin is not a person.
    code = subprocess.call(argv, cwd=str(REPO_ROOT), timeout=timeout_s)
    if code == 0:
        summary = run_summary(workdir)
        if summary:
            print(summary)
    return code


#: The facts worth showing a person who just ran a mode, in the order they read them.
SUMMARY_KEYS = (
    "name", "package_path", "files", "artifact", "check", "validation",
    "smoke_score", "smoke_reason",
    "revision_of", "before_score", "after_score", "regressed", "revision_note",
)


def run_summary(workdir: Path) -> str:
    """One line of what a finished run recorded, read from the run's own facts.

    DSH's own report is the agent's final word. The facts — where a package went, what
    it scored, whether a revision regressed — are in `facts.json`, and a person should
    not have to open it to find out what they just ran. Only keys that are present are
    shown, so a mode that records none of them prints nothing extra.
    """
    import json

    facts = Path(workdir) / "facts.json"
    if not facts.exists():
        return ""
    try:
        recorded = json.loads(facts.read_text())
    except (OSError, json.JSONDecodeError):
        return ""
    shown = {
        key: recorded[key] for key in SUMMARY_KEYS
        if recorded.get(key) not in (None, "", [])
    }
    if not shown:
        return ""
    return "refactory: what the run recorded — " + json.dumps(shown, default=str)


def main(argv: list[str] | None = None) -> int:
    """``python -m refactory_dsh.interactive <package>/<mode> --intent <text>``."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m refactory_dsh.interactive",
        description="Run one mode with a person in the loop, on DSH.",
    )
    parser.add_argument("mode", help="the mode to run, as <package>/<name>")
    parser.add_argument(
        "--intent", default="",
        help="what the run is for, in the person's words; seeds the ledger the agent "
             "nodes read and a resumed run is judged against",
    )
    parser.add_argument("--workdir", default="", help="the run directory")
    parser.add_argument(
        "--knob", action="append", default=[], metavar="NAME=VALUE",
        help="set one of the mode's knobs for this run (repeatable)",
    )
    parser.add_argument(
        "--approve", action="append", default=[], metavar="GATE=VALUE",
        help="state a gate's answer before the run, e.g. gate_strategy=proceed "
             "(repeatable); without it the run asks you at the terminal",
    )
    parser.add_argument("--timeout", type=int, default=0, help="seconds; 0 waits for the person")
    parser.add_argument(
        "--provider", default="",
        help="provider route for this run's model (default: the settings default)",
    )
    parser.add_argument(
        "--model", default="",
        help="model id for this run, e.g. deepseek-v4-pro (default: the settings default)",
    )
    args = parser.parse_args(argv)

    workdir = Path(args.workdir) if args.workdir else None
    try:
        code = run(
            args.mode,
            workdir=workdir,
            intent=args.intent,
            knobs=args.knob,
            approve=args.approve,
            timeout_s=args.timeout or None,
            provider=args.provider,
            model=args.model,
        )
    except Exception as exc:  # a failed run must be a message, not a traceback
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    return code


if __name__ == "__main__":  # pragma: no cover - the entry point
    raise SystemExit(main())
