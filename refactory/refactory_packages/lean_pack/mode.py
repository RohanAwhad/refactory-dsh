"""The Lean mode: state a theorem, attempt a proof, let the proof checker decide.

The pipeline is:

    task_input -> tool_check -> scaffold -> attempt -> verify -> repair_gate
                                                ^                   |
                                                +---- reloop --------+
                                                                    |
                                                          proceed -> finish

Nothing in this mode decides whether a proof is correct. ``verify`` runs the Lean
proof checker over a per-problem project and the run's number is what the checker
said: an exit code of zero *and* an axiom dump that does not mention ``sorryAx``.
The scoring rule is therefore a fact about the run rather than an assertion by the
author, which is the whole reason the mode is worth optimizing -- a candidate that
changed the statement, or the check, cannot score better.

``tool_check`` is a node rather than a note because the interesting failure is
absence: a machine without Lean must fail at the first step, with the install
command, rather than forty minutes later inside a subprocess. The tool requirement
itself is declared in ``tools.py``; see ``refactory_dsh.toolchain``.
"""

from __future__ import annotations

from refactory_dsh import nn

from . import problems

OPS = "refactory_packages.lean_pack.ops"


class Prover(nn.Mode):
    """Attempt a Lean proof of a fixed theorem, and let Lean accept or reject it."""

    name = "prover"
    description = "prove a theorem in Lean and score what the Lean checker accepted"

    @classmethod
    def declare(cls) -> dict[str, nn.Knob]:
        return {
            "library": nn.Knob(
                "core", "enum", enum=problems.LIBRARIES,
                description="which library the theorem needs; the core problem runs without mathlib",
            ),
            "strategy": nn.Knob(
                "omega", "enum", enum=problems.STRATEGIES,
                description="which tactic block the first attempt writes",
            ),
            "attempts": nn.Knob(
                4, "int", lo=1, hi=4,
                description="how many strategies the retry gate may try before giving up",
            ),
            "timeout_s": nn.Knob(
                60, "int", lo=10, hi=300,
                description="how long one Lean invocation may take before it is abandoned",
            ),
        }

    def build(self) -> nn.Graph:
        graph = nn.Graph()
        graph.add(nn.Node(
            "task_input", "op", call=f"{OPS}:load_problem",
            writes=("problem.json",),
            produces=("problem_name",),
            knobs=("library",),
            notes="write the theorem to be proved and its proof strategies",
        ))
        graph.add(nn.Node(
            "tool_check", "op", call=f"{OPS}:check_toolchain",
            reads=("problem.json",),
            writes=("toolchain.json",),
            produces=("lean_version",),
            knobs=("library",),
            notes="refuse the run here, with the install command, if Lean is absent",
        ))
        graph.add(nn.Node(
            "scaffold", "op", call=f"{OPS}:scaffold_project",
            reads=("problem.json",),
            writes=("project", "statement_check.json"),
            produces=("statement_ok",),
            knobs=("library", "timeout_s"),
            notes="write the lakefile and the statement, then check the statement alone",
        ))
        graph.add(nn.Node(
            "attempt", "op", call=f"{OPS}:write_solution",
            reads=("problem.json", "verify.json"),
            writes=("project/Solution.lean", "attempt.json"),
            produces=("strategy_used",),
            knobs=("strategy",),
            notes="write the next strategy's proof into the solution file",
        ))
        graph.add(nn.Node(
            "verify", "op", call=f"{OPS}:check_with_lean",
            reads=("problem.json", "attempt.json", "project/Solution.lean"),
            writes=("verify.json",),
            produces=("verified",),
            knobs=("timeout_s",),
            notes="run the Lean proof checker; its verdict is the run's number",
        ))
        graph.add(nn.Node(
            "repair_gate", "gate", evaluator=f"{OPS}:retry_gate",
            reads=("problem.json", "verify.json"),
            produces=("attempts_used",),
            knobs=("attempts",),
            max_iterations=8,
            notes="reloop while an untried strategy remains",
        ))
        graph.add(nn.Node(
            "finish", "op", call=f"{OPS}:finish",
            reads=("problem.json", "verify.json"),
            writes=("result.json",),
            produces=("verified", "attempts_used", "best_score"),
            notes="publish the checker's verdict and the attempts it took",
        ))

        graph.connect("task_input", "tool_check")
        graph.connect("tool_check", "scaffold")
        graph.connect("scaffold", "attempt")
        graph.connect("attempt", "verify")
        graph.connect("verify", "repair_gate")
        graph.connect("repair_gate", "attempt", condition="reloop")
        graph.connect("repair_gate", "finish", condition="proceed")
        return graph

    def score(self, run: nn.RunResult) -> float:
        """What Lean accepted, discounted by how many attempts it took to get there.

        A proof found on the first attempt is better than the same proof found on
        the fourth: the run spent less of the machine's time for the same theorem.
        The discount is what gives the outer loop a gradient, because ``verified``
        alone is binary and every candidate that proves the theorem would tie.
        """
        if not run.facts.get("verified"):
            return 0.0
        attempts = max(1, int(run.facts.get("attempts_used", 1)))
        return round(1.0 - 0.1 * (attempts - 1), 6)

    def invariants(self) -> tuple[nn.Invariant, ...]:
        """Both facts the score reads must come from the pipeline that ran."""
        return (nn.fact_produced("verified", "attempts_used"),)

    def ports(self) -> tuple[nn.Port, ...]:
        return (nn.Port(
            name="result",
            path="result.json",
            media_type="application/json",
            description="the checker's verdict, the strategy that produced it, and Lean's output",
        ),)

    def guidance(self) -> str:
        """What only a spent batch would show, said up front.

        The retry gate already walks the strategies, so the mode proves the core
        theorem without any search. The levers are which strategy is tried first
        and how many attempts are allowed: on the mathlib problem the default
        strategy does not close the goal, so the first attempt is spent learning
        that, and moving ``strategy`` to the one that works is worth 0.1.
        """
        return (
            "The score is what Lean accepted, so no structural edit can raise it: "
            "only a proof the checker passes can. The retry gate rotates through "
            "the strategies, so `attempts` decides whether a run gets there at all "
            "and `strategy` decides whether it gets there on the first try, which "
            "is worth 0.1. `library` switches to the mathlib problem, which needs "
            "the `mathlib` tool and is where the default strategy fails."
        )

    def preferred_edits(self) -> tuple[str, ...]:
        return ("set_knob", "insert_after")

    def insertable(self) -> tuple[nn.Node, ...]:
        """Blocks a structural edit may add. One, and it is advisory.

        A critique of Lean's error output cannot change what the checker accepts,
        so it is declared ``score_effect='none'``: an optimizer that sees it beside
        a node that can move the number should be able to tell them apart.
        """
        return (nn.Node(
            "critic", "agent",
            score_effect="none",
            prompt_template=(
                "Lean rejected the proof of `{{problem_name}}` that is recorded in "
                "verify.json. In two sentences, say which tactic is more likely to "
                "close the goal, and why."
            ),
            reads=("verify.json",),
            writes=("critique.txt",),
            produces=("critique",),
            notes="read the checker's errors and suggest a different tactic",
        ),)
