"""create-mode: a mode whose output is a whole package.

Creating a mode is the same shape as optimizing one, one level up — design,
emit, validate, and regenerate with the reasons when validation refuses. The
identical validate/reject/regenerate loop is what makes the outer loop safe, so
it is the same loop here, run by the same executor over the same kind of graph.

    prepare -> design -> emit -> materialise -> validate
                   ^                                |
                   |                reloop -> emit  |
                   |                proceed -> register -> smoke -> report
                   +--------------------------------+

The gate is deterministic. A model's package is imported, its graph is built,
every call it names is resolved, and its score is checked to be real before the
package is registered at all.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from refactory_dsh import registry
from refactory_dsh.create_ops import STRATEGY_SCHEMA
from refactory_dsh.nn import Graph, Knob, Mode, Node, RunResult

OPS = "refactory_dsh.create_ops"


class CreateMode(Mode):
    """Emit a package of modes from a brief.

    Unscored on purpose: its run is judged by whether the package it produced
    imports, validates and smoke-scores, which the graph itself checks.
    """

    name = "create-mode"
    description = "design, emit and validate a whole package from a brief"

    @classmethod
    def declare(cls) -> dict[str, Knob]:
        return {
            "attempts": Knob(
                3, "int", lo=1, hi=8,
                description="how many times the emit step may be retried with the "
                            "validator's reasons",
            ),
            "design_attempts": Knob(
                2, "int", lo=1, hi=5,
                description="how many times the strategy step may be retried when the "
                            "intended graph does not hold together",
            ),
        }

    def build(self) -> Graph:
        graph = Graph()
        graph.add(Node(
            "prepare", "op", call=f"{OPS}:prepare",
            writes=("brief.json",),
            produces=("name", "brief", "extends", "validation", "api", "reference"),
            notes="read the brief and publish it to the prompts",
        ))
        graph.add(Node(
            "survey", "op", call=f"{OPS}:survey",
            produces=("survey", "existing"),
            notes="enumerate what already exists, before anything is invented",
        ))
        graph.add(Node(
            "strategy", "agent",
            prompt_template=(
                "You are choosing an approach for a package of modes.\n\n"
                "Package name: {{name}}\n"
                "Brief: {{brief}}\n\n"
                "## What already exists\n{{survey}}\n\n"
                "## Reference package\n{{reference}}\n\n"
                "## What went wrong last time\n{{design_review}}\n\n"
                "Decide the approach before any code is written: consider at least two, "
                "say why the one you chose wins, say what it will not do, name the "
                "existing mode it is closest to, and describe the graph you intend to "
                "write.\n\n" + STRATEGY_SCHEMA
            ),
            reads=("brief.json", "survey.json", "strategy-review.txt"),
            writes=("strategy.json",),
            produces=("strategy",),
            notes="decide the approach and declare the intended graph",
        ))
        graph.add(Node(
            "review", "gate", evaluator=f"{OPS}:review_design",
            reads=("strategy.json",),
            writes=("strategy-review.txt",),
            produces=("design_review", "design_attempts_used"),
            knobs=("design_attempts",),
            max_iterations=8,
            notes="check the intended graph before any code exists",
        ))
        graph.add(Node(
            "design", "agent",
            prompt_template=(
                "You are designing a package of modes for a workflow harness.\n\n"
                "Package name: {{name}}\n"
                "Brief: {{brief}}\n\n"
                "Read the API below and write the design for the package: every "
                "operation, every node, every knob, and what score() reads. Be concrete "
                "and complete — the next step writes the code from your design alone.\n\n"
                "## The approach that was chosen\n{{strategy}}\n\n"
                "## API\n{{api}}\n\n"
                "## Reference package\n{{reference}}\n"
            ),
            reads=("brief.json", "strategy.json"),
            writes=("design.md",),
            produces=("design",),
            notes="plan the package before any code is written",
        ))
        graph.add(Node(
            "emit", "agent",
            prompt_template=(
                "Write the package `{{name}}` as complete, runnable Python.\n\n"
                "Brief: {{brief}}\n\n"
                "Design:\n{{design}}\n\n"
                "## API\n{{api}}\n\n"
                "## Reference package\n{{reference}}\n\n"
                "## What went wrong last time\n{{validation}}\n\n"
                "Answer with file blocks, each introduced by a line of exactly:\n"
                "===FILE: <relative path>===\n"
                "followed by the file's contents. Emit `__init__.py` (with "
                "`PACKAGE = nn.Package(...)`), the ops module, and any module the "
                "ops or the mode need. Use the declared ops module path in every "
                "`call` and `evaluator`. No prose outside the blocks.\n"
            ),
            reads=("brief.json", "design.md", "validation.txt"),
            writes=("emit.txt",),
            produces=("emit",),
            notes="write the package's source files",
        ))
        graph.add(Node(
            "materialise", "op", call=f"{OPS}:materialise",
            reads=("emit.txt",),
            writes=("staging/manifest.json",),
            produces=("package_path", "files"),
            notes="write the staged package to disk",
        ))
        graph.add(Node(
            "validate", "gate", evaluator=f"{OPS}:validate",
            reads=("emit.txt",),
            writes=("validation.txt",),
            produces=("validation", "attempts_used"),
            knobs=("attempts",),
            max_iterations=12,
            notes="import the package and check it is a usable mode",
        ))
        graph.add(Node(
            "register", "op", call=f"{OPS}:register",
            writes=("registered.json",),
            produces=("registered",),
            notes="confirm the registry lists the new package",
        ))
        graph.add(Node(
            "smoke", "op", call=f"{OPS}:smoke",
            writes=("smoke.json",),
            produces=("smoke_score", "smoke_ok"),
            notes="run the new mode once; a mode that cannot score is not finished",
        ))
        graph.add(Node(
            "report", "op", call=f"{OPS}:write_result",
            writes=("create.json",),
            produces=("created",),
            notes="summarise what was created",
        ))

        graph.connect("prepare", "survey")
        graph.connect("survey", "strategy")
        graph.connect("strategy", "review")
        graph.connect("review", "strategy", condition="reloop")
        graph.connect("review", "design", condition="proceed")
        graph.connect("design", "emit")
        graph.connect("emit", "materialise")
        graph.connect("materialise", "validate")
        graph.connect("validate", "emit", condition="reloop")
        graph.connect("validate", "register", condition="proceed")
        graph.connect("register", "smoke")
        graph.connect("smoke", "report")
        return graph


@dataclass
class CreateConfig:
    """What to create, and how hard to try."""

    name: str
    brief: str
    extends: str = ""
    attempts: int = 3
    workdir: Path = Path(".factory/create")
    max_steps: int = 200
    seed: int = 0


@dataclass
class CreateResult:
    """What the run produced."""

    package: str
    mode: str
    path: Path
    files: list[str] = field(default_factory=list)
    smoke_score: float | None = None
    iterations: int = 0
    duration_s: float = 0.0

    def describe(self) -> dict[str, Any]:
        return {
            "package": self.package,
            "mode": self.mode,
            "path": str(self.path),
            "files": self.files,
            "smoke_score": self.smoke_score,
            "iterations": self.iterations,
            "duration_s": self.duration_s,
        }


def create(config: CreateConfig, client: Any = None) -> CreateResult:
    """Run create-mode once. Returns what was created, or raises why it was not."""
    mode = CreateMode()
    state = mode.state_dict()
    state["knobs"]["attempts"]["value"] = int(config.attempts)
    mode.load_state_dict(state)

    workdir = Path(config.workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    run = mode.run(
        workdir,
        model=client,
        task={"name": config.name, "brief": config.brief, "extends": config.extends},
        max_steps=config.max_steps,
    )
    if not run.exit_ok:
        raise CreateError(f"creating {config.name!r} failed: {run.error}")

    name = str(run.facts.get("name") or config.name)
    package = registry.load(name, Path.cwd())
    (workdir / "create.json").write_text(
        json.dumps(
            {"package": name, "smoke_score": run.facts.get("smoke_score"),
             "files": run.facts.get("files", [])},
            indent=2,
            default=str,
        )
    )
    return CreateResult(
        package=name,
        mode=package.ids()[0],
        path=registry.where(name, Path.cwd()) or registry.workspace_root(Path.cwd()) / name,
        files=list(run.facts.get("files") or []),
        smoke_score=run.facts.get("smoke_score"),
        iterations=run.iterations,
        duration_s=run.duration_s,
    )


class CreateError(RuntimeError):
    """create-mode could not produce a working package."""


def result_from_run(run: RunResult) -> dict[str, Any]:
    """The facts of a create-mode run, for a caller that drove the executor itself."""
    return dict(run.facts)


def main(argv: list[str] | None = None) -> int:
    """``python -m refactory_dsh.create --name <n> --brief <text> [--extends <mode>]``."""
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        prog="python -m refactory_dsh.create",
        description="Create a package of modes from a brief.",
    )
    parser.add_argument("--name", required=True, help="the package name")
    parser.add_argument("--brief", required=True, help="what the package should do")
    parser.add_argument("--extends", default="", help="a <package>/<mode> to use as a reference")
    parser.add_argument("--attempts", type=int, default=3)
    parser.add_argument("--workdir", default=".factory/create")
    parser.add_argument("--model", default="")
    args = parser.parse_args(argv)

    from refactory_dsh.model import DshModelClient

    try:
        result = create(
            CreateConfig(
                name=args.name,
                brief=args.brief,
                extends=args.extends,
                attempts=args.attempts,
                workdir=Path(args.workdir),
            ),
            client=DshModelClient(model=args.model),
        )
    except CreateError as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 2
    print(json.dumps(result.describe(), indent=2, default=str))
    return 0


if __name__ == "__main__":  # pragma: no cover - the entry point
    raise SystemExit(main())
