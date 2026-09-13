"""What a mode declares about itself: its memory, and what it publishes.

Two things `srf/packaging` has and we had no equivalent for.

``Memory`` makes memory *declared* rather than implicit in whatever an optimizer
happens to keep, and gives it a retention policy — the difference between a memory
that dies with the run and one that accumulates across runs. Without it, a
cross-run prior is an ad-hoc file an individual optimizer invented.

``Port`` makes a run's outputs *meaningful* rather than inferred from file writes:
"this run produced a best solution, a leaderboard, and a trace" is what a GUI
renders and what a downstream mode can read as input.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_NAME = re.compile(r"^[a-z][a-z0-9_.-]*$")

#: How long a declared memory lives.
RETENTIONS = ("run", "project", "global")

#: What shape a declared memory has.
KINDS = ("log", "map", "value")


class DeclarationError(ValueError):
    """A memory or port declaration is not well formed."""


@dataclass(frozen=True)
class Memory:
    """A namespace a mode may keep, with the policy that governs its life."""

    namespace: str
    kind: str = "log"
    retention: str = "run"
    description: str = ""

    def __post_init__(self) -> None:
        if not _NAME.match(self.namespace or ""):
            raise DeclarationError(
                f"memory namespace {self.namespace!r} must be lower-case letters, "
                "digits, '_', '.' or '-', starting with a letter, e.g. 'gepa.rejections'"
            )
        if self.kind not in KINDS:
            raise DeclarationError(
                f"unknown memory kind {self.kind!r}; known: {list(KINDS)}"
            )
        if self.retention not in RETENTIONS:
            raise DeclarationError(
                f"unknown retention {self.retention!r}; known: {list(RETENTIONS)}; "
                "'run' dies with the run, 'project' lives with the project, "
                "'global' lives across projects"
            )

    def to_state(self) -> dict[str, Any]:
        return {
            "namespace": self.namespace,
            "kind": self.kind,
            "retention": self.retention,
            "description": self.description,
        }

    @classmethod
    def from_state(cls, state: dict[str, Any]) -> "Memory":
        return cls(
            namespace=str(state.get("namespace", "")),
            kind=str(state.get("kind", "log")),
            retention=str(state.get("retention", "run")),
            description=str(state.get("description", "")),
        )


@dataclass(frozen=True)
class Port:
    """Something a run publishes, so a reader knows what it has rather than guessing."""

    name: str
    path: str
    media_type: str = "text/markdown"
    description: str = ""

    def __post_init__(self) -> None:
        if not _NAME.match(self.name or ""):
            raise DeclarationError(f"port name {self.name!r} is not a valid name")
        if not (self.path or "").strip():
            raise DeclarationError(f"port {self.name!r} declares no path")
        if not (self.media_type or "").strip():
            raise DeclarationError(f"port {self.name!r} declares no media type")

    def to_state(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "path": self.path,
            "media_type": self.media_type,
            "description": self.description,
        }

    @classmethod
    def from_state(cls, state: dict[str, Any]) -> "Port":
        return cls(
            name=str(state.get("name", "")),
            path=str(state.get("path", "")),
            media_type=str(state.get("media_type", "text/markdown")),
            description=str(state.get("description", "")),
        )


def memory_declarations(mode: Any) -> list[dict[str, Any]]:
    return [declaration.to_state() for declaration in mode.memory()]


def port_declarations(mode: Any) -> list[dict[str, Any]]:
    return [declaration.to_state() for declaration in mode.ports()]
