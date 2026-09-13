"""The baselines: a random edit, and a model that reads the gradient.

Both implement the same contract, ``propose`` returning typed edits, so the
engine and the validating loop are identical for either. The difference is only
where the ideas come from, which is what makes the two comparable.
"""

from __future__ import annotations

import json
import random
import re
from typing import Any, Sequence

from refactory_dsh.nn import edits as nn_edits

from .base import Candidate, Optimizer, Proposal, ProposalContext


class RandomOptimizer(Optimizer):
    """Sample edits uniformly from the enabled catalogue.

    Structural edits need blocks to insert, so a mode that declares none is only
    reachable through its parameters. The sampler says so by returning fewer
    proposals rather than by inventing an operation.
    """

    name = "random"

    def __init__(self, *, k: int = 3, seed: int | None = None) -> None:
        super().__init__(k=k)
        self._rng = random.Random(seed)

    def propose(
        self, ctx: ProposalContext, n: int, parents: Sequence[Candidate]
    ) -> list[Proposal]:
        if not parents:
            return []
        proposals: list[Proposal] = []
        for index in range(max(1, n)):
            parent = parents[index % len(parents)]
            edit = nn_edits.random_edit(
                parent.state,
                preset=ctx.preset,
                insertable=ctx.insertable,
                rng=self._rng,
            )
            if edit is None:
                continue
            proposals.append(
                Proposal(edit=edit, parent=parent.id, rationale="sampled at random")
            )
        return proposals


class AutoresearchOptimizer(Optimizer):
    """Ask a model to read the traces and propose the next update.

    The prompt carries the write surface, the edit catalogue, the insertable
    blocks, the leaderboard and the evidence, and — on a retry — the reasons the
    previous attempt was refused. Telling the model exactly what was wrong is what
    makes the loop converge in one or two attempts instead of five.
    """

    name = "autoresearch"

    def __init__(self, client: Any, *, k: int = 3, system: str = "") -> None:
        super().__init__(k=k)
        if client is None:
            raise ValueError("the autoresearch optimizer needs a model client")
        self._client = client
        self._system = system or (
            "You are the optimizer of a search over workflows. You reply with JSON only."
        )

    def propose(
        self, ctx: ProposalContext, n: int, parents: Sequence[Candidate]
    ) -> list[Proposal]:
        question = (
            f"{ctx.render()}\n\nPropose at most {max(1, n)} update(s). "
            "Reply with the JSON object described under 'Answer' and nothing else."
        )
        text = self._client.complete(question, system=self._system)
        payload = extract_json(text)
        self.last_diagnosis = str(payload.get("diagnosis", "") or "")
        parent_ids = {parent.id for parent in parents}

        proposals: list[Proposal] = []
        for raw in payload.get("proposals") or ():
            if not isinstance(raw, dict):
                continue
            edit = raw.get("edit") or raw.get("edit_json")
            if isinstance(edit, str):
                try:
                    edit = json.loads(edit)
                except json.JSONDecodeError:
                    continue
            if not isinstance(edit, dict) or "op" not in edit:
                continue
            parent = raw.get("parent")
            proposals.append(
                Proposal(
                    edit=edit,
                    rationale=str(raw.get("rationale", "") or ""),
                    prediction=str(raw.get("prediction", "") or ""),
                    confidence=_confidence(raw.get("confidence")),
                    parent=parent if parent in parent_ids else None,
                )
            )
        return proposals[: max(1, n)]


def _confidence(value: Any) -> float:
    try:
        return min(1.0, max(0.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def extract_json(text: str) -> dict[str, Any]:
    """The first JSON object in ``text``, fenced or bare.

    A model's answer is prose with an object in it often enough that refusing
    anything but a bare object would waste most of the tokens it just spent.
    """
    if not isinstance(text, str) or not text.strip():
        raise ValueError("the model returned nothing")
    fenced = re.findall(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    for chunk in [*fenced, text]:
        found = _first_object(chunk)
        if found is not None:
            return found
    raise ValueError(f"no JSON object in the model's answer: {text[:400]!r}")


def _first_object(text: str) -> dict[str, Any] | None:
    start = text.find("{")
    while start != -1:
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(text)):
            char = text[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    try:
                        loaded = json.loads(text[start : index + 1])
                    except json.JSONDecodeError:
                        break
                    if isinstance(loaded, dict):
                        return loaded
                    break
        start = text.find("{", start + 1)
    return None
