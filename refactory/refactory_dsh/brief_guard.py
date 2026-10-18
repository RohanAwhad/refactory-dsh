"""Refuse an empty or whitespace-only brief, and check required knobs.

One module, two functions:

- ``require_brief`` is the CLI guard: it checks the positional ``what`` before
  anything is forwarded.
- ``check_required_knobs`` is the run-start and V9 guard: it checks every
  ``required=True`` str knob and returns the violations.  Used by ``Mode.run``
  (the walker path), ``interactive.prepare`` (the DSH path) and V9 validation
  (the optimizer/sandbox/registry path).

A separate module because the CLI cannot import the prepare ops, the prepare ops
cannot import the CLI, and ``nn/`` should not import either.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Mapping

if TYPE_CHECKING:
    from refactory_dsh.nn.knob import Knob


def require_brief(brief: str | None, *, source: str = "brief") -> None:
    """Raise :class:`ValueError` when *brief* is missing, empty or whitespace-only.

    ``source`` names the argument or knob that was empty (e.g. ``'what'`` from the
    CLI positional, ``'brief'`` from knobs or the task dict).
    """
    if brief is None or not str(brief).strip():
        raise ValueError(
            f"refactory: {source} is empty or whitespace-only; "
            "every authoring mode needs an idea to work from"
        )


def check_required_knobs(
    declared: Mapping[str, "Knob"],
    values: Mapping[str, Any],
    *,
    allow_defaults: bool = False,
) -> list[tuple[str, str]]:
    """Return ``(name, message)`` for every required knob whose value is empty.

    With ``allow_defaults``, a value equal to the knob's default is silently
    accepted.  V9 uses this so that a mode's starting state (whose required knobs
    are placeholders) passes structural validation; the run-start check does not.
    """
    violations: list[tuple[str, str]] = []
    for name, knob in declared.items():
        if not getattr(knob, "required", False) or knob.kind != "str":
            continue
        value = values.get(name, knob.default)
        if allow_defaults and value == knob.default:
            continue
        if not str(value).strip():
            violations.append(
                (name,
                 f"required knob {name!r} is empty or whitespace-only; "
                 "it must have a non-empty value")
            )
    return violations
