"""Typed parameters: the only thing an optimizer may write.

A :class:`Knob` is a declared parameter with a kind and bounds. Coercion is
strict, and that is the point: a value a knob does not accept is an error to
report, never a value to clamp. Clamping would attribute the score to a value the
optimizer did not propose.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: Kinds a knob may declare. ``enum`` holds its accepted values in ``enum``.
KINDS = ("int", "float", "bool", "str", "enum")

_SPEC_FIELDS = frozenset(
    {"default", "kind", "lo", "hi", "enum", "frozen", "description"}
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
    frozen: bool = False
    description: str = ""

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise KnobError(f"unknown knob kind {self.kind!r}; known: {list(KINDS)}")
        if self.kind == "enum" and not self.enum:
            raise KnobError("an 'enum' knob must declare at least one accepted value")
        if self.kind != "enum" and self.enum:
            raise KnobError(f"'{self.kind}' knob must not declare enum values")
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
            "frozen": self.frozen,
            "description": self.description,
        }

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
            frozen=bool(spec.get("frozen", False)),
            description=str(spec.get("description", "")),
        )
