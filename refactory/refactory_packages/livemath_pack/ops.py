"""Operations for the LiveMath harness: the deterministic half of one pass."""

from __future__ import annotations

from typing import Any

from .tasks import RESPONSE


def skill_section(skill: str) -> str:
    """The `## Skill` block SkillOpt's system prompt carries, or nothing for no skill."""
    return f"## Skill\n{skill.strip()}\n\n" if skill.strip() else ""


def user_turn(item: dict[str, Any]) -> str:
    """The question as SkillOpt's Claude backend sends it, character for character.

    SkillOpt builds `## Question` and `## Choices`, and its `claude -p` backend wraps
    the user message as a one-line history (continuation lines indented two spaces)
    followed by "Answer the latest user request."
    """
    choices = "\n".join(f"{c['label']}. {c['text']}" for c in item.get("choices") or [])
    body = f"## Question\n{item.get('question', '')}\n\n## Choices\n{choices}".strip()
    return "History:\n- User: " + body.replace("\n", "\n  ") + "\n\nAnswer the latest user request."


def read_item(ctx: Any) -> dict[str, Any]:
    """Publish the current item and the skill as the facts the solve prompt renders."""
    item = ctx.read_json("current_item.json", {}) or {}
    return {
        "item_id": str(item.get("id") or ""),
        "skill_section": skill_section(str(ctx.knob("skill", "") or "")),
        "user_turn": user_turn(item),
    }


def record_response(ctx: Any) -> dict[str, Any]:
    """Write the model's final response where the Task grades it."""
    response = str(ctx.facts.get("response") or "")
    ctx.write_text(RESPONSE, response)
    return {"responded": bool(response.strip())}
