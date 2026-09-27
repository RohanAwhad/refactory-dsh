"""Small authoring graphs with sequential coding and focused review.

These are ordinary modes. Copy a state to customize research, QA or repair;
the runtime does not silently inject the full authoring graph.
"""

from __future__ import annotations

from dataclasses import replace

from refactory_dsh.create import CreateMode
from refactory_dsh.design import DesignMode
from refactory_dsh.edit import EditMode
from refactory_dsh.nn import Graph, Knob, Node

OPS = "refactory_dsh.fast_ops"

STRATEGY_PROMPT = (
    "Current stage: write one concise implementation plan for the coding agent.\n"
    "Brief: {{brief}}\n"
    "Use the supplied context to identify the files to change, the requested behavior "
    "and constraints to preserve, and the checks that establish completion. "
    "If a source detail is missing, read only the relevant named project/reference file. "
    "Do not inspect framework internals, worker receipts or prior acceptance history. "
    "Do not delegate, call other agents, research alternatives, implement files or run "
    "checks. Write implementation-plan.txt in at most 150 words, then return a short "
    "completion message. One coder and one focused QA agent follow sequentially; "
    "the runtime owns checks and any bounded repair.\n"
)


def _strategy_node(graph: Graph, *, artifact: bool) -> None:
    context = (
        "Existing source project: {{source_project}}\nSource files: {{source_files}}\n"
        "Required runtime check: {{check}}\n"
        if artifact else
        "Plan the minimum reusable graph, declared knobs, score and required artifact "
        "invariants; avoid alternative architectures.\n"
        "Package name: {{name}}\nClass of work: {{class_of_work}}\n"
        "Inputs: {{requires_input}}\nAPI: {{api}}\nReference source: {{reference}}\n"
    )
    graph.add(Node(
        "strategy", "agent", role="strategist", resource_role="work",
        prompt_template=STRATEGY_PROMPT + context,
        reads=("brief.json", "source-manifest.json" if artifact else "survey.json"),
        writes=("implementation-plan.txt",), produces=("implementation_plan",),
        notes="one concise strategy, followed by one coder and one QA agent; no parallel workers",
    ))

QA_PROMPT = (
    "Perform one focused QA review of the requested change. Brief: {{brief}}\n"
    "Artifact/package path: {{output_path}}\n"
    "Emitted or changed files to review: {{review_files}}\n"
    "Required check command and its runtime result: {{focused_check}}\n"
    "Read those files and relevant existing checks; inspect other project source only "
    "when needed to understand a dependency or finding. Use the supplied runtime result "
    "as evidence for the check already executed. Run any additional checks the brief "
    "requires that this result does not cover. Check the requested behavior "
    "and obvious regressions; do not delegate or launch research, other reviewers, an optimizer or "
    "a full model evaluation. Honor checks explicitly requested in the brief, and state "
    "any that could not run. Do not modify the implementation during QA. Do not inspect "
    "framework internals, delivery receipts or prior acceptance histories to discover "
    "this stage's output contract; it is specified below.\n"
    "Write focused-qa.json with exactly this shape: "
    '{"status":"pass" or "fail", "checks":[{"name":"...", "command":"... or source inspection", '
    '"status":"pass" or "fail" or "not_run", "evidence":"actual output or specific source finding"}], '
    '"findings":["unresolved defects"], "not_run":["checks outside this focused scope"]}. '
    "At least one substantive check is required. A required check not run is a failure. "
    "Structural admission, runtime validation and this review are different evidence. "
    "After writing focused-qa.json, return a short completion message. The runtime "
    "reads that file, applies the repair bound and advances the graph; do not run "
    "the gate, generate extra reports or repeat completed checks for handoff."
)


def _qa_nodes(graph: Graph, *, artifact: bool) -> None:
    graph.add(Node(
        "focused_check", "op", call=f"{OPS}:focused_check", role="validation" if artifact else "ceo",
        resource_role="final_validation",
        knobs=("check", "check_timeout_s"),
        reads=("emit.txt",) + (() if artifact else ("authoring-evidence.json", "smoke.json")),
        writes=("focused-check.json", "focused-source.json", "authoring-evidence.json"),
        produces=("focused_check", "output_path", "review_files", "runtime_evidence"),
        notes="run the explicitly requested bounded check; no implicit full evaluation",
    ))
    from refactory_dsh.authoring_evidence import REVIEW_CONTEXT, AUDIT_INSTRUCTIONS

    graph.add(Node(
        "focused_qa", "agent", role="code_reviewer", prompt_template=QA_PROMPT + REVIEW_CONTEXT + AUDIT_INSTRUCTIONS,
        resource_role="final_validation",
        reads=("brief.json", "focused-check.json", "authoring-evidence.json"),
        writes=("focused-qa.json", "intent-audit.json"),
        produces=("focused_qa",), notes="one light focused QA stage; no reviewer fan-out",
    ))
    graph.add(Node(
        "gate_qa", "gate", evaluator=f"{OPS}:gate_qa",
        resource_role="final_validation",
        knobs=("repair_attempts",), reads=("focused-qa.json", "focused-check.json", "focused-source.json",
                                          "authoring-evidence.json", "intent-audit.json"),
        writes=("fast-report.json", "repair.txt"),
        produces=("repair_feedback", "repairs_used", "qa_passed"), max_iterations=4,
        notes="repair only within the shared bound; unresolved findings are failure",
    ))
    graph.connect("focused_check", "focused_qa")
    graph.connect("focused_qa", "gate_qa")
    graph.connect("gate_qa", "emit", condition="reloop")


