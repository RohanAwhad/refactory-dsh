"""The LiveMathematicianBench Task: 177 multiple-choice items and how one is graded.

`instances()` lists every item of the manifest's official split, train first, then
validation, then test, each carrying its `part`. Metadata is the question and the
shuffled choices only. The correct choice never enters metadata, the workspace or a
fact; `verify` looks it up by id, which is what keeps a pass from reading its own
answer (see docs/benchmark-cheating-prevention.md).
"""

from __future__ import annotations

from pathlib import Path

from refactory_dsh.task import Task, TaskInstance, VerifyResult

from . import data, scoring

#: The file a pass leaves its model's final response in, for `verify` to grade.
RESPONSE = "response.md"


class LiveMathTask(Task):
    """LiveMathematicianBench items, graded by SkillOpt's LiveMath scorer."""

    name = "livemath"

    def instances(self) -> list[TaskInstance]:
        return [
            TaskInstance(
                id=item["id"],
                metadata={
                    "part": item["part"],
                    "question": item["question"],
                    "choices": [dict(choice) for choice in item["choices"]],
                },
            )
            for item in data.items()
        ]

    def setup(self, instance: TaskInstance, workspace: Path) -> None:
        """Remove the previous item's response, so a pass that writes none scores 0."""
        (Path(workspace) / RESPONSE).unlink(missing_ok=True)

    def verify(self, instance: TaskInstance, workspace: Path) -> VerifyResult:
        """1 when the response's answer is the correct label, else 0, with what was read."""
        key = data.answer_key().get(instance.id)
        if key is None:
            return VerifyResult(passed=False, score=0.0,
                                details=f"{instance.id} is not a LiveMath item")
        path = Path(workspace) / RESPONSE
        response = path.read_text(encoding="utf-8") if path.exists() else ""
        graded = scoring.evaluate(response, key["correct_choice"], key["choices"])
        passed = bool(graded["em"])
        if not response.strip():
            details = "no response"
        else:
            details = (f"predicted {graded['predicted_label'] or '-'}, "
                       f"correct {graded['correct_label']}")
        return VerifyResult(passed=passed, score=1.0 if passed else 0.0, details=details)
