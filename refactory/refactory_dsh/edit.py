"""Revise an existing mode from a brief.

The gap: `create` starts from nothing and `loop` searches empirically, so there was
no way to say "change *this* mode to do *that*" in words. The loop cannot express an
intent it cannot measure; create cannot start from an existing mode without
re-inventing it.

A revision is the same graph create-mode walks, with a different intent and the
mode's own source as the reference. What it adds is the check that matters: the
result is smoked and compared against the mode it came from, and a change that
breaks the score is refused by default. A plausible-looking edit that quietly makes
a mode worse is the failure this exists to prevent.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from refactory_dsh import registry
from refactory_dsh.paths import project_for_nodes
from refactory_dsh.create import (
    CreateConfig, CreateError, CreateMode, parse_verdicts,
)
from refactory_dsh.model import DEFAULT_TIMEOUT_S
from refactory_dsh.sandbox import DEFAULT_MODEL_SMOKE_TIMEOUT_S


class EditMode(CreateMode):
    """Revise an existing mode rather than invent a new one."""

    name = "edit"
    description = "revise an existing mode from a brief, and check it did not regress"
    intent = (
        "Revise an existing mode. The reference below is its current source: keep "
        "everything the brief does not ask to change, and change only what it asks "
        "for. Prefer the smallest edit that achieves the brief over a rewrite."
    )


@dataclass
class EditConfig(CreateConfig):
    """A revision: what to change, and what to do about a regression."""

    mode: str = ""
    #: Skip original execution/comparison; still validate the revised mode.
    allow_regression: bool = False


def _digest(state: dict[str, Any]) -> str:
    """A short, stable name for one state, so two revisions can be compared.

    The same digest the candidate identity uses, so a revision and a candidate
    that are the same state agree about it.
    """
    from refactory_dsh.optim.base import candidate_id

    return candidate_id(state)


def edit(config: EditConfig, client: Any = None) -> dict[str, Any]:
    """Revise a mode and report the before and after scores.

    The graph owns validation, smoke and comparison on every entry point. This
    driver retains its authoritative report instead of measuring it again keyless.
    """
    if not config.mode:
        raise CreateError(
            "a revision needs the mode to revise, e.g. --mode circle_packing/adaptive"
        )
    project = project_for_nodes()
    # Resolved once, here: the state being revised is read from the mode as it is
    # now, and it is recorded below so the revision can be reproduced against the
    # exact state it started from rather than against "whatever that mode was".
    _package, source_class = registry.resolve(project, config.mode)
    source_state = source_class().state_dict()
    if not 1 <= config.smoke_timeout_s <= 86400:
        raise CreateError("smoke_timeout_s must be between 1 and 86400 seconds")

    from refactory_dsh.fast import EditFastMode

    mode_class = EditFastMode if config.fast else EditMode
    run_workdir = Path(config.workdir)
    run_workdir.mkdir(parents=True, exist_ok=True)

    mode = mode_class()
    state = mode.state_dict()
    if config.fast:
        state["knobs"]["repair_attempts"]["value"] = int(config.repair_attempts)
        state["knobs"]["check"]["value"] = config.check
        state["knobs"]["check_timeout_s"]["value"] = int(config.check_timeout_s)
    else:
        state["knobs"]["attempts"]["value"] = int(config.attempts)
    state["knobs"]["smoke_timeout_s"]["value"] = int(config.smoke_timeout_s)
    state["knobs"]["task_ref"]["value"] = config.task_ref
    state["knobs"]["data_node"]["value"] = bool(config.data_node)
    state["knobs"]["sample_knobs"]["value"] = json.dumps(list(config.samples))
    # The graph's smoke step is what compares before and after, so the person's
    # answer to "may it regress" has to travel as the knob that step reads.
    state["knobs"]["allow_regression"]["value"] = bool(config.allow_regression)
    mode.load_state_dict(state)
    mode_name = "edit_fast" if config.fast else "edit"
    from refactory_dsh.run_ownership import ownership

    with ownership(run_workdir):
        from refactory_dsh.run_preparation import prepare_run
        from refactory_dsh import steering
        from refactory_dsh.paths import project_root

        prepare_run(
            state, run_workdir,
            invocation_name=f"default_pack-{mode_name}",
            mode_id=f"default_pack/{mode_name}",
            intent=config.brief,
            transform=lambda workflow, stage_dir=None: steering.for_run(workflow, stage_dir or run_workdir),
            runtime="walker",
            command="python -m refactory_dsh.edit",
            resume_command=f"python -m refactory_dsh.edit --resume {run_workdir}",
            fast=config.fast,
            worker_backend=client.route().get("worker_backend", "") if client and callable(getattr(client, "route", None)) else "",
            project=project_root(),
        )

        # Persist up-front verdicts as run-scoped ledger entries.
        ledger = steering.Ledger(run_workdir)
        for gate, value in config.verdicts.items():
            ledger.verdict(gate, value, approval=True)

        run = mode.run(
            run_workdir,
            model=client,
            task={
                "name": config.name,
                "brief": config.brief,
                "extends": config.extends or config.mode,
                "mode": config.mode,
                # Persisted with the run, including across resume. The canonical smoke
                # refuses if the source state changed during authoring.
                "revision_of_state": source_state,
                "intent": EditMode.intent,
            },
            max_steps=config.max_steps,
            resource_limits=config.resource_limits,
        )
    if run.gate_stop:
        return {"exit_ok": False, "error": run.error, "gate_stop": run.gate_stop}
    if run.halted:
        return {"exit_ok": False, "error": run.error, "halted": True}
    if not run.exit_ok:
        raise CreateError(f"revising {config.mode!r} failed: {run.error}")

    # The report node's payload also contains the Task/artifact contract and the
    # reason a model-based revision was not compared. Keep those fields intact.
    created = json.loads(run.facts["created"])
    name = str(created["package"])
    return {
        **created,
        "mode": registry.load(name, project).ids()[0],
        "revision_of_digest": _digest(source_state),
        "workdir": str(run_workdir),
    }


def main(argv: list[str] | None = None) -> int:
    """``python -m refactory_dsh.edit --mode <pkg>/<mode> --brief <text>``."""
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        prog="python -m refactory_dsh.edit",
        description="Revise an existing mode from a brief.",
    )
    parser.add_argument("--mode", required=False, default="", help="the mode to revise")
    parser.add_argument("--name", required=False, default="", help="the package to write the revision into")
    parser.add_argument("--brief", required=False, default="")
    parser.add_argument("--attempts", type=int, default=3)
    parser.add_argument("--fast", action="store_true", help="use the inspectable edit_fast graph with focused QA")
    parser.add_argument("--repair-attempts", type=int, default=1, help="fast graph repair limit (0..3)")
    parser.add_argument("--check", default="", help="fast graph's explicit focused check command")
    from refactory_dsh.budgets import add_arguments, arguments

    add_arguments(parser)
    parser.add_argument("--allow-regression", action="store_true",
                        help="skip original-mode execution and score comparison; still validate the revision")
    # Per package name, for the same reason a creation is: one fixed directory
    # means runs overwrite each other's state.
    parser.add_argument("--workdir", default="")
    parser.add_argument(
        "--sample", action="append", default=[], metavar="NAME=VALUE", dest="samples",
        help="a knob value the validation run should use (repeatable), e.g. the input "
             "the brief is about, so the smoke exercises that path",
    )
    parser.add_argument(
        "--resume", default="", metavar="RUN_DIR",
        help="continue a failed run from its own directory instead of starting a "
             "new one; every node that already finished is kept",
    )
    parser.add_argument("--model", default="")
    parser.add_argument("--provider", default="", help="provider route that model is on")
    parser.add_argument(
        "--model-timeout", type=int, default=DEFAULT_TIMEOUT_S, dest="model_timeout_s",
        help="seconds one model completion may take before it is called hung",
    )
    parser.add_argument(
        "--smoke-timeout", type=int, default=DEFAULT_MODEL_SMOKE_TIMEOUT_S, dest="smoke_timeout_s",
        help="seconds for each full mode validation pass (default: 3600)",
    )
    parser.add_argument(
        "--verdict", action="append", default=[], metavar="GATE=VALUE",
        help="answer a gate a person owns, as <gate>=<value> (repeatable)",
    )
    args = parser.parse_args(argv)
    if not args.resume:
        if not args.mode:
            parser.error("--mode is required when not resuming")
        if not args.name:
            parser.error("--name is required when not resuming")

    from refactory_dsh.model import DshModelClient
    from refactory_dsh.paths import project_root

    if args.resume:
        workdir = Path(args.resume)
        fast = args.fast
        config_path = workdir / '.refactory/run-config.json'
        if config_path.exists():
            try:
                run_config = json.loads(config_path.read_text())
                fast = run_config.get('fast', fast)
            except (json.JSONDecodeError, OSError):
                pass
        client = DshModelClient(
            project=None,
            timeout_s=int(args.model_timeout_s), model=args.model, provider=args.provider,
        )
        from refactory_dsh.run_ownership import ownership

        try:
            with ownership(workdir):
                from refactory_dsh.fast import EditFastMode

                run = (EditFastMode if fast else EditMode).resume(
                    workdir, model=client, resource_limits=arguments(args),
                )
        except Exception as exc:
            print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}), file=sys.stderr)
            return 2
        if run.gate_stop:
            from refactory_dsh.run_preparation import print_gate_stop
            print_gate_stop(run.gate_stop, workdir, graph=(EditFastMode if fast else EditMode)().build())
            return 2
        if run.halted:
            print(json.dumps({"halted": True, "error": run.error}), file=sys.stderr)
            return 2
        print(json.dumps({
            "resumed": str(workdir), "exit_ok": run.exit_ok, "error": run.error,
            "facts": {key: value for key, value in run.facts.items() if key in
                      ("name", "files", "package_path", "smoke_score", "before_score", "after_score")},
        }, indent=2, default=str))
        return 0 if run.exit_ok else 2

    try:
        result = edit(
            EditConfig(
                name=args.name,
                brief=args.brief,
                mode=args.mode,
                attempts=args.attempts,
                allow_regression=args.allow_regression,
                workdir=Path(args.workdir or f".factory/edit/{args.name}"),
                samples=list(args.samples),
                model_timeout_s=int(args.model_timeout_s),
                smoke_timeout_s=int(args.smoke_timeout_s),
                verdicts=parse_verdicts(args.verdict),
                fast=args.fast, repair_attempts=args.repair_attempts, check=args.check,
                resource_limits=arguments(args),
            ),
            client=DshModelClient(
                project=None if args.resume else project_root(),
                timeout_s=int(args.model_timeout_s), model=args.model, provider=args.provider,
            ),
        )
    except CreateError as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 2
    if result.get("gate_stop"):
        from refactory_dsh.run_preparation import print_gate_stop
        from refactory_dsh.fast import EditFastMode
        print_gate_stop(result["gate_stop"], Path(args.workdir or f".factory/edit/{args.name}"),
                        graph=(EditFastMode if args.fast else EditMode)().build())
        return 2
    if result.get("halted"):
        print(json.dumps({"halted": True, "error": result.get("error")}), file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":  # pragma: no cover - the entry point
    raise SystemExit(main())
