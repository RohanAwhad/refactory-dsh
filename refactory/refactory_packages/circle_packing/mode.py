"""The circle-packing mode, ported from the ``circle_packing_fast`` JSON graph.

The seed pipeline is a population search:

    task_input -> seed_run -> select_parent -> generate -> budget_check
                    ^                                        |
                    |                            reloop ->  eval -> accept
                    +----------------------------------------+
                                    proceed -> exit

What the outer loop optimizes is the *generator*: the grid the placements are
drawn from, the order circles are placed in, how parents are chosen, how
strictly a candidate must beat its parent, and how much budget the ladder gets.
It also optimizes the shape of the pipeline, and the seed shape has a real
defect worth finding: ``generate`` is deterministic, so once the seed is in the
population every further iteration re-evaluates the same answer.
"""

from __future__ import annotations

from refactory_dsh import nn

OPS = "refactory_packages.circle_packing.ops"


class CirclePacking(nn.Mode):
    """A population search over circle placements."""

    name = "adaptive"
    description = "population search over circle placements with a budget gate"

    @classmethod
    def declare(cls) -> dict[str, nn.Knob]:
        return {
            "grid": nn.Knob(
                10, "enum", enum=(6, 10, 14, 18, 24),
                description="grid resolution the placements are drawn from",
            ),
            "order": nn.Knob(
                "desc", "enum", enum=("desc", "asc"),
                description="whether the largest or the smallest circles are placed first",
            ),
            "selector": nn.Knob(
                "best", "enum", enum=("best", "tournament", "uniform"),
                description="how the parent for the next candidate is chosen",
            ),
            "acceptance_mode": nn.Knob(
                "strict", "enum", enum=("strict", "lenient", "off"),
                description="how much better than its parent a candidate must be",
            ),
            "budget": nn.Knob(
                3, "int", lo=1, hi=8,
                description="how many candidates the ladder may evaluate",
            ),
            "seed": nn.Knob(
                7, "int", frozen=True,
                description="the generator's random seed; frozen so a run reproduces",
            ),
        }

    def build(self) -> nn.Graph:
        graph = nn.Graph()
        graph.add(nn.Node(
            "task_input", "op", call=f"{OPS}:task_input",
            writes=("task.json", "budget_state.json"),
            produces=("circles",),
            knobs=("budget",),
            notes="write the instance and open the budget",
        ))
        graph.add(nn.Node(
            "seed_run", "op", call=f"{OPS}:seed_run",
            reads=("task.json",),
            writes=("population.json", "best_solution.json", "genealogy.json",
                    "accepted_history.json", "rejection_history.json"),
            produces=("seed_score",),
            notes="seed the population with the task's own baseline heuristic",
        ))
        graph.add(nn.Node(
            "select_parent", "op", call=f"{OPS}:select_parent",
            reads=("population.json",),
            writes=("selected_parent.json",),
            produces=("parent_id", "parent_score"),
            knobs=("selector", "seed"),
            notes="choose the individual the next candidate comes from",
        ))
        graph.add(nn.Node(
            "generate", "op", call=f"{OPS}:generate",
            reads=("task.json", "selected_parent.json", "budget_state.json"),
            writes=("candidate.json",),
            produces=("candidate_id",),
            knobs=("grid", "order", "seed"),
            notes="draw the next candidate from the resolved generator settings",
        ))
        graph.add(nn.Node(
            "budget_check", "gate", evaluator=f"{OPS}:budget_check",
            reads=("budget_state.json",),
            produces=("spent",),
            max_iterations=64,
            notes="reloop while budget remains",
        ))
        graph.add(nn.Node(
            "eval", "op", call=f"{OPS}:evaluate_candidate",
            reads=("task.json", "candidate.json", "budget_state.json"),
            writes=("eval_result.json", "budget.jsonl", "budget_state.json"),
            produces=("candidate_score",),
            notes="score the candidate with the task's rule",
        ))
        graph.add(nn.Node(
            "accept", "op", call=f"{OPS}:accept_or_reject",
            reads=("candidate.json", "eval_result.json", "population.json",
                   "genealogy.json", "accepted_history.json", "rejection_history.json",
                   "selected_parent.json", "budget_state.json"),
            writes=("population.json", "best_solution.json", "genealogy.json",
                    "accepted_history.json", "rejection_history.json",
                    "accepted_history.jsonl", "candidates.jsonl", "evaluations.jsonl"),
            produces=("accepted", "best_score_so_far"),
            knobs=("acceptance_mode",),
            notes="keep the candidate when it beats its parent",
        ))
        graph.add(nn.Node(
            "exit", "op", call=f"{OPS}:finish",
            reads=("population.json", "budget_state.json"),
            writes=("best_score.json", "best_solution.json"),
            produces=("best_score", "best_id", "spent", "population"),
            notes="publish the number the outer loop maximizes",
        ))

        graph.connect("task_input", "seed_run")
        graph.connect("seed_run", "select_parent")
        graph.connect("select_parent", "generate")
        graph.connect("generate", "budget_check")
        graph.connect("budget_check", "eval", condition="reloop")
        graph.connect("budget_check", "exit", condition="proceed")
        graph.connect("eval", "accept")
        graph.connect("accept", "select_parent")
        return graph

    def score(self, run: nn.RunResult) -> float:
        """The task's own score: the radii of the circles that fit."""
        return float(run.facts["best_score"])

    def invariants(self) -> tuple[nn.Invariant, ...]:
        """The metric must be produced by the pipeline the candidate actually runs."""
        return (nn.fact_produced("best_score"),)

    def insertable(self) -> tuple[nn.Node, ...]:
        """Blocks a structural edit may add to this pipeline.

        ``restart_on_stall`` is the interesting one: the seed pipeline's generator
        is deterministic, so it re-evaluates one answer forever, and the fix is a
        node rather than a parameter.
        """
        return (
            nn.Node(
                "restart_on_stall", "op", call=f"{OPS}:robust_select_parent",
                reads=("population.json", "rejection_history.json", "budget_state.json"),
                writes=("population.json", "selected_parent.json", "budget_state.json"),
                produces=("parent_id", "parent_score"),
                knobs=("seed",),
                notes="restart from a fresh jittered sample once the search stalls",
            ),
            nn.Node(
                "observe", "op", call=f"{OPS}:observe",
                reads=("population.json",),
                writes=("observations.json",),
                produces=("population", "spread"),
                notes="record how varied the population is",
            ),
            nn.Node(
                "critic", "agent",
                prompt_template=(
                    "The search is working on a circle-packing instance with "
                    "{{circles}} circles. The current candidate is {{candidate_id}} "
                    "and the parent is {{parent_id}}. In two sentences, say what to "
                    "change about the generator settings to place more circles, and "
                    "why."
                ),
                reads=("candidate.json", "selected_parent.json"),
                writes=("critique.txt",),
                produces=("critique",),
                notes="advise on the next generator settings",
            ),
            nn.Node(
                "retry_gate", "gate", evaluator=f"{OPS}:retry_gate",
                reads=("eval_result.json",),
                produces=("eval_attempts",),
                max_iterations=3,
                notes="retry an evaluation that produced no score",
            ),
        )
