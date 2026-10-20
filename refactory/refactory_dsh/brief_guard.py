"""Refuse an empty or whitespace-only brief, and re-export check_required_knobs.

- ``require_brief`` is the CLI guard: it checks the positional ``what`` before
  anything is forwarded.
- ``check_required_knobs`` is defined in ``nn/knob.py`` beside ``Knob`` and
  re-exported here for callers that import it from ``brief_guard``.
"""

from __future__ import annotations


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


from refactory_dsh.nn.knob import check_required_knobs  # moved to nn/knob.py; re-exported for callers  # noqa: E402
