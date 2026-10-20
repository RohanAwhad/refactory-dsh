"""Typed parameters: the only thing an optimizer may write.

A :class:`Knob` is a declared parameter with a kind and bounds. Coercion is
strict, and that is the point: a value a knob does not accept is an error to
report, never a value to clamp. Clamping would attribute the score to a value the
optimizer did not propose.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

#: Kinds a knob may declare. ``enum`` holds its accepted values in ``enum``.
KINDS = ("int", "float", "bool", "str", "enum")

_SPEC_FIELDS = frozenset(
    {"default", "kind", "lo", "hi", "enum", "frozen", "description", "values",
     "required"}
)

#: Textual moves for an open string knob.
#:
#: A string knob is the declared seam for a prompt, a template, a strategy name.
#: Without a sampler it is movable only by a model, so a `str` knob is invisible to
#: the random optimizer and to the starting sweep -- which quietly makes "anything
#: declared is optimizable" false for exactly the knobs an author most wants to move.
STR_MUTATIONS = (
    "be concise",
    "be thorough",
    "explain each step",
    "prefer the simplest solution",
    "double-check the result",
    "consider edge cases",
    "answer with the code only",
    "work step by step",
    "state your assumptions",
    "prefer fewer moving parts",
)


class KnobError(ValueError):
    """A value does not satisfy the knob it was written to."""


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


@dataclass(frozen=True)
class Knob:
    """A declared parameter: a default, a kind, and optional bounds."""

    default: Any
    kind: str = "float"
    lo: float | None = None
    hi: float | None = None
    enum: tuple[Any, ...] = ()
    #: For a ``str`` knob: the finite set it accepts, if the author has one in mind.
    #: Empty means the knob is open, and the sampler mutates the text instead.
    values: tuple[str, ...] = ()
    frozen: bool = False
    description: str = ""
    required: bool = False

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise KnobError(f"unknown knob kind {self.kind!r}; known: {list(KINDS)}")
        if self.required and self.kind != "str":
            raise KnobError("only a 'str' knob may be required")
        if self.kind == "enum" and not self.enum:
            raise KnobError("an 'enum' knob must declare at least one accepted value")
        if self.kind != "enum" and self.enum:
            raise KnobError(f"'{self.kind}' knob must not declare enum values")
        if self.values and self.kind != "str":
            raise KnobError(
                f"only a 'str' knob may declare values; '{self.kind}' has enum or bounds"
            )
        if any(not isinstance(value, str) for value in self.values):
            raise KnobError("every value of a 'str' knob must be a string")
        if self.lo is not None and self.hi is not None and self.lo > self.hi:
            raise KnobError(f"bounds are inverted: lo={self.lo} > hi={self.hi}")
        # A bad default is an authoring error, so it surfaces when the mode is
        # imported rather than when the outer loop first moves the knob.
        self.coerce(self.default)

    def coerce(self, value: Any) -> Any:
        """Return ``value`` as this knob's kind, or raise :class:`KnobError`."""
        if self.kind == "enum":
            if isinstance(value, bool) and not any(
                isinstance(member, bool) for member in self.enum
            ):
                raise KnobError(f"expected one of {list(self.enum)}, got {value!r}")
            for member in self.enum:
                if value == member:
                    return member
            raise KnobError(f"expected one of {list(self.enum)}, got {value!r}")

        if self.kind == "bool":
            if not isinstance(value, bool):
                raise KnobError(f"expected a bool, got {value!r}")
            return value

        if self.kind == "str":
            if not isinstance(value, str):
                raise KnobError(f"expected a string, got {value!r}")
            return value

        if not _is_number(value):
            raise KnobError(f"expected a number for kind '{self.kind}', got {value!r}")
        if self.kind == "int":
            if isinstance(value, float) and not value.is_integer():
                raise KnobError(f"expected an integer, got {value!r}")
            out: Any = int(value)
        else:
            out = float(value)

        if self.lo is not None and out < self.lo:
            raise KnobError(f"{out} is below the lower bound {self.lo}")
        if self.hi is not None and out > self.hi:
            raise KnobError(f"{out} is above the upper bound {self.hi}")
        return out

    def spec(self) -> dict[str, Any]:
        """The serializable declaration, as it appears in a state dict."""
        return {
            "default": self.default,
            "kind": self.kind,
            "lo": self.lo,
            "hi": self.hi,
            "enum": list(self.enum),
            "values": list(self.values),
            "frozen": self.frozen,
            "description": self.description,
            "required": self.required,
        }

    # ── what it accepts, and how to move it ────────────────────────────────
    def accepted(self) -> tuple[Any, ...]:
        """The finite set this knob accepts, or empty when it is open.

        One name for one idea: an ``enum`` knob has members and a ``str`` knob may
        declare values. A numeric knob is a range rather than a set, so it answers
        empty here and the caller samples it.
        """
        if self.kind == "enum":
            return self.enum
        if self.kind == "str":
            return self.values
        return ()

    def range(self) -> tuple[float | None, float | None]:
        """The numeric bounds, or a pair of ``None``s. Always two entries."""
        return (self.lo, self.hi)

    def alternatives(self, current: Any = None) -> list[Any]:
        """Acceptable values other than ``current``.

        A model can write any value; this is what a sampler can produce. The run
        reports which knobs a sampler cannot reach, rather than searching a smaller
        space than the author declared.
        """
        finite = self.accepted()
        if finite:
            return [value for value in finite if value != current]
        if self.kind == "bool":
            return [not bool(current)]
        if self.kind == "int":
            low = int(self.lo if self.lo is not None else 0)
            high = int(self.hi if self.hi is not None else low + 10)
            return [value for value in range(low, high + 1) if value != current]
        if self.kind == "str":
            return _mutations(str(current if current is not None else self.default))
        return []

    def sample(self, rng: Any, current: Any = None) -> Any | None:
        """A different acceptable value, or ``None`` when there is nothing to offer."""
        if self.kind == "float":
            return self._sample_float(rng, current)
        options = self.alternatives(current)
        return rng.choice(options) if options else None

    def _sample_float(self, rng: Any, current: Any) -> float | None:
        low = self.lo if self.lo is not None else 0.0
        high = self.hi if self.hi is not None else low + 1.0
        if high <= low:
            return None
        for _ in range(16):
            value = round(low + (high - low) * rng.random(), 6)
            if value != current:
                return value
        return None

    @classmethod
    def from_spec(cls, spec: dict[str, Any]) -> "Knob":
        """Rebuild a knob from :meth:`spec`, refusing unknown fields."""
        unknown = sorted(set(spec) - _SPEC_FIELDS)
        if unknown:
            raise KnobError(f"unknown knob spec field(s): {unknown}")
        return cls(
            default=spec["default"],
            kind=str(spec.get("kind", "float")),
            lo=spec.get("lo"),
            hi=spec.get("hi"),
            enum=tuple(spec.get("enum") or ()),
            values=tuple(spec.get("values") or ()),
            frozen=bool(spec.get("frozen", False)),
            description=str(spec.get("description", "")),
            required=bool(spec.get("required", False)),
        )


