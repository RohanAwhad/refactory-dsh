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

    ``intent`` is what the run is *for*, and it is a class attribute because a
    revision is this same graph with a different brief: survey, choose, review,
    design, emit, validate. Subclassing to change the intent is cheaper and truer
    than a second copy of the graph.
    """

    name = "create"
    description = "design, emit and validate a whole package from a brief"
    unscored_reason = "it is judged by whether the package it produced imports, validates and smoke-scores"
    intent = "Make a new package of modes."

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
            # create-v2's two creation directives: a Task class the produced mode
            # must satisfy, and whether the package iterates data per item.
            "task_ref": Knob(
                "", "str",
                description="a Task class the produced mode must satisfy, as "
                            "`module:Class` or a registered name (empty for none)",
            ),
            "data_node": Knob(
                False, "bool",
                description="build the package as a DataNode that runs its subgraph "
                            "once per data item",
            ),
        }

    def build(self) -> Graph:
        graph = Graph()
        graph.add(Node(
            "prepare", "op", call=f"{OPS}:prepare",
            # `task_ref` and `data_node` are read here and published as facts.
            knobs=("task_ref", "data_node"),
            writes=("brief.json",),
            produces=("name", "brief", "extends", "revision_of", "intent", "validation",
                      "design_review", "api", "reference", "task_ref", "data_node"),
            notes="read the brief and publish it to the prompts",
        ))
        graph.add(Node(
            "survey", "op", call=f"{OPS}:survey",
            writes=("survey.json",),
            produces=("survey", "existing"),
            notes="enumerate what already exists, before anything is invented",
        ))

        # ── create-v2: the research director ───────────────────────────────
        # v2 replaced v1's fixed three-researcher fork with a director that chooses
        # the directions itself. Ours spawns one subagent per direction, which is
        # this runtime's way of fanning out, and the plan it leaves behind is what
        # the strategy director reads — the fan-out is not a side effect.
        graph.add(Node(
            "research_director", "agent",
            prompt_template=(
                "{{intent}}\n\n"
                "Package name: {{name}}\nBrief: {{brief}}\n\n"
                "## What already exists\n{{survey}}\n\n"
                "You are the research director. Decide what has to be learned before "
                "this package can be designed: between three and five questions, each "
                "one answerable from this checkout, from the reference package, or from "
                "the API below. Spawn one subagent per question and have each write its "
                "findings to `research/<slug>.md` under the run directory. Then write "
                "`research-plan.json` as an array of `{\"focus\", \"slug\", "
                "\"finding\"}` objects. A direction whose finding is empty did not "
                "answer its question: say so rather than writing something plausible.\n\n"
                "## API\n{{api}}\n\n## Reference package\n{{reference}}\n"
            ),
            reads=("brief.json", "survey.json"),
            writes=("research-plan.json",),
            produces=("research",),
            notes="choose the research directions and have a subagent answer each one",
        ))
        graph.add(Node(
            "strategy_director", "agent",
            prompt_template=(
                "{{intent}}\n\n"
                "Package name: {{name}}\nBrief: {{brief}}\n\n"
                "## What already exists\n{{survey}}\n\n"
                "## What the research found\n{{research}}\n\n"
                "You are the strategy director. From the research, choose between two "
                "and five strategy perspectives on how this package could work — "
                "including one that argues for the simplest thing that satisfies the "
                "brief, and one that says what the acceptance criteria must be. Spawn "
                "one subagent per perspective and have each write `strategy/<slug>.md` "
                "under the run directory. Then write `strategy-plan.json` as an array of "
                "`{\"perspective\", \"slug\", \"intent_items\"}` objects, where "
                "`intent_items` lists the asks from the brief that perspective covers. "
                "Every ask must be covered by at least one perspective; name the "
                "uncovered ones rather than leaving them out.\n\n"
                "## Reference package\n{{reference}}\n"
            ),
            reads=("brief.json", "survey.json", "research-plan.json"),
            writes=("strategy-plan.json",),
            produces=("strategy_plan",),
            notes="choose the strategy perspectives and their intent coverage",
        ))
        graph.add(Node(
            "synthesize_strategy", "agent",
            prompt_template=(
                "{{intent}}\n\n"
                "Package name: {{name}}\nBrief: {{brief}}\n\n"
                "## What already exists\n{{survey}}\n\n"
                "## What the research found\n{{research}}\n\n"
                "## The perspectives\n{{strategy_plan}}\n\n"
                "## What went wrong last time\n{{design_review}}\n\n"
                "Compile the perspectives into one strategy. Every ask in the brief must "
                "appear in it or be named as deliberately out of scope: an ask that is "
                "silently dropped is the failure this step exists to catch. Then decide "
                "the approach: consider at least two, say why the one you chose wins, "
                "say what it will not do, name the existing mode it is closest to, and "
                "describe the graph you intend to write.\n\n" + STRATEGY_SCHEMA
            ),
            reads=("brief.json", "survey.json", "research-plan.json",
                   "strategy-plan.json", "strategy-review.txt"),
            writes=("strategy.json",),
            produces=("strategy",),
            notes="compile the perspectives into one intent-faithful strategy",
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
        # create-v2 puts a person here (`gate_strategy`), and it is the one v2 gate
        # this port keeps as-is: the strategy is a decision the person owns, and a
        # run that can approve its own strategy is not reviewed at all. A headless
        # run refuses it, exactly as it refuses any `user` gate.
        graph.add(Node(
            "gate_strategy", "gate", gate_kind="user",
            reads=("strategy.json",),
            max_iterations=4,
            notes=(
                "Approve the strategy before any code is written? The chosen approach "
                "is in strategy.json in the run directory, and the package is written "
                "from it. Answer proceed to build it, or reloop to have the strategy "
                "director choose another approach."
            ),
        ))
        graph.add(Node(
            "design", "agent",
            prompt_template=(
                "{{intent}}\n\n"
                "Package name: {{name}}\nBrief: {{brief}}\n\n"
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
                "## A Task this mode must satisfy\n{{task_ref}}\n\n"
                "## Data iteration\n{{data_node}}\n\n"
                "Answer with file blocks, each introduced by a line of exactly:\n"
                "===FILE: <relative path>===\n"
                "followed by the file's contents. Emit `__init__.py` (with "
                "`PACKAGE = nn.Package(...)`), the ops module, and any module the "
                "ops or the mode need. Use the declared ops module path in every "
                "`call` and `evaluator`. When a Task is named, the mode must accept "
                "that Task's per-item prompt as its input and produce what its "
                "`verify()` scores. When data iteration is asked for, the graph's "
                "start node is a `data` node whose `subgraph_entry` and `subgraph_exit` "
                "delimit the per-item subgraph. No prose outside the blocks.\n"
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
        # create-v2's `gate_build`: the built thing is checked before it is reviewed.
        graph.add(Node(
            "validate", "gate", evaluator=f"{OPS}:validate",
            reads=("emit.txt",),
            writes=("validation.txt",),
            produces=("validation", "attempts_used"),
            knobs=("attempts",),
            max_iterations=12,
            notes="import the package and check it is a usable mode",
        ))

        # ── create-v2: the three reviews, in parallel ──────────────────────
        # v2 fans out to a health check, a code review and an adversarial QA
        # director, and waits for all three before synthesising. The fork reaches
        # its branches through `targets` and the join waits on `sources`, so one
        # review finishing early cannot let the run past the barrier.
        graph.add(Node("fork_qa", "fork",
                       targets=("health_checker", "code_reviewer", "qa_director"),
                       notes="start the three reviews"))
        graph.add(Node(
            "health_checker", "agent",
            prompt_template=(
                "Brief: {{brief}}\n\n## The design\n{{design}}\n\n"
                "You are the health checker. Run the created package the way a user "
                "would: import it, list its modes, and run its smoke check. Record every "
                "command and its exact output in `reviews/health-check.md`. If anything "
                "fails or is missing, write the line `BLOCKING:` followed by what failed. "
                "A check you did not run is not a check that passed — say which ones you "
                "skipped."
            ),
            reads=("brief.json", "design.md"),
            writes=("reviews/health-check.md",),
            produces=("health_check",),
            notes="run the package and record what happened, command by command",
        ))
        graph.add(Node(
            "code_reviewer", "agent",
            prompt_template=(
                "## The design\n{{design}}\n\n## The emitted source\n{{emit}}\n\n"
                "You are the code reviewer. Read the emitted source against the design "
                "and the API: are every op, node, knob and fact declared and consumed, "
                "does `score()` read a fact the graph produces, and would the package "
                "import? Record findings in `reviews/code-review.md`. A defect that "
                "would stop the package working is written as `BLOCKING:` followed by "
                "the file and what is wrong. Style is not blocking."
            ),
            reads=("design.md", "emit.txt"),
            writes=("reviews/code-review.md",),
            produces=("code_review",),
            notes="review the emitted source against the design",
        ))
        graph.add(Node(
            "qa_director", "agent",
            prompt_template=(
                "Brief: {{brief}}\n\n## The strategy (the acceptance criteria are in "
                "it)\n{{strategy}}\n\n"
                "You are the QA director. Design adversarial checks against this "
                "package — the cases a friendly reviewer would not try — and run them. "
                "Write `reviews/qa.md` with each check and its exact result. Every "
                "acceptance criterion in the strategy must have a check. Anything that "
                "breaks an acceptance criterion is written as `BLOCKING:` followed by "
                "what you ran and what happened."
            ),
            reads=("brief.json", "strategy.json"),
            writes=("reviews/qa.md",),
            produces=("qa_review",),
            notes="attack the package against its acceptance criteria",
        ))
        graph.add(Node("join_qa", "join",
                       sources=("health_checker", "code_reviewer", "qa_director"),
                       notes="wait for all three reviews"))
        graph.add(Node(
            "synthesize_qa", "op", call=f"{OPS}:synthesize_qa",
            reads=("reviews/health-check.md", "reviews/code-review.md", "reviews/qa.md"),
            writes=("reviews/qa-synthesized.md",),
            produces=("qa", "qa_blocking", "qa_missing"),
            notes="merge the three reviews; an absent review is not a passed one",
        ))
        graph.add(Node(
            "gate_qa", "gate", evaluator=f"{OPS}:gate_qa",
            reads=("reviews/qa-synthesized.md",),
            writes=("reviews/qa-gate.txt",),
            max_iterations=4,
            notes="proceed only when every review ran and none is blocking",
        ))

        # ── create-v2: the overwatch pass ──────────────────────────────────
        graph.add(Node(
            "overwatch", "agent",
            prompt_template=(
                "Brief: {{brief}}\n\n## The strategy\n{{strategy}}\n\n"
                "## The merged reviews\n{{qa}}\n\n"
                "You are the overwatch. This is not another review: audit the reviews "
                "themselves. Take each ask in the brief and each acceptance criterion, "
                "and say what evidence proves it — a command and its output — or that "
                "nothing does. Run your own spot checks on the things the reviews claim "
                "passed: import the package, run its smoke check, read back the mode's "
                "knobs. Write `reviews/overwatch.md` ending with a line that is exactly "
                "`VERDICT: PASS` or `VERDICT: FAIL`, and name every ask with no evidence "
                "behind it. A claim you could not reproduce is not evidence."
            ),
            reads=("brief.json", "strategy.json", "reviews/qa-synthesized.md", "design.md"),
            writes=("reviews/overwatch.md",),
            produces=("overwatch",),
            notes="audit the evidence behind the reviews, not the work again",
        ))
        graph.add(Node(
            "gate_overwatch", "gate", evaluator=f"{OPS}:gate_overwatch",
            reads=("reviews/overwatch.md",),
            writes=("reviews/overwatch-gate.txt",),
            max_iterations=4,
            notes="proceed only on a stated PASS verdict",
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
        graph.connect("survey", "research_director")
        graph.connect("research_director", "strategy_director")
        graph.connect("strategy_director", "synthesize_strategy")
        graph.connect("synthesize_strategy", "review")
        graph.connect("review", "synthesize_strategy", condition="reloop")
        graph.connect("review", "gate_strategy", condition="proceed")
        graph.connect("gate_strategy", "strategy_director", condition="reloop")
        graph.connect("gate_strategy", "design", condition="proceed")
        graph.connect("design", "emit")
        graph.connect("emit", "materialise")
        graph.connect("materialise", "validate")
        graph.connect("validate", "emit", condition="reloop")
        graph.connect("validate", "fork_qa", condition="proceed")
        graph.connect("fork_qa", "join_qa")
        graph.connect("join_qa", "synthesize_qa")
        graph.connect("synthesize_qa", "gate_qa")
        graph.connect("gate_qa", "emit", condition="reloop")
        graph.connect("gate_qa", "overwatch", condition="proceed")
        graph.connect("overwatch", "gate_overwatch")
        graph.connect("gate_overwatch", "emit", condition="reloop")
        graph.connect("gate_overwatch", "register", condition="proceed")
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
    #: A Task class the produced mode must satisfy (create-v2's `--task`).
    task_ref: str = ""
    #: Whether the produced graph is a DataNode iterating a subgraph per item.
    data_node: bool = False
    #: Verdicts for gates a person answers. `gate_strategy` is one, so a headless
    #: run has to be given the person's answer explicitly rather than inventing it.
    verdicts: dict[str, str] = field(default_factory=dict)


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
    state["knobs"]["task_ref"]["value"] = config.task_ref
    state["knobs"]["data_node"]["value"] = bool(config.data_node)
    mode.load_state_dict(state)

    workdir = Path(config.workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    run = mode.run(
        workdir,
        model=client,
        task={
            "name": config.name,
            "brief": config.brief,
            "extends": config.extends,
            "task_ref": config.task_ref,
            "data_node": config.data_node,
        },
        max_steps=config.max_steps,
        verdicts=dict(config.verdicts),
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
    # create-v2's two creation directives, reachable from the shell.
    parser.add_argument(
        "--data-node", action="store_true", default=False, dest="data_node",
        help="build the package as a DataNode that runs its subgraph once per item",
    )
    parser.add_argument(
        "--task", default=None, metavar="TASK_REF", dest="task_ref",
        help="a Task class (module:Class or a registered name) the produced mode must satisfy",
    )
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
                data_node=bool(args.data_node),
                task_ref=str(args.task_ref or ""),
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