class CreateFastMode(CreateMode):
    name = "create_fast"
    description = "create a small package with one sequential strategist, coder and QA agent"
    plan_first = True

    @classmethod
    def declare(cls) -> dict[str, Knob]:
        knobs = super().declare()
        for name in ("research_attempts", "design_attempts", "attempts"):
            knobs.pop(name, None)
        knobs.update({
            "repair_attempts": Knob(1, "int", lo=0, hi=3, description="total repairs across admission, runtime validation and focused QA"),
            "check": Knob("", "str", description="explicit focused check command in the emitted package; empty means not requested"),
            "check_timeout_s": Knob(60, "int", lo=1, hi=600, description="seconds allowed for the explicit focused check"),
        })
        return knobs

    def build(self) -> Graph:
        full = super().build()
        graph = Graph()
        graph.add(replace(full.nodes["prepare"], call=f"{OPS}:prepare_package",
                          resource_role="work",
                          writes=(*full.nodes["prepare"].writes, "repair.txt", "validation.txt"),
                          produces=(*full.nodes["prepare"].produces, "repair_feedback", "repairs_used")))
        graph.add(replace(full.nodes["survey"], call=f"{OPS}:survey_package", resource_role="work",
                          notes="record only the explicitly requested reference; fast mode does not research other packages"))
        if self.plan_first:
            _strategy_node(graph, artifact=False)
        graph.add(Node(
            "emit", "agent", role="builder", resource_role="work",
            prompt_template=(
                "Current stage: emit the package into emit.txt.\n"
                + self.intent + "\nPackage name: {{name}}\nBrief: {{brief}}\n"
                + ("Implementation plan: {{implementation_plan}}\n" if self.plan_first else "")
                +
                "Class of work: {{class_of_work}}\nInputs: {{requires_input}}\n"
                "API: {{api}}\nSelected reference: {{survey}}\nReference source: {{reference}}\n"
                "Last structural validation: {{validation}}\nRepair findings: {{repair_feedback}}\n"
                "Implement the smallest complete reusable package that satisfies the brief. "
                "Use the supplied API and reference source directly. Inspect reference files "
                "only to resolve a concrete missing dependency or detail; do not search for "
                "other packages, read framework internals, delivery receipts or prior "
                "acceptance histories. Preserve Task bindings, feedable inputs and required "
                "artifact invariants. Do not delegate or launch research or review subagents. A focused QA stage "
                "and canonical runtime validation follow. Emit the whole package as file "
                "blocks introduced by ===FILE: <relative path>===, starting with __init__.py "
                "containing PACKAGE = nn.Package(...). Paths are relative to the package root, "
                "never prefixed with the package name. Write those blocks to emit.txt, then "
                "return a short completion message. Materialization, registration, structural "
                "admission, the requested check and canonical smoke belong to later runtime "
                "nodes; do not invoke them or locate their implementation during emission."
            ), reads=("brief.json", "survey.json", "repair.txt", "validation.txt")
                     + (("implementation-plan.txt",) if self.plan_first else ()),
            writes=("emit.txt",), produces=("emit",), notes="implement directly from the brief and current source",
        ))
        graph.add(replace(full.nodes["materialise"], resource_role="work"))
        graph.add(replace(full.nodes["validate"], evaluator=f"{OPS}:validate_package",
                          resource_role="work",
                          writes=("validation.txt", "repair.txt", "fast-report.json"),
                          knobs=("repair_attempts",), max_iterations=4,
                          produces=("validation", "repair_feedback", "repairs_used")))
        _qa_nodes(graph, artifact=False)
        graph.add(replace(full.nodes["register"], resource_role="work"))
        graph.add(replace(full.nodes["smoke"], evaluator="refactory_dsh.create_ops:gate_smoke",
                          knobs=("allow_regression", "sample_knobs", "smoke_timeout_s", "repair_attempts"),
                          writes=(*full.nodes["smoke"].writes, "repair.txt", "fast-report.json"),
                          produces=(*full.nodes["smoke"].produces, "repair_feedback", "repairs_used"),
                          resource_role="final_validation", max_iterations=4))
        graph.add(replace(full.nodes["report"], call=f"{OPS}:report_package",
                          resource_role="final_validation",
                          writes=("create.json", "fast-report.json")))
        if self.plan_first:
            graph.connect("survey", "strategy")
            graph.connect("strategy", "emit")
        else:
            graph.connect("survey", "emit")
        for source, target in (("prepare", "survey"),
                               ("emit", "materialise"), ("materialise", "validate"),
                               ("register", "smoke")):
            graph.connect(source, target)
        graph.connect("validate", "emit", condition="reloop")
        graph.connect("validate", "register", condition="proceed")
        graph.connect("smoke", "focused_check", condition="proceed")
        graph.connect("smoke", "emit", condition="reloop")
        graph.connect("gate_qa", "report", condition="proceed")
        return graph


