"""Small authoring graphs: implementation, one focused review, bounded repair.

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

QA_PROMPT = (
    "Perform one focused QA review of the requested change. Brief: {{brief}}\n"
    "Artifact/package path: {{output_path}}\n"
    "Required check command and its runtime result: {{focused_check}}\n"
    "Read the changed source and relevant existing checks. Check the requested behavior "
    "and obvious regressions; do not launch research, other reviewers, an optimizer or "
    "a full model evaluation. Honor checks explicitly requested in the brief, and state "
    "any that could not run. Do not modify the implementation during QA.\n"
    "Write focused-qa.json with exactly this shape: "
    '{"status":"pass" or "fail", "checks":[{"name":"...", "command":"... or source inspection", '
    '"status":"pass" or "fail" or "not_run", "evidence":"actual output or specific source finding"}], '
    '"findings":["unresolved defects"], "not_run":["checks outside this focused scope"]}. '
    "At least one substantive check is required. A required check not run is a failure. "
    "Structural admission, this model-authored review and the separate runtime "
    "validation are different evidence; do not claim pending validation passed."
)


def _qa_nodes(graph: Graph, *, artifact: bool) -> None:
    graph.add(Node(
        "focused_check", "op", call=f"{OPS}:focused_check", role="validation" if artifact else "ceo",
        knobs=("check", "check_timeout_s"), writes=("focused-check.json", "focused-source.json"),
        produces=("focused_check", "output_path"),
        notes="run the explicitly requested bounded check; no implicit full evaluation",
    ))
    graph.add(Node(
        "focused_qa", "agent", role="code_reviewer", prompt_template=QA_PROMPT,
        reads=("brief.json", "focused-check.json"), writes=("focused-qa.json",),
        produces=("focused_qa",), notes="one light focused QA stage; no reviewer fan-out",
    ))
    graph.add(Node(
        "gate_qa", "gate", evaluator=f"{OPS}:gate_qa",
        knobs=("repair_attempts",), reads=("focused-qa.json", "focused-check.json", "focused-source.json"),
        writes=("fast-report.json", "repair.txt"),
        produces=("repair_feedback", "repairs_used", "qa_passed"), max_iterations=3,
        notes="repair only within the shared bound; unresolved findings are failure",
    ))
    graph.connect("focused_check", "focused_qa")
    graph.connect("focused_qa", "gate_qa")
    graph.connect("gate_qa", "emit", condition="reloop")


class CreateFastMode(CreateMode):
    name = "create_fast"
    description = "create a small package with focused QA and at most one repair by default"

    @classmethod
    def declare(cls) -> dict[str, Knob]:
        knobs = super().declare()
        for name in ("research_attempts", "design_attempts", "attempts"):
            knobs.pop(name, None)
        knobs.update({
            "repair_attempts": Knob(1, "int", lo=0, hi=3, description="total repairs across structural admission and focused QA"),
            "check": Knob("", "str", description="explicit focused check command in the emitted package; empty means not requested"),
            "check_timeout_s": Knob(60, "int", lo=1, hi=600, description="seconds allowed for the explicit focused check"),
        })
        return knobs

    def build(self) -> Graph:
        full = super().build()
        graph = Graph()
        graph.add(replace(full.nodes["prepare"], call=f"{OPS}:prepare_package",
                          writes=(*full.nodes["prepare"].writes, "repair.txt", "validation.txt"),
                          produces=(*full.nodes["prepare"].produces, "repair_feedback", "repairs_used")))
        graph.add(full.nodes["survey"])
        graph.add(Node(
            "emit", "agent", role="builder",
            prompt_template=(
                self.intent + "\nPackage name: {{name}}\nBrief: {{brief}}\n"
                "Class of work: {{class_of_work}}\nInputs: {{requires_input}}\n"
                "API: {{api}}\nExisting packages: {{survey}}\nReference source: {{reference}}\n"
                "Last structural validation: {{validation}}\nRepair findings: {{repair_feedback}}\n"
                "Implement the smallest complete reusable package that satisfies the brief. "
                "Use the API and preserve Task bindings, feedable inputs and required artifact "
                "invariants. Do not launch research or review subagents. A focused QA stage "
                "and canonical runtime validation follow. Emit the whole package as file "
                "blocks introduced by ===FILE: <relative path>===, starting with __init__.py "
                "containing PACKAGE = nn.Package(...). Paths are relative to the package root, "
                "never prefixed with the package name. Write those blocks to emit.txt."
            ), reads=("brief.json", "survey.json", "repair.txt", "validation.txt"),
            writes=("emit.txt",), produces=("emit",), notes="implement directly from the brief and current source",
        ))
        graph.add(full.nodes["materialise"])
        graph.add(replace(full.nodes["validate"], evaluator=f"{OPS}:validate_package",
                          writes=("validation.txt", "repair.txt", "fast-report.json"),
                          knobs=("repair_attempts",), max_iterations=3,
                          produces=("validation", "repair_feedback", "repairs_used")))
        _qa_nodes(graph, artifact=False)
        graph.add(full.nodes["register"])
        graph.add(replace(full.nodes["smoke"], call=f"{OPS}:smoke"))
        graph.add(replace(full.nodes["report"], call=f"{OPS}:report_package",
                          writes=("create.json", "fast-report.json")))
        for source, target in (("prepare", "survey"), ("survey", "emit"),
                               ("emit", "materialise"), ("materialise", "validate"),
                               ("register", "smoke"), ("smoke", "report")):
            graph.connect(source, target)
        graph.connect("validate", "emit", condition="reloop")
        graph.connect("validate", "focused_check", condition="proceed")
        graph.connect("gate_qa", "register", condition="proceed")
        return graph


class EditFastMode(CreateFastMode):
    name = "edit_fast"
    description = "revise a package with focused QA, bounded repair and regression validation"
    intent = EditMode.intent


class DesignFastMode(DesignMode):
    name = "design_fast"
    description = "build a small artifact or a feature in a copied project with focused QA"

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
            "prepare", "op", call=f"{OPS}:prepare_artifact",
            knobs=("name", "brief", "check", "source_project"),
            writes=("brief.json", "source-manifest.json", "repair.txt"),
            produces=("name", "brief", "check", "validation", "source_project", "repair_feedback", "repairs_used"),
            notes="read the artifact brief and capture the source project identity",
        ))
        graph.add(Node(
            "emit", "agent", role="builder",
            prompt_template=(
                "Build artifact {{name}}. Brief: {{brief}}\n"
                "Existing source project (read only): {{source_project}}\n"
                "Required check from the artifact root: {{check}}\n"
                "Repair feedback: {{repair_feedback}}\n"
                "Implement the smallest complete change. For an existing project emit only "
                "new or changed files; unchanged source is copied by the materialise node. "
                "Never write to the source project. Do not launch research, reviewer agents "
                "or an optimizer. Write emit.txt with file blocks introduced by exactly "
                "===FILE: <relative path>=== followed by the full file contents. Paths are "
                "relative to the artifact root. The runtime runs the requested check and "
                "one focused QA review next. Preserve all requested behavior."
            ), reads=("brief.json", "source-manifest.json", "repair.txt"),
            writes=("emit.txt",), produces=("emit",), notes="implement the small artifact/change directly",
        ))
        graph.add(Node(
            "materialise", "op", call=f"{OPS}:materialise_artifact",
            reads=("emit.txt", "source-manifest.json"), produces=("artifact", "files"),
            notes="copy the source and apply emitted files to the separate output artifact",
        ))
        _qa_nodes(graph, artifact=True)
        graph.add(Node(
            "report", "op", call=f"{OPS}:report_artifact",
            reads=("focused-qa.json", "focused-check.json"), writes=("design.json", "fast-report.json"),
            produces=("designed",), notes="report only observed checks and the actual repair limit",
        ))
        graph.connect("prepare", "emit")
        graph.connect("emit", "materialise")
        graph.connect("materialise", "focused_check")
        graph.connect("gate_qa", "report", condition="proceed")
        return graph
