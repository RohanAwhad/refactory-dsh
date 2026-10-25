"""Judge nodes: a model call that writes a structured verdict, and the reader its gate uses.

A judge reads prose -- the reviews, the evidence map, the correction audit -- and
answers in JSON. The gate after it reads that JSON with ``json.loads`` and never scans
prose for markers, so a review that quotes a finding's format, or a line such as
"-> 1 finding", cannot decide anything. The gate is an ordinary Python node: the walker
calls it in-process and the spine runs it as ``python -m refactory_dsh.run``, so both
runtimes take the same decision from the same file, and a scripted model drives it in
tests.

Design mode has two judges (``refactory_dsh.design``): ``qa_judge`` after the review
fork, and ``overwatch`` before delivery. Their gates are ``design_ops.gate_qa`` and
``design_ops.gate_overwatch``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

#: What the QA judge writes, and the verdicts it may give.
QA_JUDGE = "qa-judge.json"
QA_VERDICTS = ("proceed", "redo")

#: What the overwatch writes in design mode, and the verdicts it may give.
OVERWATCH_VERDICT = "overwatch-verdict.json"
OVERWATCH_VERDICTS = ("pass", "redo")

#: How a judge says a tool or credential it needs is missing: the run stops for a
#: person instead of sending the builder a defect it cannot fix.
UNAVAILABLE = "UNAVAILABLE:"

#: A file that is one fenced block and nothing else: the one wrapper a model commonly
#: puts around JSON. Anything else around the JSON makes the file unreadable.
_FENCED = re.compile(r"\A```[A-Za-z]*[ \t]*\n(.*)\n```\Z", re.S)


class Unreadable(ValueError):
    """The judge's file is missing, is not JSON, or is not a verdict."""


@dataclass(frozen=True)
class Judgment:
    """A judge's verdict, as its gate reads it."""

    verdict: str
    #: Each defect the builder must fix: ``source``, ``where``, ``what``, ``why``.
    must_fix: tuple[dict[str, str], ...] = ()
    follow_ups: tuple[str, ...] = ()
    #: Findings the judge set aside: ``source``, ``finding``, ``why``.
    dismissed: tuple[dict[str, str], ...] = ()
    #: The overwatch's account of each gate correction: ``id``, ``met``, ``evidence``.
    corrections: tuple[dict[str, Any], ...] = field(default=())

    @property
    def unmet(self) -> list[dict[str, Any]]:
        """The corrections the judge says are not met."""
        return [item for item in self.corrections if item.get("met") is not True]


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if value is None:
        return ""
    return json.dumps(value, sort_keys=True)


def _record(value: Any, keys: tuple[str, ...], main: str) -> dict[str, str]:
    """One list entry as a dict of strings; a bare string is its ``main`` field."""
    if isinstance(value, dict):
        return {key: _text(value.get(key)) for key in keys}
    return {key: (_text(value) if key == main else "") for key in keys}


def _follow_up(value: Any) -> str:
    if isinstance(value, dict):
        parts = [_text(value.get(key)) for key in ("what", "where", "why", "finding")]
        return " — ".join(part for part in parts if part) or _text(value)
    return _text(value)


