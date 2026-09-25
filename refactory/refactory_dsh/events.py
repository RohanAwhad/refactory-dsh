"""What a run says while it runs, in one vocabulary.

A run writes a durable record (``progress.jsonl``) and a person watching needs a
line now and then, not that file at the end. Both come from the same record: this
module owns the event names, which of them a person wants by default, and how one
is rendered. Nothing else invents a progress format, so the terminal, the web
panel and the file cannot disagree about what happened.

Two levels, and the difference is deliberate:

- **meaningful** — the events a person watching a search wants: the run's shape,
  each candidate's score, what the optimizer kept and refused, each epoch's best,
  the final report and the held-out audit. Printed by default.
- **verbose** — everything else a run records, rendered as the raw event. Printed
  only when a caller asks, because a stream nobody can read is not streaming.
"""

from __future__ import annotations

import json
import sys
from typing import Any, Callable, TextIO

#: The events a watcher gets by default. Everything else is verbose-only.
MEANINGFUL = frozenset({
    "init",
    "forward",
    "optimizer",
    "scheduler",
    "report",
    "holdout",
})

#: Record keys the printer owns; a verbose line shows the event's own fields only.
_ENVELOPE = ("step", "epoch", "generation", "evaluations", "time")


def _score(value: Any) -> str:
    """A score as a person reads it, or `-` when the candidate was not scored."""
    if isinstance(value, bool) or value is None:
        return "-"
    if isinstance(value, (int, float)):
        return f"{float(value):.3f}"
    return str(value)


def render(record: dict[str, Any], *, verbose: bool = False) -> str | None:
    """One line for one event, or ``None`` when the default level hides it.

    ``verbose`` renders every event: the meaningful ones as they always read, the
    rest as the event's own fields, so a caller debugging a run sees what was
    recorded rather than a second, lossier format.
    """
    step = str(record.get("step", ""))
    epoch = record.get("epoch", 0)
    if step == "init":
        return (
            f"run · optimizer {record.get('optimizer', '?')} · "
            f"{record.get('epochs', '?')} epoch(s) x {record.get('batch_size', '?')} · "
            f"population {record.get('population', '?')}"
            + (" · resumed" if record.get("resumed") else "")
        )
    if step == "forward":
        where = f"{record.get('index', '?')}/{record.get('of', '?')}"
        line = (
            f"epoch {epoch} · candidate {where} {record.get('candidate', '?')} · "
            f"score {_score(record.get('score'))}"
        )
        if record.get("exit_ok") is False:
            detail = str(record.get("error") or "").strip()
            return line + " · failed" + (f": {detail[:160]}" if detail else "")
        if record.get("duration_s") is not None:
            line += f" · {float(record['duration_s']):.1f}s"
        return line
    if step == "optimizer":
        line = (
            f"epoch {epoch} · proposals {record.get('proposed', 0)} · "
            f"accepted {record.get('accepted', 0)} · refused {record.get('refused', 0)}"
        )
        codes = record.get("refusal_codes")
        if isinstance(codes, dict) and codes:
            shown = ", ".join(f"{code} {count}" for code, count in sorted(codes.items()))
            line += f" ({shown})"
        return line
    if step == "scheduler":
        return (
            f"epoch {record.get('epoch', epoch)} done · best {_score(record.get('best'))} · "
            f"mean {_score(record.get('mean'))} · improved {record.get('improved', '?')} · "
            f"bound {record.get('bound') or 'no'}"
        )
    if step == "report":
        return (
            f"done · best {_score(record.get('best_score'))} "
            f"(raw {_score(record.get('raw_best_score'))}) · "
            f"{record.get('evaluations', '?')} evaluations · bound {record.get('bound') or 'no'}"
        )
    if step == "holdout":
        return (
            f"holdout · train {_score(record.get('train'))} · "
            f"holdout {_score(record.get('holdout'))} · gap {_score(record.get('gap'))} · "
            f"overfit {record.get('overfit', '?')}"
        )
    if not verbose:
        return None
    fields = {key: value for key, value in record.items() if key not in _ENVELOPE}
    detail = json.dumps(fields, default=str) if fields else ""
    return f"epoch {epoch} · {step}" + (f" · {detail}" if detail else "")


class Printer:
    """Render a run's events to a stream, at one of the two levels."""

    def __init__(self, stream: TextIO | None = None, *, verbose: bool = False) -> None:
        self.stream = stream if stream is not None else sys.stderr
        self.verbose = verbose

    def __call__(self, record: dict[str, Any]) -> None:
        """Print one event, unless the selected level hides it."""
        line = render(record, verbose=self.verbose)
        if line is not None:
            print(line, file=self.stream, flush=True)

    def output_line(self, line: str) -> None:
        """Print one line of a child's own output; only the verbose level shows it."""
        if self.verbose and line.strip():
            print(line.rstrip("\n"), file=self.stream, flush=True)


#: The shape a caller supplies when it wants events somewhere other than a stream.
Sink = Callable[[dict[str, Any]], None]
