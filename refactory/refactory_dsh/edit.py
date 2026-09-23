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
from refactory_dsh.paths import project_root, project_for_nodes
from refactory_dsh.create import (
    CreateConfig, CreateError, CreateMode, CreateResult, create, parse_verdicts,
)
from refactory_dsh.model import DEFAULT_TIMEOUT_S


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
    #: Refuse a revision whose smoke score is worse than the mode it came from.
    allow_regression: bool = False


def _smoke(identifier: str, project: Path) -> dict[str, Any]:
    """Score a mode, so before and after are comparable."""
    from refactory_dsh import sandbox

    result = sandbox.smoke(identifier, project / ".factory" / "edit-smoke", project=project)
    if not result.ok:
        return {"exit_ok": False, "error": result.problem(), "score": None}
    return {
        "exit_ok": True,
        "error": None,
        "score": float(result.payload.get("score") or 0.0),
    }


def _digest(state: dict[str, Any]) -> str:
    """A short, stable name for one state, so two revisions can be compared.

    The same digest the candidate identity uses, so a revision and a candidate
    that are the same state agree about it.
    """
    from refactory_dsh.optim.base import candidate_id

    return candidate_id(state)


def edit(config: EditConfig, client: Any = None) -> dict[str, Any]:
    """Revise a mode and report the before and after scores.

    The revision is produced by the same machinery as a creation, so it validates,
    registers and smoke-scores the same way. What is added here is the comparison,
    because a revision has something to be worse than.
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
    source_mode = source_class()
    before = _smoke(config.mode, project)
    if not before["exit_ok"]:
        raise CreateError(
            f"cannot revise {config.mode!r}: its own smoke run failed: {before['error']}"
        )

    mode_class = EditMode
    run_workdir = Path(config.workdir)
    run_workdir.mkdir(parents=True, exist_ok=True)

    mode = mode_class()
    state = mode.state_dict()
    state["knobs"]["attempts"]["value"] = int(config.attempts)
    state["knobs"]["task_ref"]["value"] = config.task_ref
    state["knobs"]["data_node"]["value"] = bool(config.data_node)
    # The graph's smoke step is what compares before and after, so the person's
    # answer to "may it regress" has to travel as the knob that step reads.
    state["knobs"]["allow_regression"]["value"] = bool(config.allow_regression)
    mode.load_state_dict(state)
    run = mode.run(
        run_workdir,
        model=client,
        task={
            "name": config.name,
            "brief": config.brief,
            "extends": config.extends or config.mode,
            "mode": config.mode,
            "intent": EditMode.intent,
        },
        max_steps=config.max_steps,
        # The revision is the create-v2 graph, so it carries the same person's gate
        # on the strategy; a headless run has to be given their answer.
        verdicts=dict(config.verdicts),
    )
    if not run.exit_ok:
        raise CreateError(f"revising {config.mode!r} failed: {run.error}")

    name = str(run.facts.get("name") or config.name)
    after = _smoke(f"{name}/{registry.load(name, project).modes[0].name}", project)
    if not after["exit_ok"]:
        raise CreateError(f"the revision does not run: {after['error']}")

    regressed = (
        before["score"] is not None
        and after["score"] is not None
        and after["score"] < before["score"]
    )
    if regressed and not config.allow_regression:
        raise CreateError(
            f"the revision scores {after['score']} where {config.mode} scored "
            f"{before['score']}; pass --allow-regression if that is intended"
        )

    # Provenance: what it came from, **at which state**, and what it scored.
    #
    # The state, not only the identifier. A mode is not a fixed thing — the outer
    # loop edits it — so "revised from circle_packing/adaptive" names a class that
    # may have changed since. Recording the state makes the revision reproducible,
    # lets the two be diffed, and is the only way to tell later whether the
    # revision was made against the current baseline or an older one.
    source_state = source_mode.state_dict()
    origin = registry.workspace_root(project) / name / registry.ORIGIN
    recorded = json.loads(origin.read_text()) if origin.exists() else {}
    recorded.update({
        "revision_of": config.mode,
        "revision_of_state": source_state,
        "revision_of_digest": _digest(source_state),
        "before_score": before["score"],
        "after_score": after["score"],
        "brief": config.brief,
    })
    origin.write_text(json.dumps(recorded, indent=2))

    return {
        "package": name,
        "mode": f"{name}/{registry.load(name, project).modes[0].name}",
        "revision_of": config.mode,
        "revision_of_digest": _digest(source_state),
        "before_score": before["score"],
        "after_score": after["score"],
        "regressed": regressed,
        "files": list(run.facts.get("files") or []),
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
    parser.add_argument("--mode", required=True, help="the mode to revise")
    parser.add_argument("--name", required=True, help="the package to write the revision into")
    parser.add_argument("--brief", required=True)
    parser.add_argument("--attempts", type=int, default=3)
    parser.add_argument("--allow-regression", action="store_true")
    parser.add_argument("--workdir", default=".factory/edit")
    parser.add_argument("--model", default="")
    parser.add_argument(
        "--model-timeout", type=int, default=DEFAULT_TIMEOUT_S, dest="model_timeout_s",
        help="seconds one model completion may take before it is called hung",
    )
    parser.add_argument(
        "--verdict", action="append", default=[], metavar="GATE=VALUE",
        help="answer a gate a person owns, as <gate>=<value> (repeatable)",
    )
    args = parser.parse_args(argv)

    from refactory_dsh.model import DshModelClient

    try:
        result = edit(
            EditConfig(
                name=args.name,
                brief=args.brief,
                mode=args.mode,
                attempts=args.attempts,
                allow_regression=args.allow_regression,
                workdir=Path(args.workdir),
                model_timeout_s=int(args.model_timeout_s),
                verdicts=parse_verdicts(args.verdict),
            ),
            client=DshModelClient(timeout_s=int(args.model_timeout_s), model=args.model),
        )
    except CreateError as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":  # pragma: no cover - the entry point
    raise SystemExit(main())
