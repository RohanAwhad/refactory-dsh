"""Independent verification step: design-blind methods applied after the build's check.

The verifier is given the brief, the diff and the repository — never design.md.
It applies the evidence log's design-blind methods (M1, M2, M3, M11, M12, M13, M15)
and reports each finding with a runnable counterexample or a cited file:line.

See docs/hardening.md for why the verifier is design-blind.
"""

from __future__ import annotations

from typing import Any

VERIFICATION_METHODS = """
Apply each of these design-blind verification methods to the brief, the diff
and the repository. Do not read design.md or strategy.json.

M1  Adversarial inputs — find inputs the change accepts that would produce
    wrong output or violate the brief's constraints.
M2  Trace a real case — pick a concrete scenario the brief describes, trace it
    through the diff line by line, and report where it goes wrong or is not handled.
M3  Where else does this pattern appear — search the repository for every call
    site, re-export, or copy of the code the diff changes, and report any that
    the diff does not update.
M11 Verify claims against code — for every claim the diff's comments or
    docstrings make, confirm the code matches.
M12 Shared state — identify every file, environment variable, lock or process
    the diff's code reads or writes, find every other reader/writer in the
    repository, and report conflicts.
M13 Boundary values — for every numeric or string parameter the diff parses,
    test zero, one, maximum, empty, and type-boundary values against the code.
M15 Weakened tests — for every test the diff changes, determine whether the
    assertion is now weaker than before; a test that would pass when the code
    is wrong is blocking.
"""

VERIFIER_PROMPT = (
    "Artifact directory (absolute): {{artifact}}\n"
    "Read and run the delivered files at this exact path; do not infer it "
    "from your working directory. {{change}}\n\n"
    "Brief: {{brief}}\n\n"
    + VERIFICATION_METHODS +
    "\nFor each finding, report it as:\n"
    "BLOCKING: <file:line>: <description with a runnable counterexample or "
    "a cited file:line chain>\n\n"
    "Do NOT read design.md, strategy.json or any review files. You verify "
    "the change against the brief and the repository only.\n\n"
    "End with a line that is exactly VERDICT: PASS when there are no "
    "blocking findings, or VERDICT: FAIL when there are any."
)


def parse_verification_findings(ctx: Any) -> list[str]:
    """Parse BLOCKING findings and VERDICT from the verifier output."""
    from refactory_dsh.create_ops import PASS_MARK, blocking_findings

    text = ctx.read_text("reviews/verification.md") if ctx.exists(
        "reviews/verification.md") else ""
    if not text.strip():
        return ["reviews/verification.md is empty or missing"]
    findings = list(blocking_findings(text))
    verdict_line = text.strip().splitlines()[-1].strip()
    if verdict_line != PASS_MARK:
        findings.append(
            f"the verifier did not state {PASS_MARK!r}; "
            f"its last line is: {verdict_line!r}"
        )
    return findings


def wire_verification(graph: Any, *, fast: bool = False) -> None:
    """Add verifier and gate_verification nodes; rewire edges accordingly.

    In full mode (``fast=False``):
      check →(proceed) verifier → gate_verification →(proceed) fork_qa
                                                     →(reloop) emit

    In fast mode (``fast=True``):
      focused_check → verifier → gate_verification →(proceed) focused_qa
                                                    →(reloop) emit
    """
    from refactory_dsh.nn import Node

    OPS_FULL = "refactory_dsh.design_ops"
    OPS_FAST = "refactory_dsh.fast_ops"

    graph.add(Node(
        "verifier", "agent",
        prompt_template=VERIFIER_PROMPT,
        reads=("brief.json", "test-guardedness.json", "changes.diff"),
        writes=("reviews/verification.md",),
        produces=("verification",),
        role="code_reviewer",
        resource_role="final_validation" if fast else None,
        notes="design-blind verification: apply the evidence log's methods to the brief, diff and repo",
    ))

    if fast:
        graph.add(Node(
            "gate_verification", "gate",
            evaluator=f"{OPS_FAST}:gate_verification",
            resource_role="final_validation",
            reads=("reviews/verification.md",),
            writes=("reviews/verification-gate.txt", "fast-report.json", "repair.txt"),
            produces=("verification_gate", "repair_feedback", "repairs_used",
                      "review_repairs_used"),
            knobs=("repair_attempts",),
            max_iterations=4,
            notes="proceed on PASS; reloop to emit on FAIL within repair bound",
        ))
        # Remove focused_check→focused_qa edge
        graph.edges = [e for e in graph.edges
                       if not (e.source == "focused_check" and e.target == "focused_qa")]
        graph.connect("focused_check", "verifier")
        graph.connect("verifier", "gate_verification")
        graph.connect("gate_verification", "focused_qa", condition="proceed")
        graph.connect("gate_verification", "emit", condition="reloop")
    else:
        graph.add(Node(
            "gate_verification", "gate",
            evaluator=f"{OPS_FULL}:gate_verification",
            reads=("reviews/verification.md",),
            writes=("reviews/verification-gate.txt", "validation.txt"),
            produces=("verification_gate", "validation", "attempts_used",
                      "review_attempts_used"),
            knobs=("review_attempts",),
            max_iterations=4,
            notes="proceed on PASS; reloop to emit on FAIL within review bound",
        ))
        # Remove check→fork_qa (proceed) edge
        graph.edges = [e for e in graph.edges
                       if not (e.source == "check" and e.target == "fork_qa"
                               and e.condition == "proceed")]
        graph.connect("check", "verifier", condition="proceed")
        graph.connect("verifier", "gate_verification")
        graph.connect("gate_verification", "fork_qa", condition="proceed")
        graph.connect("gate_verification", "emit", condition="reloop")
