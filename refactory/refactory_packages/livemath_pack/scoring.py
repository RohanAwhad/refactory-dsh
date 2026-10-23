"""LiveMath answer extraction and scoring, ported unchanged from SkillOpt.

Source: microsoft/SkillOpt `skillopt/envs/livemathematicianbench/evaluator.py` at
1a44050 (MIT). Kept verbatim in behaviour so a score here and a score there are the
same function of the same response: the label inside the last `<answer>` tag, else the
last non-empty line; then a choice label, a choice's exact text, or the first token.
"""

from __future__ import annotations

import re
from typing import Any


def extract_answer(text: str) -> str:
    matches = re.findall(r"<answer>(.*?)</answer>", text, re.DOTALL | re.IGNORECASE)
    if matches:
        return matches[-1].strip()
    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    if lines:
        return lines[-1]
    return text.strip()


def normalize_label(text: Any) -> str:
    return str(text).strip().upper().rstrip(".):")


def parse_choice_label(prediction_text: str, choices: list[dict[str, Any]]) -> str:
    answer = extract_answer(prediction_text)
    label = normalize_label(answer)
    valid = {normalize_label(choice.get("label", "")) for choice in choices}
    if label in valid:
        return label
    lowered = answer.lower()
    for choice in choices:
        text = str(choice.get("text", "")).strip()
        if text and text.lower() == lowered:
            return normalize_label(choice.get("label", ""))
    first = normalize_label(answer.split()[0]) if answer.split() else ""
    if first in valid:
        return first
    return label


def evaluate(prediction_text: str, correct_choice: dict[str, Any],
             choices: list[dict[str, Any]]) -> dict[str, Any]:
    predicted = parse_choice_label(prediction_text, choices)
    correct = normalize_label(correct_choice.get("label", ""))
    predicted_text = ""
    for choice in choices:
        if normalize_label(choice.get("label", "")) == predicted:
            predicted_text = str(choice.get("text", "")).strip()
            break
    is_correct = float(predicted == correct)
    return {
        "em": is_correct,
        "predicted_label": predicted,
        "predicted_text": predicted_text,
        "correct_label": correct,
        "correct_text": str(correct_choice.get("text", "")).strip(),
    }
