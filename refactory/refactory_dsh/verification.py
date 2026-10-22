"""Independent verification step: design-blind, running in parallel with the reviewers.

The verifier is given the brief, the diff and the repository — never design.md.
It applies the evidence log's design-blind methods that no other reviewer applies
(M1, M2, M13, M15) and reports each finding with a runnable counterexample or a
cited file:line. The code reviewers already apply M2, M3, M11 and M12 to every
changed function (`REPO_CHANGE_REVIEW_INSTRUCTIONS`), so the verifier spends its
bounded time (`VERIFIER_TIMEOUT_S`) on what only a reader without the design finds.
It runs as an optional branch of the review fork: a verifier that fails or runs out
of time is missing, and its findings never get a builder round of their own.

See docs/hardening.md §15 for why the verifier is design-blind.
"""

from __future__ import annotations

from typing import Any

#: The most wall time the verifier's session may take, on either runtime. It runs
#: beside the three reviews, which take about eight minutes, so a verifier inside
#: this bound adds little or nothing to the round.
VERIFIER_TIMEOUT_S = 600

_METHODS_HEADER = """
Apply each of these design-blind verification methods to the brief, the diff
and the repository. Do not read design.md or strategy.json. The code reviewers
already check callers, shared state and the diff's own claims; do not repeat
that work. You have about ten minutes: report what you have found by then.
"""

M1_METHOD = """M1  Adversarial inputs — find inputs the change accepts that would produce
    wrong output or violate the brief's constraints."""

M2_METHOD = """M2  Trace a real case — pick a concrete scenario the brief describes, trace it
    through the diff line by line, and report where it goes wrong or is not handled."""

M3_METHOD = """M3  Where else does this pattern appear — search the repository for every call
    site, re-export, or copy of the code the diff changes, and report any that
    the diff does not update."""

M11_METHOD = """M11 Verify claims against code — for every claim the diff's comments or
    docstrings make, confirm the code matches."""

M12_METHOD = """M12 Shared state — identify every file, environment variable, lock or process
    the diff's code reads or writes, find every other reader/writer in the
    repository, and report conflicts."""

M13_METHOD = """M13 Boundary values — for every numeric or string parameter the diff parses,
    test zero, one, maximum, empty, and type-boundary values against the code."""

M15_METHOD = """M15 Weakened tests — for every test the diff changes, determine whether the
    assertion is now weaker than before; a test that would pass when the code
    is wrong is blocking."""

VERIFICATION_METHODS = (
    _METHODS_HEADER + "\n"
    + M1_METHOD + "\n"
    + M2_METHOD + "\n"
    + M13_METHOD + "\n"
    + M15_METHOD + "\n"
)

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


def wire_verification(graph: Any, *, check_node: str, proceed_target: str,
                      evaluator: str = "") -> None:
    """Add verifier and gate_verification nodes for custom wiring (fix_pack).

    Full-mode and fast-mode graphs add the verifier directly to the review fork;
    only the custom form (``check_node`` and ``proceed_target`` required) remains::

      check_node →(proceed) verifier → gate_verification →(proceed) proceed_target
                                                            →(reloop) emit
    """
    from refactory_dsh.nn import Node

    OPS_GENERIC = "refactory_dsh.verification"

    src = check_node
    dst = proceed_target
    gate_eval = evaluator or f"{OPS_GENERIC}:gate_verification_generic"
    src_condition = "proceed"

    graph.add(Node(
        "verifier", "agent",
        prompt_template=VERIFIER_PROMPT,
        timeout_s=VERIFIER_TIMEOUT_S,
        reads=("brief.json", "test-guardedness.json", "changes.diff"),
        writes=("reviews/verification.md",),
        produces=("verification",),
        role="code_reviewer",
        resource_role="final_validation",
        notes="design-blind verification: apply the evidence log's methods to the brief, diff and repo",
    ))

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

    graph.connect(src, "verifier", condition=src_condition)
    graph.connect("verifier", "gate_verification")
    graph.connect("gate_verification", dst, condition="proceed")
    graph.connect("gate_verification", "emit", condition="reloop")
