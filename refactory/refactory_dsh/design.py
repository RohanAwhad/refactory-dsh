"""design-v2: a brief in, a checked software artifact out.

The third of the three meta-modes. create emits a package of modes; optimize searches
one; design emits software. The shape is the same as create's -- design, emit, check,
regenerate with the reasons -- and only the artifact and its gate differ, which is
what makes it a mode rather than a second program.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from refactory_dsh.create_ops import API
from refactory_dsh.nn import Graph, Knob, Mode, Node, RunResult

OPS = "refactory_dsh.design_ops"


class DesignMode(Mode):
    """Design, emit and check a software artifact.

    Unscored on purpose, like create-mode: its run is judged by whether the artifact
    passes the check the brief declared.
    """

    name = "design"
    description = "design, emit and check a software artifact from a brief"
    unscored_reason = "it is judged by whether the artifact passes the check its brief declared"

    @classmethod
    def declare(cls) -> dict[str, Knob]:
        return {
            "check_attempts": Knob(
                2, "int", lo=1, hi=5,
                description="how many times the emit step may be retried when the "
                            "artifact does not pass its check",
            ),
            "check_timeout_s": Knob(
                180, "int", lo=10, hi=1800,
                description="how long the brief's check may run",
            ),
        }

    def build(self) -> Graph:
        graph = Graph()
        graph.add(Node(
            "prepare", "op", call=f"{OPS}:prepare_design",
            writes=("brief.json",),
            produces=("name", "brief", "check", "validation"),
            notes="read the brief and the command that decides whether it is done",
        ))
        graph.add(Node(
            "design", "agent",
            prompt_template=(
                "Design a piece of software.\\n\\n"
                "Artifact name: {{name}}\\n"
                "Brief: {{brief}}\\n"
                "It will be checked by running, from the artifact's own directory:\\n"
                "    {{check}}\\n\\n"
                "Say what you will build, which files it needs, and how the check will "
                "pass. Be concrete: the next step writes the code from your design "
                "alone, and the check is the only thing that decides whether it worked.\\n\\n"
                "## Reference\\n{{api}}\\n"
            ),
            reads=("brief.json",),
            writes=("design.md",),
            produces=("design",),
            notes="plan the artifact before any code is written",
        ))
        graph.add(Node(
            "emit", "agent",
            prompt_template=(
                "Write the artifact `{{name}}` as complete, runnable files.\\n\\n"
                "Brief: {{brief}}\\n"
                "It will be checked by running: {{check}}\\n\\n"
                "Design:\\n{{design}}\\n\\n"
                "## What went wrong last time\\n{{validation}}\\n\\n"
                "Answer with file blocks, each introduced by a line of exactly:\\n"
                "===FILE: <relative path>===\\n"
                "followed by the file's contents. No prose outside the blocks.\\n"
            ),
            reads=("brief.json", "design.md", "check.txt"),
            writes=("emit.txt",),
            produces=("emit",),
            notes="write the artifact's files",
        ))
        graph.add(Node(
            "materialise", "op", call=f"{OPS}:materialise_artifact",
            reads=("emit.txt",),
            produces=("artifact", "files"),
            notes="write the artifact into its own directory",
        ))
        graph.add(Node(
            "check", "gate", evaluator=f"{OPS}:check_artifact",
            reads=("brief.json",),
            writes=("check.txt",),
            produces=("validation", "check_attempts_used"),
            knobs=("check_attempts", "check_timeout_s"),
            max_iterations=8,
            notes="run the brief's check, in a sandbox",
        ))
        graph.add(Node(
            "report", "op", call=f"{OPS}:report_artifact",
            writes=("design.json",),
            produces=("designed",),
            notes="say what was produced",
        ))

        graph.connect("prepare", "design")
        graph.connect("design", "emit")
        graph.connect("emit", "materialise")
        graph.connect("materialise", "check")
        graph.connect("check", "emit", condition="reloop")
        graph.connect("check", "report", condition="proceed")
        return graph

    def invariants(self) -> tuple[Any, ...]:
        return ()


def main(argv: list[str] | None = None) -> int:
    """``python -m refactory_dsh.design --name <n> --brief <text> --check <cmd>``."""
    import argparse
    import json
    import sys

    parser = argparse.ArgumentParser(
        prog="python -m refactory_dsh.design",
        description="Design, emit and check a software artifact from a brief.",
    )
    parser.add_argument("--name", required=True, help="the artifact's name")
    parser.add_argument("--brief", required=True, help="what to build")
    parser.add_argument("--check", required=True,
                        help="the command that decides whether it works")
    parser.add_argument("--attempts", type=int, default=2)
    parser.add_argument("--workdir", default=".factory/design")
    parser.add_argument("--model", default="")
    args = parser.parse_args(argv)

    from refactory_dsh.model import DshModelClient

    mode = DesignMode()
    state = mode.state_dict()
    state["knobs"]["check_attempts"]["value"] = int(args.attempts)
    mode.load_state_dict(state)
    result = mode.run(
        Path(args.workdir),
        model=DshModelClient(model=args.model),
        task={"name": args.name, "brief": args.brief, "check": args.check},
    )
    if not result.exit_ok:
        print(json.dumps({"error": result.error}), file=sys.stderr)
        return 2
    print(json.dumps({
        "artifact": str(result.facts.get("artifact")),
        "files": result.facts.get("files", []),
        "check": result.facts.get("check"),
        "iterations": result.iterations,
    }, indent=2, default=str))
    return 0


if __name__ == "__main__":  # pragma: no cover - the entry point
    raise SystemExit(main())
