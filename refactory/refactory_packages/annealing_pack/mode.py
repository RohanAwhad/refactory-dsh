"""The annealing-packing mode.

The seed pipeline is a stochastic hill-climb over the greedy packer's placement:

    seed_placement -> perturb_circle -> accept_or_revert -> iterations_gate
                          ^                                      |
                          +---------------- loop ----------------+
                                                                 |
                                            proceed ->         finish

One cycle, and it passes through ``iterations_gate``, which is the only node
that bounds the run. ``finish`` is the only terminal and the sole producer of the
fact ``score()`` reads.
"""

from __future__ import annotations

from refactory_dsh import nn

OPS = "refactory_packages.annealing_pack.ops"


class Anneal(nn.Mode):
    """A stochastic hill-climb over a greedy circle packing."""

    name = "anneal"
    description = "hill-climb a greedy circle packing by accepting non-worsening moves"

    @classmethod
    def declare(cls) -> dict[str, nn.Knob]:
        return {
            "iterations": nn.Knob(
                200, "int", lo=1, hi=400,
                description="how many single-circle moves the gate lets the loop run",
            ),
            "step": nn.Knob(
                0.02, "float", lo=0.001, hi=0.25,
                description="half-width of the uniform offset applied to the chosen circle",
            ),
            "seed": nn.Knob(
                7, "int", frozen=True,
                description="the per-move RNG seed; frozen so a run reproduces",
            ),
        }

    def build(self) -> nn.Graph:
        graph = nn.Graph()
        graph.add(nn.Node(
            "seed_placement", "op", call=f"{OPS}:seed_placement",
            writes=("current.json", "best.json", "history.json", "progress.json"),
            produces=("seed_score",),
            notes="seed the working and best placement from the greedy packer",
        ))
        graph.add(nn.Node(
            "perturb_circle", "op", call=f"{OPS}:perturb_circle",
            reads=("current.json", "progress.json"),
            writes=("candidate.json", "progress.json"),
            produces=("candidate_score",),
            knobs=("step", "seed"),
            notes="move one random circle by a small offset and score the result",
        ))
        graph.add(nn.Node(
            "accept_or_revert", "op", call=f"{OPS}:accept_or_revert",
            reads=("current.json", "candidate.json", "best.json", "history.json", "progress.json"),
            writes=("current.json", "best.json", "history.json"),
            produces=("accepted", "current_score", "best_score_so_far"),
            notes="keep the move when the task score does not fall",
        ))
        graph.add(nn.Node(
            "iterations_gate", "gate", evaluator=f"{OPS}:iterations_gate",
            reads=("progress.json",),
            produces=("iterations_run",),
            knobs=("iterations",),
            max_iterations=400,
            notes="loop while iterations remain, then proceed to the result",
        ))
        graph.add(nn.Node(
            "finish", "op", call=f"{OPS}:finish",
            reads=("best.json", "progress.json"),
            writes=("best_result.json",),
            produces=("best_score", "best_centers"),
            notes="publish the best score and the best placement",
        ))

        graph.connect("seed_placement", "perturb_circle")
        graph.connect("perturb_circle", "accept_or_revert")
        graph.connect("accept_or_revert", "iterations_gate")
        graph.connect("iterations_gate", "perturb_circle", condition="loop")
        graph.connect("iterations_gate", "finish", condition="proceed")
        return graph

    def score(self, run: nn.RunResult) -> float:
        """The task's own score: the radii of the circles that fit."""
        return float(run.facts["best_score"])

    def invariants(self) -> tuple[nn.Invariant, ...]:
        """The metric must be produced by the pipeline the candidate actually runs."""
        return (nn.fact_produced("best_score", "best_centers"),)

    def insertable(self) -> tuple[nn.Node, ...]:
        """A block a structural edit may add to this pipeline."""
        return (
            nn.Node(
                "observe_score_moves", "op", call=f"{OPS}:observe_score_moves",
                reads=("history.json",),
                writes=("score_trace.json",),
                produces=("score_delta",),
                notes="record how the score moved over the run",
            ),
        )
