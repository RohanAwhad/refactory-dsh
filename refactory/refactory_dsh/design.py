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

from refactory_dsh.create_ops import API, RESEARCH_SCHEMA
from refactory_dsh.model import DEFAULT_TIMEOUT_S
from refactory_dsh.nn import Graph, Knob, Mode, Node, RunResult

OPS = "refactory_dsh.design_ops"

_ACCEPTANCE_BASELINE = (
    "Acceptance baseline: the original brief, any approved design, and explicit user "
    "steering. Preserve user-approved non-goals. A README limitation, proposed "
    "exclusion, coordinator summary or reviewer note cannot waive a requested "
    "behavior without explicit user agreement to that change. Report conflicts "
    "instead of silently narrowing scope. Reviews must mark observed noncompliance "
    "as BLOCKING even when the limitation is documented.\n\n"
)


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
            "research_attempts": Knob(
                3, "int", lo=1, hi=8,
                description="how many times the research step may be retried when its "
                            "plan is not a plan or its findings were never written",
            ),
            "check_attempts": Knob(
                2, "int", lo=1, hi=5,
                description="how many times the emit step may be retried when the "
                            "artifact does not pass its check",
            ),
            "check_timeout_s": Knob(
                180, "int", lo=10, hi=1800,
                description="how long the brief's check may run",
            ),
            # As in create: the walker passes these in the run's task, and a run on
            # DSH carries them as knobs.
            "name": Knob("", "str", description="the artifact to design (empty takes the name from the brief)"),
            "brief": Knob("", "str", description="what to build, in the asker's words"),
            "check": Knob("", "str", description="the command that decides whether the artifact works"),
        }

    def build(self) -> Graph:
        graph = Graph()
        graph.add(Node(
            "prepare", "op", call=f"{OPS}:prepare_design",
            knobs=("name", "brief", "check"),
            writes=("brief.json", "research-review.txt", "check.txt"),
            produces=("name", "brief", "check", "validation", "research_review", "qa_gate",
                      "overwatch_review"),
            notes="read the brief and the command that decides whether it is done",
        ))

        # ── design-v2's head: research, perspectives, then the design itself ──
        graph.add(Node(
            "research_director", "agent",
            prompt_template=(
                "Artifact name: {{name}}\nBrief: {{brief}}\n"
                "It will be checked by running, from the artifact's own directory:\n"
                "    {{check}}\n\n"
                "You are the research director. Decide what has to be learned before "
                "this artifact can be designed: between three and five questions, each "
                "answerable by reading this checkout or by running something here. Spawn "
                "one subagent per question and have each write its findings to "
                "`research/<slug>.md` under the run directory, then write "
                "`research-plan.json` yourself.\n\n"
                "## What was wrong with the last plan\n{{research_review}}\n\n"
                "## The plan's shape\n" + RESEARCH_SCHEMA
            ),
            reads=("brief.json", "research-review.txt"),
            writes=("research-plan.json",),
            produces=("research",),
            notes="choose the research directions and have a subagent answer each one",
        ))
        # The same pair the create graph carries, for the same reason: the plan's
        # `writes` contract is satisfied by any bytes, so the stage that grounds the
        # design could narrate its intent and pass.
        graph.add(Node(
            "record_findings", "op", call="refactory_dsh.create_ops:record_findings",
            reads=("research-plan.json",),
            writes=("research/",),
            produces=("findings_recorded",),
            notes="write each direction's finding where the reviews will look for it",
        ))
        graph.add(Node(
            "gate_research", "gate", evaluator="refactory_dsh.create_ops:gate_research",
            reads=("research-plan.json",),
            writes=("research-review.txt",),
            produces=("research_review", "research_attempts_used"),
            knobs=("research_attempts",),
            max_iterations=8,
            notes="proceed only when the plan is a plan and its findings exist",
        ))
        graph.add(Node(
            "strategy_director", "agent",
            prompt_template=(
                "Artifact name: {{name}}\nBrief: {{brief}}\n\n"
                "## What the research found\n{{research}}\n\n"
                "You are the strategy director. From the research, choose between two "
                "and five perspectives on how this artifact could be built — including "
                "the simplest thing that could pass the check, and one that says what "
                "the check will not catch. Spawn one subagent per perspective and have "
                "it write `strategy/<slug>.md` under the run directory. Then write "
                "`strategy-plan.json` as an array of `{\"perspective\", \"slug\", "
                "\"intent_items\"}` objects, where `intent_items` lists the asks from "
                "the brief that perspective covers. Every ask must be covered."
            ),
            reads=("brief.json", "research-plan.json"),
            writes=("strategy-plan.json",),
            produces=("strategy_plan",),
            notes="choose the perspectives and record what each one covers",
        ))
        graph.add(Node(
            "synthesize_strategy", "agent",
            prompt_template=(
                "Artifact name: {{name}}\nBrief: {{brief}}\n"
                "The check, run from the artifact's own directory:\n    {{check}}\n\n"
                "## What the research found\n{{research}}\n\n"
                "## The perspectives\n{{strategy_plan}}\n\n"
                + _ACCEPTANCE_BASELINE
                + "Compile the perspectives into one plan: what will be built, which files "
                "it needs, how the check will pass, and what is deliberately out of "
                "scope with the user's agreement. Every remaining ask in the brief must "
                "appear in the plan; an unmet ask remains unresolved. "
                "For each ask distinguish what the supplied check establishes from "
                "what still needs independent evidence. Plan executable semantic checks "
                "for the gaps; a successful command or an existing output file does not "
                "establish that the output means what the user requested. For exchange "
                "formats, plan acceptance by an established consuming application or "
                "independent format reader, not just a parser the artifact author writes."
            ),
            reads=("brief.json", "research-plan.json", "strategy-plan.json"),
            writes=("strategy.json",),
            produces=("strategy",),
            notes="compile the perspectives into one plan",
        ))
        # design-v2's `design_doc`: the design is a document of its own, written
        # before the code and approved before anything is built.
        graph.add(Node(
            "design_doc", "agent",
            prompt_template=(
                "Artifact name: {{name}}\nBrief: {{brief}}\n"
                "The check, run from the artifact's own directory:\n    {{check}}\n\n"
                "## The plan\n{{strategy}}\n\n"
                + _ACCEPTANCE_BASELINE
                + "Write the design document for this artifact: every file it will "
                "contain, what each one does, the interfaces between them, and how the "
                "check exercises them. Be concrete — the next step writes the code from "
                "this document and the brief. Specify accepted input syntax and unambiguous "
                "rejection behavior: unsupported counts, signs, units, or other values "
                "must not silently become different valid requests. For visual output, "
                "plan a rendered inspection as well as format/geometry checks, including "
                "labels, margins, clipping, and agreement with the requested dimensions."
            ),
            reads=("brief.json", "strategy.json"),
            writes=("design.md",),
            produces=("design",),
            notes="write the design document the code is written from",
        ))
        # design-v2 puts the person's approval on the design document, after the plan
        # exists and before any code does.
        graph.add(Node(
            "gate_strategy", "gate", gate_kind="user",
            reads=("design.md",),
            max_iterations=4,
            notes=(
                "Approve the design before the artifact is written? It is in design.md "
                "in the run directory. Answer proceed to build it, or reloop to have the "
                "plan redone."
            ),
        ))
        graph.add(Node(
            "emit", "agent",
            prompt_template=(
                "Write the artifact `{{name}}` as complete, runnable files.\n\n"
                "Brief: {{brief}}\n"
                "It will be checked by running: {{check}}\n\n"
                "## The design\nRead the approved design from `design.md` in the run directory.\n\n"
                "## What went wrong last time\n{{validation}}\n\n"
                "## What the reviews refused\n{{qa_gate}}\n{{overwatch_review}}\n\n"
                + _ACCEPTANCE_BASELINE
                + "Write every file into emit.txt as a block introduced by a line of exactly:\n"
                "===FILE: <relative path>===\n"
                "followed by the file's full contents, with no prose outside the blocks. "
                "Do not create the files themselves: the materialise step writes them from "
                "emit.txt. Then return a short completion message.\n"
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
            "check", "gate", evaluator=f"{OPS}:check_artifact", role="validation",
            resource_role="final_validation",
            reads=("brief.json",),
            writes=("check.txt", "validation.txt", "authoring-evidence.json"),
            produces=("validation", "check_attempts_used", "attempts_used", "runtime_evidence"),
            knobs=("check_attempts", "check_timeout_s"),
            max_iterations=8,
            notes="run the brief's check, in a sandbox",
        ))

        # ── design-v2's three reviews, in parallel, and the overwatch pass ──
        graph.add(Node("fork_qa", "fork",
                       targets=("health_checker", "code_reviewer", "qa_director"),
                       notes="start the three reviews"))
        graph.add(Node(
            "health_checker", "agent",
            prompt_template=(
                "Artifact directory (absolute): {{artifact}}\n"
                "Read and run the delivered files at this exact path; do not infer it "
                "from your working directory.\n\n"
                "Brief: {{brief}}\nThe check it must pass: {{check}}\n\n"
                "## The design\nRead the approved design from `design.md` in the run directory.\n\n"
                + _ACCEPTANCE_BASELINE
                + "You are the health checker. Run the artifact the way a user would: run "
                "targeted checks for uncovered requirements. Reuse the canonical acceptance "
                "receipt for the check already executed. Record every command and "
                "its exact output in `reviews/health-check.md`. For visual deliverables, "
                "render actual representative output and inspect it at a useful size; "
                "record the renderer and image path. Text existing in SVG/XML does not "
                "prove it is visible or readable. If rendering or visual inspection is "
                "unavailable, say so and leave the visual requirement unverified. "
                "For exchange-format files, use an established consuming application "
                "or independent format reader to open representative actual output and "
                "inspect imported units, structure, geometry and required semantics. "
                "Record normal loading, audit findings and recovery/repair separately. "
                "Your own tag parser, or rendering a paired format, does not establish "
                "interoperability. If a required consumer check cannot run, name that "
                "unverified requirement rather than reporting a pass. Preserve the "
                "current artifact and evidence, name the missing validation dependency, "
                "and give the CEO or person the exact action needed to unblock it. "
                "Rewriting the artifact or repeating the same probe cannot supply an "
                "unavailable tool; do not prescribe those as a remedy. "
                "Anything that fails or is "
                "missing is written as the line `BLOCKING:` followed by what failed. A "
                "check you did not run is not a check that passed."
            ),
            reads=("brief.json", "design.md"),
            writes=("reviews/health-check.md",),
            produces=("health_check",),
            notes="run the artifact and record what happened, command by command",
        ))
        graph.add(Node(
            "code_reviewer", "agent",
            prompt_template=(
                "Artifact directory (absolute): {{artifact}}\n"
                "Read and run the delivered files at this exact path; do not infer it "
                "from your working directory.\n\n"
                "Brief: {{brief}}\n\n"
                "## The design\nRead the approved design from `design.md` in the run directory.\n\n"
                "## The emitted source\nRead the emitted source from `emit.txt` in the run directory.\n\n"
                + _ACCEPTANCE_BASELINE
                + "You are the code reviewer. Read the emitted files against the design "
                "and the brief: is every file the design names present, does it do what "
                "the design says, and would it run? Trace input values through parsing "
                "to the delivered artifact: count/role binding, signs, units, and "
                "unsupported input must preserve the request or explicitly reject it. "
                "A matching number elsewhere in prose is not evidence for the requested "
                "field. Record findings in "
                "`reviews/code-review.md`. A defect that would stop the artifact working "
                "is written as `BLOCKING:` followed by the file and what is wrong."
            ),
            reads=("brief.json", "design.md", "emit.txt"),
            writes=("reviews/code-review.md",),
            produces=("code_review",),
            notes="review the emitted files against the design",
        ))
        graph.add(Node(
            "qa_director", "agent",
            prompt_template=(
                "Artifact directory (absolute): {{artifact}}\n"
                "Read and run the delivered files at this exact path; do not infer it "
                "from your working directory.\n\n"
                "Brief: {{brief}}\nThe check it must pass: {{check}}\n\n"
                "## The approved design\nRead the approved design from `design.md` in the run directory.\n\n"
                "## The plan\nRead the synthesis strategy from `strategy.json` in the run directory.\n\n"
                + _ACCEPTANCE_BASELINE
                + "You are the QA director. Design adversarial checks on the artifact — "
                "the cases a friendly reviewer would not try — and run them. Vary one "
                "semantic property at a time and inspect the actual output, not just "
                "the exit code. Include conflicting nearby numbers, unsupported counts, "
                "and alternative sign/unit encodings when the interface parses them. "
                "Accepted output must match the request; unsupported input must fail "
                "clearly without silently substituting a valid case. For visual output, "
                "exercise small and large dimensions and inspect rendered labels and "
                "bounds; distinguish geometry correctness from presentation quality. Write "
                "`reviews/qa.md` with each check and its exact result. Anything that "
                "breaks an ask in the brief is written as `BLOCKING:` followed by what "
                "you ran and what happened."
            ),
            reads=("brief.json", "design.md", "strategy.json"),
            writes=("reviews/qa.md",),
            produces=("qa_review",),
            notes="attack the artifact against the brief",
        ))
        graph.add(Node("join_qa", "join",
                       sources=("health_checker", "code_reviewer", "qa_director"),
                       notes="wait for all three reviews"))
        graph.add(Node(
            "synthesize_qa", "op", call="refactory_dsh.create_ops:synthesize_qa",
            reads=("reviews/health-check.md", "reviews/code-review.md", "reviews/qa.md"),
            writes=("reviews/qa-synthesized.md",),
            produces=("qa", "qa_blocking", "qa_missing"),
            notes="merge the three reviews; an absent review is not a passed one",
        ))
        graph.add(Node(
            "gate_qa", "gate", evaluator="refactory_dsh.create_ops:gate_qa",
            reads=("reviews/qa-synthesized.md", "reviews/health-check.md",
                   "reviews/code-review.md", "reviews/qa.md"),
            writes=("reviews/qa-gate.txt",),
            produces=("qa_gate",),
            max_iterations=4,
            notes="proceed only when every review ran and none is blocking",
        ))
        graph.add(Node(
            "overwatch", "agent",
            prompt_template=(
                "Artifact directory (absolute): {{artifact}}\n"
                "Read and run the delivered files at this exact path; do not infer it "
                "from your working directory.\n\n"
                "Brief: {{brief}}\nThe check it must pass: {{check}}\n\n"
                "## The design\nRead the approved design from `design.md` in the run directory.\n\n"
                "## The merged reviews\nRead the file `reviews/qa-synthesized.md` in the run directory. It contains the merged reviews from the health checker, code reviewer, and QA director.\n\n"
                + _ACCEPTANCE_BASELINE
                + "You are the overwatch. Audit the reviews rather than repeating them: "
                "take each ask in the brief and say what evidence proves it — a command "
                "and its output — or that nothing does. Run your own spot checks on what "
                "the reviews claim passed. Do not promote a syntax check, file-presence "
                "check, self-report, or label-string check into semantic or visual "
                "evidence. Required behavior without adequate evidence remains blocking, "
                "including required visual usability without rendered inspection. "
                "For required exchange-format output, require actual consumer/reader "
                "acceptance; distinguish an ordinary successful import from recovery "
                "that repairs the file or discards entities. Recovery-only acceptance "
                "does not meet ordinary interchange requirements unless the brief "
                "explicitly permits that repair. If required validation is unavailable, "
                "preserve the artifact and existing evidence, identify the missing "
                "external dependency and exact unblock action for the CEO or person, "
                "and keep that requirement unverified. Do not prescribe re-authoring "
                "or repeating an unchanged probe to resolve an unavailable tool. "
                "Write `reviews/overwatch.md` ending with a "
                "line that is exactly `VERDICT: PASS` or `VERDICT: FAIL`, and name every "
                "ask with no evidence behind it."
            ),
            reads=("brief.json", "design.md", "reviews/qa-synthesized.md"),
            writes=("reviews/overwatch.md",),
            produces=("overwatch",),
            notes="audit the evidence behind the reviews",
        ))
        graph.add(Node(
            "gate_overwatch", "gate", evaluator="refactory_dsh.create_ops:gate_overwatch",
            reads=("reviews/overwatch.md",),
            writes=("reviews/overwatch-gate.txt",),
            produces=("overwatch_review",),
            max_iterations=4,
            notes="proceed only on a stated PASS verdict",
        ))
        graph.add(Node(
            "report", "op", call=f"{OPS}:report_artifact",
            writes=("design.json",),
            produces=("designed",),
            notes="summarise what was designed",
        ))

        graph.connect("prepare", "research_director")
        graph.connect("research_director", "record_findings")
        graph.connect("record_findings", "gate_research")
        graph.connect("gate_research", "research_director", condition="reloop")
        graph.connect("gate_research", "strategy_director", condition="proceed")
        graph.connect("strategy_director", "synthesize_strategy")
        graph.connect("synthesize_strategy", "design_doc")
        graph.connect("design_doc", "gate_strategy")
        graph.connect("gate_strategy", "synthesize_strategy", condition="reloop")
        graph.connect("gate_strategy", "emit", condition="proceed")
        graph.connect("emit", "materialise")
        graph.connect("materialise", "check")
        graph.connect("check", "emit", condition="reloop")
        graph.connect("check", "fork_qa", condition="proceed")
        graph.connect("fork_qa", "join_qa")
        graph.connect("join_qa", "synthesize_qa")
        graph.connect("synthesize_qa", "gate_qa")
        graph.connect("gate_qa", "emit", condition="reloop")
        graph.connect("gate_qa", "overwatch", condition="proceed")
        graph.connect("overwatch", "gate_overwatch")
        graph.connect("gate_overwatch", "emit", condition="reloop")
        graph.connect("gate_overwatch", "report", condition="proceed")
        from refactory_dsh.authoring_evidence import wire_reviews
        wire_reviews(graph)
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
    parser.add_argument("--fast", action="store_true", help="use the inspectable design_fast graph with focused QA")
    parser.add_argument("--repair-attempts", type=int, default=1, help="fast graph repair limit (0..3)")
    parser.add_argument("--source-project", default="", help="fast design: copy this existing project before applying the feature")
    from refactory_dsh.budgets import add_arguments, arguments

    add_arguments(parser)
    # Per artifact name, for the same reason a creation is: one fixed directory
    # means runs overwrite each other's state.
    parser.add_argument("--workdir", default="")
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
        "--verdict", action="append", default=[], metavar="GATE=VALUE",
        help="answer a gate a person owns, as <gate>=<value> (repeatable)",
    )
    args = parser.parse_args(argv)

    from refactory_dsh.model import DshModelClient
    from refactory_dsh.paths import project_root

    if args.resume:
        client = DshModelClient(
            project=None if args.resume else project_root(),
            timeout_s=int(args.model_timeout_s), model=args.model, provider=args.provider,
        )
        try:
            from refactory_dsh.fast import DesignFastMode

            run = (DesignFastMode if args.fast else DesignMode).resume(
                Path(args.resume), model=client, resource_limits=arguments(args),
            )
        except Exception as exc:
            print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}), file=sys.stderr)
            return 2
        print(json.dumps({
            "resumed": str(Path(args.resume)), "exit_ok": run.exit_ok, "error": run.error,
            "facts": {key: value for key, value in run.facts.items() if key in
                      ("name", "files", "artifact", "check", "validation")},
        }, indent=2, default=str))
        return 0 if run.exit_ok else 2

    from refactory_dsh.fast import DesignFastMode

    mode = DesignFastMode() if args.fast else DesignMode()
    state = mode.state_dict()
    state["knobs"]["repair_attempts" if args.fast else "check_attempts"]["value"] = int(args.repair_attempts if args.fast else args.attempts)
    mode.load_state_dict(state)
    from refactory_dsh.create import parse_verdicts

    result = mode.run(
        Path(args.workdir or f".factory/design/{args.name}"),
        model=DshModelClient(
            project=None if args.resume else project_root(),
            timeout_s=int(args.model_timeout_s), model=args.model, provider=args.provider,
        ),
        task={"name": args.name, "brief": args.brief, "check": args.check, "source_project": args.source_project},
        verdicts=parse_verdicts(args.verdict),
        resource_limits=arguments(args),
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