def check_required_knobs(
    declared: Mapping[str, "Knob"],
    values: Mapping[str, Any],
    *,
    allow_defaults: bool = False,
) -> list[tuple[str, str]]:
    """Return ``(name, message)`` for every required knob whose value is empty.

    A required knob's default is a placeholder: at run start (``Mode.run``,
    ``interactive.prepare``) a value equal to it is still refused when blank. With
    ``allow_defaults`` such a value is accepted instead, which is what V9 wants: it
    validates a mode's structure and starting state, where nothing has been given yet.
    """
    violations: list[tuple[str, str]] = []
    # declared may be a list when callers pass raw knob lists (e.g. nn.validate
    # called with declared_knobs=[]).  Only dicts carry name→Knob pairs.
    if not hasattr(declared, "items"):
        return violations
    for name, knob in declared.items():
        if not getattr(knob, "required", False) or knob.kind != "str":
            continue
        value = values.get(name, knob.default)
        if allow_defaults and value == knob.default:
            continue
        if not str(value).strip():
            violations.append(
                (name,
                 f"required knob {name!r} has no value, or an empty or blank one; "
                 "give it one")
            )
    return violations


def _mutations(text: str) -> list[str]:
    """Textual moves for an open string knob, none of which is the text itself."""
    lines = text.splitlines()
    present = {line.strip() for line in lines}
    out: list[str] = []
    for clause in STR_MUTATIONS:
        if clause not in present:
            out.append((text + "\n" + clause).strip())
    if len(lines) > 1:
        out.append("\n".join(lines[:-1]).strip())
        for clause in STR_MUTATIONS:
            out.append("\n".join([*lines[:-1], clause]).strip())
    return [value for value in dict.fromkeys(out) if value and value != text]
