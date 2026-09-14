"""The sympy mode: conjecture a closed form, then let exact algebra decide.

The pipeline is:

    task_input -> derive -> budget_check
                     ^            |
                     |  reloop    |
                     +------------+
                                  |
                        proceed -> finish

Something else in this repository's packages is worth knowing before reading this
one: it is the Python-tool half of the pair. ``lean_pack`` needs a toolchain, a
proof checker and a library checkout; this needs one importable module; both declare
what they need in a ``tools.py`` beside their manifest and both are read by the same
reader. That is the claim the two packages exist to make together.

``derive`` establishes an identity by exact algebra rather than by sampling: sympy
computes the sum, and the residual against a conjectured closed form has to reduce
to zero as a symbol in ``n``. The score is how many of the identities were
established, which is a fact of the run.
"""

from __future__ import annotations

from refactory_dsh import nn

from .ops import ROUTES

OPS = "refactory_packages.sympy_pack.ops"


class Exact(nn.Mode):
    """Establish closed forms of sums exactly, and score how many were established."""

    name = "exact"
    description = "establish closed forms of sums with sympy and score what checked out"

    @classmethod
    def declare(cls) -> dict[str, nn.Knob]:
        return {
            "candidates": nn.Knob(
                1, "int", lo=1, hi=4,
                description="how many conjectured closed forms are checked per identity",
            ),
            "problems": nn.Knob(
                3, "int", lo=1, hi=3,
                description="how many identities the run attempts",
            ),
            "route": nn.Knob(
                "simplify", "enum", enum=ROUTES,
                description="how the residual is reduced before it is compared to zero",
            ),
        }

    def build(self) -> nn.Graph:
        graph = nn.Graph()
        graph.add(nn.Node(
            "task_input", "op", call=f"{OPS}:load_problems",
            writes=("problems.json",),
            produces=("problem_count",),
            knobs=("problems",),
            notes="write the identities this run will attempt",
        ))
        graph.add(nn.Node(
            "derive", "op", call=f"{OPS}:check_identity",
            reads=("problems.json", "state.json"),
            writes=("state.json", "results.jsonl"),
            produces=("checked_so_far",),
            knobs=("candidates", "route"),
            notes="derive one closed form and check the conjectures for it exactly",
        ))
        graph.add(nn.Node(
            "budget_check", "gate", evaluator=f"{OPS}:budget_check",
            reads=("problems.json", "state.json"),
            produces=("verified_so_far",),
            max_iterations=8,
            notes="reloop while an identity remains unattempted",
        ))
        graph.add(nn.Node(
            "finish", "op", call=f"{OPS}:finish",
            reads=("problems.json", "state.json", "results.jsonl"),
            writes=("result.json",),
            produces=("verified", "attempted"),
            notes="publish how many identities were established exactly",
        ))

        graph.connect("task_input", "derive")
        graph.connect("derive", "budget_check")
        graph.connect("budget_check", "derive", condition="reloop")
        graph.connect("budget_check", "finish", condition="proceed")
        return graph

    def score(self, run: nn.RunResult) -> float:
        """How many identities exact algebra established; never how many were asked."""
        return float(run.facts["verified"])

    def invariants(self) -> tuple[nn.Invariant, ...]:
        """The number the score reads must be produced by the pipeline that ran."""
        return (nn.fact_produced("verified"),)

    def ports(self) -> tuple[nn.Port, ...]:
        return (nn.Port(
            name="result",
            path="result.json",
            media_type="application/json",
            description="the identities attempted and the closed forms that checked out",
        ),)

    def guidance(self) -> str:
        """What the seed's own defect is, said before a batch is spent on it.

        The seed checks one conjecture per identity and every identity's first
        conjecture is wrong, so it establishes nothing. Raising ``candidates`` is
        the lever, and it is worth one identity per step because the true form of
        each is at a different position in its list. ``route`` is the second lever
        and only bites once the conjectures are in reach: a reduction that does not
        collect a polynomial difference will miss an answer that is there.
        """
        return (
            "The seed checks one conjecture per identity, and the first conjecture "
            "for every identity is wrong, so it establishes nothing and scores 0. "
            "`candidates` is the lever: the true closed form of each identity sits "
            "at a different position in its list, so each step up establishes one "
            "more. `route` matters only once the conjectures are in reach, because "
            "a weaker reduction misses an answer that is genuinely there."
        )

    def preferred_edits(self) -> tuple[str, ...]:
        return ("set_knob", "insert_after")

    def insertable(self) -> tuple[nn.Node, ...]:
        """Blocks a structural edit may add.

        ``numeric_check`` is offered as an advisory node on purpose: sampling an
        identity at a few values of ``n`` confirms nothing exactly, so it is
        declared ``score_effect='none'`` -- a block that cannot move the number has
        to be distinguishable from one that can.
        """
        return (nn.Node(
            "numeric_check", "op", call=f"{OPS}:numeric_probe",
            reads=("problems.json", "state.json"),
            writes=("numeric_probe.json",),
            produces=("numeric_agreements",),
            score_effect="none",
            notes="sample the last identity at a few values of n, as a sanity check only",
        ),)