class EditFastMode(CreateFastMode):
    name = "edit_fast"
    description = "make a small package revision directly with one coder, focused QA and regression validation"
    intent = EditMode.intent
    plan_first = False


class DesignFastMode(DesignMode):
    name = "design_fast"
    description = "build a small artifact with one sequential strategist, coder and QA agent"

    @classmethod
    def declare(cls) -> dict[str, Knob]:
        knobs = super().declare()
        knobs.pop("research_attempts")
        knobs.pop("check_attempts")
        knobs["repair_attempts"] = Knob(1, "int", lo=0, hi=3, description="total repair attempts after focused QA")
        knobs["source_project"] = Knob("", "str", description="existing project copied into the output artifact before applying the change")
        return knobs

    def build(self) -> Graph:
        graph = Graph()
        graph.add(Node(
            "prepare", "op", call=f"{OPS}:prepare_artifact", resource_role="work",
            knobs=("name", "brief", "check", "source_project"),
            writes=("brief.json", "source-manifest.json", "repair.txt", "research-review.txt", "check.txt"),
            produces=("name", "brief", "check", "validation", "source_project", "source_files", "repair_feedback", "repairs_used"),
            notes="read the artifact brief and capture the source project identity",
        ))
        _strategy_node(graph, artifact=True)
        graph.add(Node(
            "emit", "agent", role="builder", resource_role="work",
            prompt_template=(
                "Current stage: emit the changed files into emit.txt.\n"
                "Build artifact {{name}}. Brief: {{brief}}\n"
                "Implementation plan: {{implementation_plan}}\n"
                "Existing source project (read only): {{source_project}}\n"
                "Source file list: {{source_files}}\n"
                "Required check from the artifact root: {{check}}\n"
                "Repair feedback: {{repair_feedback}}\n"
                "Implement the smallest complete change. For an existing project emit only "
                "new or changed files; unchanged source is copied by the materialise node. "
                "Read the relevant project files to make the change. The file-block contract "
                "below is complete: do not read framework internals, worker receipts, graph "
                "configuration or prior acceptance histories to rediscover it. "
                "Never write to the source project. Do not delegate or launch research, reviewer agents "
                "or an optimizer. Write emit.txt with file blocks introduced by exactly "
                "===FILE: <relative path>=== followed by the full file contents. Paths are "
                "relative to the artifact root. The runtime runs the requested check and "
                "one focused QA review next. Preserve all requested behavior. After writing "
                "emit.txt, return a short completion message. Do not materialize the artifact "
                "or invoke downstream graph operations yourself."
            ), reads=("brief.json", "source-manifest.json", "implementation-plan.txt", "repair.txt"),
            writes=("emit.txt",), produces=("emit",), notes="implement the small artifact/change directly",
        ))
        graph.add(Node(
            "materialise", "op", call=f"{OPS}:materialise_artifact", resource_role="work",
            reads=("emit.txt", "source-manifest.json"), produces=("artifact", "files"),
            notes="copy the source and apply emitted files to the separate output artifact",
        ))
        _qa_nodes(graph, artifact=True)
        graph.add(Node(
            "report", "op", call=f"{OPS}:report_artifact", resource_role="final_validation",
            reads=("focused-qa.json", "focused-check.json"), writes=("design.json", "fast-report.json"),
            produces=("designed",), notes="report only observed checks and the actual repair limit",
        ))
        graph.connect("prepare", "strategy")
        graph.connect("strategy", "emit")
        graph.connect("emit", "materialise")
        graph.connect("materialise", "focused_check")
        graph.connect("gate_qa", "report", condition="proceed")
        return graph
