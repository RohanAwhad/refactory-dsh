"""The interactive review mode: a pipeline that ends at a gate a person answers.

    task_input -> draft -> check --proceed--> approve --proceed--> publish
                    ^         |                  |
                    +-reloop--+                  +---halt-------> reject

``check`` is a deterministic gate: it reloops while the draft is too thin to be
worth a person's attention. ``approve`` is a **user gate** — `gate_kind = "user"` —
so it carries no evaluator: a program must not be able to answer it. Under the CEO
the gate is presented to the person and their verdict is routed; a walker run stops
at it; a headless DSH run stops and prints the commands.
"""

from __future__ import annotations

from refactory_dsh import nn

OPS = "refactory_packages.review_pack.ops"


class Review(nn.Mode):
    """Draft a note, check it, and let a person approve it."""

    name = "review"
    description = "draft, check, and let a person approve before publishing"

    @classmethod
    def declare(cls) -> dict[str, nn.Knob]:
        return {
            "topic": nn.Knob(
                "a short note", "str", frozen=True,
                description="the subject to draft and review; an input, not a search parameter",
            ),
            "rounds": nn.Knob(
                3, "int", lo=1, hi=6,
                description="how many points the draft makes; more clears the check sooner",
            ),
        }

    def build(self) -> nn.Graph:
        graph = nn.Graph()
        graph.add(nn.Node(
            "task_input", "op", call=f"{OPS}:task_input",
            knobs=("topic",),
            writes=("task.json",),
            produces=("topic",),
            notes="materialize the task, so the draft has a subject",
        ))
        graph.add(nn.Node(
            "draft", "op", call=f"{OPS}:draft",
            reads=("task.json",),
            writes=("draft.md",),
            produces=("draft_words",),
            knobs=("rounds",),
            notes="write one draft",
        ))
        graph.add(nn.Node(
            "check", "gate", evaluator=f"{OPS}:check",
            reads=("draft.md",),
            max_iterations=3,
            notes="reloop while the draft is too thin to be worth a person's time",
        ))
        graph.add(nn.Node(
            "approve", "gate", gate_kind="user",
            reads=("draft.md",),
            notes="the person approves the draft, or halts the run",
        ))
        graph.add(nn.Node(
            "publish", "op", call=f"{OPS}:publish",
            reads=("draft.md",),
            writes=("final.md",),
            produces=("published_chars",),
            notes="publish the draft the person approved",
        ))
        graph.add(nn.Node(
            "reject", "op", call=f"{OPS}:reject",
            reads=("draft.md",),
            writes=("rejected.md",),
            produces=("rejected",),
            notes="record that the person did not approve",
        ))
        graph.connect("task_input", "draft")
        graph.connect("draft", "check")
        graph.connect("check", "draft", condition="reloop")
        graph.connect("check", "approve", condition="proceed")
        graph.connect("approve", "publish", condition="proceed")
        graph.connect("approve", "reject", condition="halt")
        return graph

    def score(self, run) -> float:
        """What was published. A rejected draft publishes nothing and scores 0."""
        return float(run.facts.get("published_chars", 0.0))
