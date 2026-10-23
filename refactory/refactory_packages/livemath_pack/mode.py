"""The LiveMath harness: one model call per question, carrying an optional skill.

It mirrors SkillOpt's LiveMath harness so the two can be compared on the same items.
The `data` node binds `LiveMathTask` and runs the pass once per item. `read_item`
turns the item into the prompt's facts. `solve` is the one model call: its prompt is
SkillOpt's LiveMath system prompt, with the `skill` knob in its `## Skill` section,
followed by the question exactly as SkillOpt's Claude backend sends it. Then
`record_response` keeps the final response for the Task, which grades it with
SkillOpt's scorer.

The `skill` knob is the surface an optimizer evolves, as SkillOpt evolves its skill
document. Its default is SkillOpt's starting skill, so a search here and a SkillOpt
run start from the same text.
"""

from __future__ import annotations

from pathlib import Path

from refactory_dsh import nn

OPS = "refactory_packages.livemath_pack.ops"
TASK = "refactory_packages.livemath_pack.tasks:LiveMathTask"

#: SkillOpt's starting skill (skillopt/envs/livemathematicianbench/skills/initial.md).
INITIAL_SKILL = (Path(__file__).resolve().parent / "skills" / "initial.md").read_text()

#: SkillOpt's LiveMath system prompt (prompts/rollout_system.md), then the user turn.
SOLVE_PROMPT = """\
You are an expert mathematical reasoning agent solving multiple-choice questions.

{{skill_section}}## Task Format
You will receive one mathematics multiple-choice question and its answer choices.
Reason carefully about quantifiers, hypotheses, extremal wording, and exact equality conditions.

## Answer Format
Think step by step, then provide your final answer inside <answer>...</answer> tags.
Inside the tags, output only the single choice label, such as A or C.

Example:
<answer>B</answer>

{{user_turn}}"""

#: SkillOpt's per-item bound: 1800 s per attempt plus its 60 s task margin.
SOLVE_TIMEOUT_S = 1860

GUIDANCE = """\
The `skill` knob is a markdown document of heuristics the solver reads before every
question, in the `## Skill` section of its system prompt. It is the lever: rewrite it to
fix the reasoning errors the failed items show, keep what works, and keep it general.
A skill that names particular questions or answers will not transfer to unseen items.
The final answer must stay a single choice label inside <answer>...</answer>."""


class SolvePack(nn.Mode):
    """Answer each LiveMathematicianBench question with one model call."""

    name = "solve"
    description = "answer LiveMathematicianBench multiple-choice questions, one model call each"

    @classmethod
    def declare(cls) -> dict[str, nn.Knob]:
        return {
            "skill": nn.Knob(
                INITIAL_SKILL, "str",
                description="markdown heuristics placed in the solver's `## Skill` section "
                            "for every question; empty means no skill",
            ),
        }

    def build(self) -> nn.Graph:
        graph = nn.Graph()
        graph.add(nn.Node(
            "items", "data", subgraph_entry="read_item", subgraph_exit="record_response",
            task_ref=TASK, writes=("current_item.json",), produces=("item_scores",),
            notes="answer each LiveMath question the Task lists",
        ))
        graph.add(nn.Node(
            "read_item", "op", call=f"{OPS}:read_item",
            reads=("current_item.json",), knobs=("skill",),
            produces=("item_id", "skill_section", "user_turn"),
            notes="publish the question and the skill for the solve prompt",
        ))
        graph.add(nn.Node(
            "solve", "agent", prompt_template=SOLVE_PROMPT, role="solver",
            produces=("response",), timeout_s=SOLVE_TIMEOUT_S,
        ))
        graph.add(nn.Node(
            "record_response", "op", call=f"{OPS}:record_response",
            writes=("response.md",), produces=("responded",),
            notes="keep the solver's response for grading",
        ))
        graph.connect("read_item", "solve")
        graph.connect("solve", "record_response")
        return graph

    def score(self, run: nn.RunResult) -> float:
        """Accuracy: the mean of the Task's per-item verdicts."""
        scores = [entry["score"] for entry in run.facts.get("item_scores", [])]
        return sum(scores) / len(scores) if scores else 0.0

    def invariants(self) -> tuple[nn.Invariant, ...]:
        return (nn.fact_produced("item_scores"),)

    def guidance(self) -> str:
        return GUIDANCE
