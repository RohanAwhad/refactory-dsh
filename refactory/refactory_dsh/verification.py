"""Independent verification step: design-blind methods applied after the build's check passes.

The verifier is given the brief, the diff and the repository — never design.md.
It applies the evidence log's design-blind methods (M1, M2, M3, M11, M12, M13, M15)
and reports each finding with a runnable counterexample or a cited file:line.

See docs/hardening.md §15 for why the verifier is design-blind.
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
    "design.md is in the run directory. Do NOT read it, or strategy.json, "
    "or any review files. You verify the change against the brief and the "
    "repository only.\n\n"
    "End with a line that is exactly VERDICT: PASS when there are no "
    "blocking findings, or VERDICT: FAIL when there are any."
)

#: Maximum advisory reloops before the verifier gate proceeds anyway.
#: Verifier noise is unmeasured, so the gate must never end a run on its own.
VERIFIER_GATE_LIMIT = 3


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


def gate_verification_generic(ctx: Any) -> str:
    """Advisory verification gate for modes without dedicated review budgets.

    Always proceeds: writes unresolved findings when present but never
    blocks or spends attempt counters.  Used by fix_pack and any future
    mode that uses repo_change but has no review stage.
    """
    findings = parse_verification_findings(ctx)
    if not findings:
        ctx.write_text("reviews/verification-gate.txt", "proceed: no blocking finding\n")
        return "proceed"
    feedback = "\n".join(findings)
    ctx.write_text("reviews/verification-gate.txt", feedback + "\n")
    ctx.write_text("reviews/verification-unresolved.md", feedback + "\n")
    return "proceed"


def wire_verification(graph: Any, *, fast: bool = False,
                      check_node: str = "", proceed_target: str = "",
                      evaluator: str = "") -> None:
    """Add verifier and gate_verification nodes; rewire edges accordingly.

    In full mode (``fast=False``, default names):
      check →(proceed) verifier → gate_verification →(proceed) fork_qa
                                                      →(reloop) emit

    In fast mode (``fast=True``, default names):
      focused_check → verifier → gate_verification →(proceed) focused_qa
                                                     →(reloop) emit

    Custom wiring (``check_node`` and ``proceed_target`` given):
      check_node →(proceed) verifier → gate_verification →(proceed) proceed_target
                                                           →(reloop) emit

    Any mode that uses ``repo_change`` should call this to get the verifier.
    """
    from refactory_dsh.nn import Node

    OPS_FULL = "refactory_dsh.design_ops"
    OPS_FAST = "refactory_dsh.fast_ops"
    OPS_GENERIC = "refactory_dsh.verification"

    # Determine node names and evaluator based on mode.
    if check_node and proceed_target:
        src = check_node
        dst = proceed_target
        gate_eval = evaluator or f"{OPS_GENERIC}:gate_verification_generic"
        src_condition = "proceed"
    elif fast:
        src = "focused_check"
        dst = "focused_qa"
        gate_eval = f"{OPS_FAST}:gate_verification"
        src_condition = None
    else:
        src = "check"
        dst = "fork_qa"
        gate_eval = f"{OPS_FULL}:gate_verification"
        src_condition = "proceed"

    graph.add(Node(
        "verifier", "agent",
        prompt_template=VERIFIER_PROMPT,
        reads=("brief.json", "test-guardedness.json", "changes.diff"),
        writes=("reviews/verification.md",),
        produces=("verification",),
        role="code_reviewer",
        resource_role="final_validation",
        notes="design-blind verification: apply the evidence log's methods to the brief, diff and repo",
    ))

    if fast:
        graph.add(Node(
            "gate_verification", "gate",
            evaluator=gate_eval,
            resource_role="final_validation",
            reads=("reviews/verification.md", "focused-check.json", "check.txt"),
            writes=("reviews/verification-gate.txt", "reviews/verification-unresolved.md",
                    "fast-report.json", "repair.txt"),
            produces=("verification_gate", "repair_feedback", "repairs_used",
                      "check_repairs_used", "verification_iterations_used"),
            knobs=("repair_attempts",),
            max_iterations=VERIFIER_GATE_LIMIT + 2,
            notes="proceed on PASS; advisory reloop on FAIL (does not spend repair_attempts); check failures and guardedness blocks via _repair",
        ))
    elif check_node and proceed_target:
        graph.add(Node(
            "gate_verification", "gate",
            evaluator=gate_eval,
            resource_role="final_validation",
            reads=("reviews/verification.md",),
            writes=("reviews/verification-gate.txt", "reviews/verification-unresolved.md"),
            produces=("verification_gate",),
            max_iterations=2,
            notes="advisory verification gate; always proceeds",
        ))
    else:
        graph.add(Node(
            "gate_verification", "gate",
            evaluator=gate_eval,
            resource_role="final_validation",
            reads=("reviews/verification.md",),
            writes=("reviews/verification-gate.txt", "reviews/verification-unresolved.md",
                    "validation.txt"),
            produces=("verification_gate", "validation",
                      "verification_iterations_used"),
            knobs=("review_attempts",),
            max_iterations=VERIFIER_GATE_LIMIT + 2,
            notes="proceed on PASS; advisory reloop on FAIL (does not spend review_attempts)",
        ))

    # Remove the existing edge — raise when it does not exist.
    old_edges = [e for e in graph.edges
                 if e.source == src and e.target == dst
                 and e.condition == src_condition]
    if not old_edges:
        raise ValueError(
            f"wire_verification: no edge {src!r} ->({src_condition!r}) {dst!r} "
            f"to replace; existing edges: "
            + ", ".join(f"{e.source}->{e.target}({e.condition})" for e in graph.edges)
        )
    graph.edges = [e for e in graph.edges
                   if not (e.source == src and e.target == dst
                           and e.condition == src_condition)]

    if src_condition:
        graph.connect(src, "verifier", condition=src_condition)
    else:
        graph.connect(src, "verifier")
    graph.connect("verifier", "gate_verification")
    graph.connect("gate_verification", dst, condition="proceed")
    graph.connect("gate_verification", "emit", condition="reloop")