def _met(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in ("true", "yes", "met"):
        return True
    if isinstance(value, str) and value.strip().lower() in ("false", "no", "unmet", "not met"):
        return False
    return None


def parse(text: str, verdicts: tuple[str, ...], *, name: str = "the judge's file") -> Judgment:
    """Read a judge's JSON verdict, or raise :class:`Unreadable` saying why.

    The verdict decides. A ``redo`` must name what to fix (a ``must_fix`` item, or for
    the overwatch an unmet correction): a redo with nothing in it gives the builder
    nothing to do, so it is unreadable rather than a round spent on nothing.
    """
    raw = text.strip()
    fenced = _FENCED.match(raw)
    if fenced:
        raw = fenced[1].strip()
    if not raw:
        raise Unreadable(f"{name} is empty")
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise Unreadable(f"{name} is not JSON ({exc})") from None
    if not isinstance(data, dict):
        raise Unreadable(f"{name} is not a JSON object")
    verdict = data.get("verdict")
    if not isinstance(verdict, str) or verdict.strip().lower() not in verdicts:
        raise Unreadable(f"{name} has verdict {verdict!r}; expected one of {', '.join(verdicts)}")
    lists = {}
    for key in ("must_fix", "follow_ups", "dismissed", "corrections"):
        value = data.get(key, [])
        if value is None:
            value = []
        if not isinstance(value, list):
            raise Unreadable(f"{name}: {key} is not a list")
        lists[key] = value
    corrections = []
    for item in lists["corrections"]:
        if not isinstance(item, dict):
            raise Unreadable(f"{name}: each corrections entry must be an object")
        corrections.append({"id": _text(item.get("id")), "met": _met(item.get("met")),
                            "evidence": _text(item.get("evidence"))})
    judgment = Judgment(
        verdict=verdict.strip().lower(),
        must_fix=tuple(_record(item, ("source", "where", "what", "why"), "what")
                       for item in lists["must_fix"]),
        follow_ups=tuple(text for text in (_follow_up(item) for item in lists["follow_ups"]) if text),
        dismissed=tuple(_record(item, ("source", "finding", "why"), "finding")
                        for item in lists["dismissed"]),
        corrections=tuple(corrections),
    )
    if judgment.verdict == "redo" and not judgment.must_fix and not judgment.unmet:
        raise Unreadable(f"{name} says redo but names nothing to fix")
    return judgment


def read(ctx: Any, name: str, verdicts: tuple[str, ...]) -> Judgment:
    """The judgment in the run directory's ``name``; :class:`Unreadable` when there is none."""
    if not ctx.exists(name):
        raise Unreadable(f"{name} was not written")
    try:
        text = ctx.read_text(name)
    except (OSError, UnicodeDecodeError) as exc:
        raise Unreadable(f"{name} could not be read ({exc})") from None
    return parse(text, verdicts, name=name)


def excerpt(ctx: Any, name: str, limit: int = 300) -> str:
    """The start of a judge's file on one line, for a note that says it was unreadable."""
    try:
        text = " ".join(ctx.read_text(name).split()) if ctx.exists(name) else ""
    except (OSError, UnicodeDecodeError):
        return ""
    return text if len(text) <= limit else text[:limit] + "…"


def unavailable(judgment: Judgment) -> str:
    """The first must-fix item saying a dependency is unavailable, or ``""``."""
    for item in judgment.must_fix:
        if item.get("what", "").startswith(UNAVAILABLE):
            return item["what"]
    return ""


def must_fix_line(item: dict[str, str]) -> str:
    """One must-fix item as a line of feedback: where, what, and why it matters."""
    head = f"[{item['source']}] " if item.get("source") else ""
    where = f"{item['where']}: " if item.get("where") else ""
    why = f" (why: {item['why']})" if item.get("why") else ""
    return f"{head}{where}{item.get('what', '')}{why}".strip()


def correction_line(item: dict[str, Any]) -> str:
    """One correction as the overwatch judged it."""
    met = {True: "met", False: "NOT met", None: "not judged"}[item.get("met")]
    evidence = f" — {item['evidence']}" if item.get("evidence") else ""
    return f"{item.get('id') or '(no id)'}: {met}{evidence}"


def feedback(judgment: Judgment, judge: str) -> str:
    """What the builder is told when ``judge`` sends the change back.

    The must-fix items, each with where and why; the corrections the judge found unmet;
    then the follow-ups, which the builder may fix if it is cheap and the pull request
    lists otherwise.
    """
    lines = [f"The {judge} sent the change back. Fix each of these:"]
    lines += [f"- {must_fix_line(item)}" for item in judgment.must_fix]
    lines += [f"- correction {correction_line(item)}" for item in judgment.unmet]
    if judgment.follow_ups:
        lines.append("Follow-ups (fix them only if it is cheap; otherwise the pull request "
                     "lists them):")
        lines += [f"- {item}" for item in judgment.follow_ups]
    return "\n".join(lines)
