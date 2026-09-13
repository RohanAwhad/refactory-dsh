"""Optimizers propose updates to a mode's state.

An optimizer is one method — ``propose`` — plus a loop the base class owns. The
loop is where the evaluation budget is protected: a proposal is expanded and
validated for microseconds before it can cost an inner run, and a proposal that
does not survive is regenerated with the machine-readable reason why. That turns
wasted *budget* into wasted *tokens*, which is the trade worth making, because an
invalid candidate cannot run and therefore teaches the search nothing.

The loop regenerates; it never repairs. An edit that was fixed up before running
would be scored as something the optimizer never proposed, and the gradient would
be a description of the repair rather than of the idea.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from typing import Any, Iterable, Sequence

from refactory_dsh.nn import edits as nn_edits
from refactory_dsh.nn import validate_state
from refactory_dsh.nn.validate import Invariant


def candidate_id(state: dict[str, Any]) -> str:
    """A stable id for a state, so two candidates that are the same state agree.

    Derived from the canonical state rather than from the edit that produced it:
    the same architecture reached two ways is the same candidate, and evaluating
    it twice is exactly the waste this loop exists to avoid.
    """
    payload = json.dumps(
        {"graph": state.get("graph"), "knobs": state.get("knobs")}, sort_keys=True, default=str
    )
    return hashlib.sha1(payload.encode()).hexdigest()[:12]


@dataclass(frozen=True)
class Proposal:
    """One proposed update, as typed data."""

    edit: dict[str, Any]
    rationale: str = ""
    prediction: str = ""
    confidence: float = 0.0
    parent: str | None = None

    def summary(self) -> str:
        return f"{self.edit.get('op')}: {self.rationale or self.prediction}".strip()


@dataclass(frozen=True)
class Rejection:
    """A proposal that did not become a candidate, and why.

    The reasons are the feedback the loop hands back, and their distribution is
    the gradient on the edit catalogue itself: one reason dominating means a rule
    the optimizer cannot see.
    """

    attempt: int
    edit: dict[str, Any]
    reasons: list[str]
    rationale: str = ""

    def codes(self) -> list[str]:
        """The rules that were broken, which is how a refusal histogram is built."""
        return sorted(
            {reason.split(" ", 1)[0].rstrip(":") for reason in self.reasons if reason}
        )


@dataclass
class Candidate:
    """One point in the search: a state, where it came from, and how it scored."""

    id: str
    parent: str | None
    state: dict[str, Any]
    edits: list[dict[str, Any]] = field(default_factory=list)
    score: float | None = None
    mean_score: float | None = None
    facts: dict[str, Any] = field(default_factory=dict)
    exit_ok: bool = True
    error: str | None = None
    duration_s: float = 0.0
    evaluated: bool = False

    @classmethod
    def from_state(
        cls,
        state: dict[str, Any],
        *,
        parent: str | None = None,
        edits: Iterable[dict[str, Any]] = (),
    ) -> "Candidate":
        return cls(id=candidate_id(state), parent=parent, state=state, edits=list(edits))

    def is_structural(self) -> bool:
        return any(nn_edits.is_macro(edit.get("op", "")) or edit.get("op") in (
            "add_node", "remove_node", "replace_node", "add_edge", "remove_edge",
            "set_node_field",
        ) for edit in self.edits)

    def describe(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "parent": self.parent,
            "score": self.score,
            "edits": self.edits,
            "structural": self.is_structural(),
            "exit_ok": self.exit_ok,
            "error": self.error,
            "duration_s": self.duration_s,
        }


@dataclass
class ProposalContext:
    """The gradient: everything the optimizer is allowed to know.

    It carries the surface it may write to, the catalogue of edits it may use, the
    blocks it may insert, the run's numbers, and the reasons recent proposals were
    refused. Nothing here reaches ``score()`` or ``invariants()``.
    """

    epoch: int = 0
    generation: int = 0
    mode_name: str = ""
    best_score: float | None = None
    mean_score: float | None = None
    knobs: list[dict[str, Any]] = field(default_factory=list)
    nodes: list[dict[str, Any]] = field(default_factory=list)
    edges: list[dict[str, Any]] = field(default_factory=list)
    leaderboard: list[dict[str, Any]] = field(default_factory=list)
    traces: list[dict[str, Any]] = field(default_factory=list)
    catalogue: list[dict[str, Any]] = field(default_factory=list)
    insertable: list[dict[str, Any]] = field(default_factory=list)
    rejections: list[dict[str, Any]] = field(default_factory=list)
    #: Knobs the operator has frozen for this run, on top of the ones the mode
    #: itself declares frozen. Freezing is a property of the run, not of the mode,
    #: so it cannot be edited into the definition by a candidate.
    frozen: list[str] = field(default_factory=list)
    preset: str = "broad"

    def render(self) -> str:
        """The request, as text, for a model optimizer."""
        sections = [
            f"# Update request — generation {self.generation}, epoch {self.epoch}",
            "",
            f"Mode: `{self.mode_name}`. Best score so far: {self.best_score}. "
            f"Batch mean: {self.mean_score}.",
            "",
            "## Parameters you may move",
            _table(["name", "value", "kind", "bounds", "frozen", "description"],
                   [[k["name"], k["value"], k["kind"], k["bounds"], k["frozen"],
                     k.get("description", "")] for k in self.knobs]),
            "",
            "## The pipeline you may edit",
            "Nodes:",
            _table(["id", "kind", "call", "knobs", "notes"],
                   [[n["id"], n["kind"], n.get("call", ""), n.get("knobs", []),
                     n.get("notes", "")] for n in self.nodes]),
            "Edges:",
            _table(["source", "target", "condition"],
                   [[e["source"], e["target"], e.get("condition")] for e in self.edges]),
            "",
            "## The operations you may use",
            _table(["op", "kind", "macro", "fields", "description"],
                   [[c["op"], c["kind"], c["macro"], c["fields"], c["description"]]
                    for c in self.catalogue]),
            "",
            "## Blocks you may insert",
            "Use these node objects verbatim in an `insert_after`/`insert_before`/"
            "`wrap`/`split` edit, changing nothing but their `id`:",
            "```json",
            json.dumps(self.insertable, indent=2, default=str),
            "```",
            "",
            "## Recent results",
            _table(["id", "parent", "score", "structural", "edits"],
                   [[c["id"], c.get("parent"), c.get("score"), c.get("structural"),
                     json.dumps(c.get("edits"), default=str)] for c in self.leaderboard]),
            "",
            "## Evidence from the traces",
            _table(["what", "value"],
                   [[str(k), json.dumps(v, default=str)] for k, v in _flatten(self.traces)]),
        ]
        if self.rejections:
            sections += [
                "",
                "## Why your last proposals were refused",
                "A proposal that breaks one of these is not evaluated at all. It costs "
                "nothing but the answer, so fix the stated reason rather than guessing.",
                _table(["reasons"], [[r] for r in self.rejections[-12:]]),
            ]
        sections += ["", "## Answer", ANSWER_INSTRUCTIONS]
        return "\n".join(sections)


ANSWER_INSTRUCTIONS = """\
Write one JSON object and nothing else:

{"diagnosis": "<what the evidence shows is limiting the score>",
 "proposals": [{"edit": {"op": "<operation>", ...},
                "rationale": "<why, citing the evidence>",
                "prediction": "<expected effect on the score>",
                "confidence": 0.0}]}

Rules:
- A parameter move is {"op": "set_knob", "name": "<parameter>", "value": <value>}.
- A structural move is one of the operations above, using the fields it lists and
  the block objects verbatim.
- Few well-argued changes, not many guesses. Prefer an edit whose effect you can
  state, and vary what you touch.
- Your prediction is recorded and later checked against the outcome, so say what
  you expect and how you could be wrong.
- If the evidence justifies no change, return an empty proposal list and say why.
"""


def _flatten(rows: Sequence[dict[str, Any]]) -> list[tuple[str, Any]]:
    out: list[tuple[str, Any]] = []
    for row in rows:
        for key, value in row.items():
            out.append((key, value))
    return out


def _table(headers: list[str], rows: list[list[Any]]) -> str:
    if not rows:
        return "(none)"
    head = "| " + " | ".join(headers) + " |"
    rule = "|" + "|".join("---" for _ in headers) + "|"
    body = [
        "| " + " | ".join(_cell(value) for value in row) + " |" for row in rows
    ]
    return "\n".join([head, rule, *body])


def _cell(value: Any) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    return text.replace("|", "\\|").replace("\n", " ")


class Optimizer:
    """Base class: one ``propose``, and the validating loop around it."""

    name: str = "optimizer"

    def __init__(self, *, k: int = 3) -> None:
        #: How many times the loop may ask again after a refusal.
        self.k = max(1, int(k))
        self.rejections: list[Rejection] = []
        self.last_diagnosis: str = ""

    # ── the part a subclass writes ─────────────────────────────────────────
    def propose(
        self, ctx: ProposalContext, n: int, parents: Sequence["Candidate"]
    ) -> list[Proposal]:
        """One round of proposals. Unvalidated and possibly unusable.

        ``parents`` are the candidates the edits are made against, so a proposal
        can name the one it belongs to and the caller can apply it to that state.
        """
        raise NotImplementedError

    # ── the loop ───────────────────────────────────────────────────────────
    def propose_valid(
        self,
        ctx: ProposalContext,
        n: int,
        *,
        parents: Sequence[Candidate],
        invariants: tuple[Invariant, ...] = (),
        preset: str = "broad",
        resolve_calls: bool = True,
    ) -> tuple[list[Candidate], list[Rejection]]:
        """Propose until ``n`` valid candidates exist, or the attempts run out.

        Returns the offspring and every refusal. A refusal is never silent: it is
        recorded here, fed back into the next attempt, and reported at the end.
        """
        self.rejections = []
        accepted: list[Candidate] = []
        feedback: list[str] = []
        if not parents:
            return accepted, self.rejections

        for attempt in range(1, self.k + 1):
            if len(accepted) >= n:
                break
            round_ctx = replace(
                ctx, rejections=[*ctx.rejections, *feedback[-12:]], preset=preset
            )
            try:
                proposals = self.propose(round_ctx, n - len(accepted), parents) or []
            except Exception as exc:  # a failed optimizer round is not a crash
                feedback = [f"{self.name} could not propose: {type(exc).__name__}: {exc}"]
                continue
            if not proposals:
                if feedback:
                    continue
                break

            progressed = False
            for proposal in proposals:
                if len(accepted) >= n:
                    break
                parent = self._pick_parent(proposal, parents)
                outcome = self.attempt(
                    proposal, parent, invariants, resolve_calls, ctx.frozen
                )
                if isinstance(outcome, Candidate):
                    accepted.append(outcome)
                    progressed = True
                else:
                    rejection = Rejection(
                        attempt=attempt,
                        edit=proposal.edit,
                        reasons=outcome,
                        rationale=proposal.rationale,
                    )
                    self.rejections.append(rejection)
                    feedback.extend(outcome)
            if not progressed and not feedback:
                break
        return accepted, self.rejections

    def _pick_parent(
        self, proposal: Proposal, parents: Sequence[Candidate]
    ) -> Candidate:
        if proposal.parent:
            for parent in parents:
                if parent.id == proposal.parent:
                    return parent
        return parents[0]

    def attempt(
        self,
        proposal: Proposal,
        parent: Candidate,
        invariants: tuple[Invariant, ...],
        resolve_calls: bool,
        frozen: Sequence[str] | None = None,
    ) -> Candidate | list[str]:
        """Apply and validate one proposal: a candidate, or the reasons it failed.

        Public because seeding a population and proposing an update have to be the
        same test: a candidate that could not be proposed could not be seeded
        either, and two code paths would eventually disagree about that.
        """
        frozen = set(frozen or ())
        if proposal.edit.get("op") == "set_knob" and proposal.edit.get("name") in frozen:
            return [
                f"E-frozen: knob {proposal.edit.get('name')!r} is frozen for this run"
            ]
        reason = nn_edits.precheck(proposal.edit, parent.state)
        if reason:
            return [f"E-precondition: {reason}"]
        try:
            state = nn_edits.apply(proposal.edit, parent.state)
        except nn_edits.EditError as exc:
            return [f"{exc.code}: {exc}"]
        except Exception as exc:  # an operator bug is reported, not swallowed
            return [f"E-internal: {type(exc).__name__}: {exc}"]

        issues = validate_state(state, invariants=invariants, resolve_calls=resolve_calls)
        if issues:
            return [str(issue) for issue in issues]
        return Candidate.from_state(
            state,
            parent=parent.id,
            edits=[*parent.edits, proposal.edit],
        )
