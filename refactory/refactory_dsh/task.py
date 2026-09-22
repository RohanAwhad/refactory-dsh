"""The Task protocol: what a dataset mode's items are, and how one is judged.

A dataset mode iterates its subgraph once per item. Where the items come from, and
whether a pass was any good, is the Task's business rather than the graph's — the
same split remote-factory's `factory/task.py` makes, so a mode bound to a benchmark
or a dataset carries that binding as a declaration instead of as instructions in a
prompt.

A Task is four hooks, all optional:

- ``instances()`` — the items. The default is one unnamed instance, which is what a
  mode that only needs to run once wants.
- ``setup(instance, workspace)`` — prepare the workspace before the pass.
- ``prompt(instance)`` — what the agent for that item is asked to do.
- ``verify(instance, workspace)`` — judge the pass, or return ``None`` to say the
  Task has no opinion and the mode's own score stands.

Resolution is by ``module:Class`` or by a name registered here, and a ref that does
not resolve is refused by name: a mode that names a Task nobody can load must not
silently run its items unverified.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class TaskError(ValueError):
    """A Task reference does not resolve, or a Task misbehaves."""


@dataclass(frozen=True)
class TaskInstance:
    """One item a Task says the mode has to process."""

    id: str
    path: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class VerifyResult:
    """What one pass was worth."""

    passed: bool
    score: float
    details: str = ""


class Task:
    """The four hooks a dataset binding implements."""

    name = ""

    def instances(self) -> list[TaskInstance]:
        """The items to process. One unnamed instance unless the Task says otherwise."""
        return [TaskInstance(id="default")]

    def setup(self, instance: TaskInstance, workspace: Path) -> None:
        """Prepare the workspace for one item. Nothing to do by default."""

    def prompt(self, instance: TaskInstance) -> str:
        """What the agent for this item is asked to do."""
        return "Implement the feature. All tests must pass."

    def verify(self, instance: TaskInstance, workspace: Path) -> VerifyResult | None:
        """Judge one pass, or ``None`` when the Task has no opinion."""
        return None


#: The Tasks a bare name can resolve to.
TASKS: dict[str, type[Task]] = {}


def register_task(name: str, cls: type[Task]) -> None:
    """Make a Task resolvable by name, and return nothing."""
    TASKS[name] = cls


def load_task(ref: str, project: Path | None = None) -> Task:
    """Resolve a Task reference to an instance.

    ``module:Class`` imports it; a bare name is looked up in :data:`TASKS`. Anything
    else is refused with the ref in the message, because a mode bound to a Task that
    cannot be loaded would run its items with nothing judging them.
    """
    ref = (ref or "").strip()
    if not ref:
        raise TaskError("an empty Task reference names no Task")
    if ":" in ref:
        module_name, _, attribute = ref.partition(":")
        try:
            module = importlib.import_module(module_name)
        except ImportError as exc:
            raise TaskError(f"cannot import {module_name!r} for Task {ref!r}: {exc}") from exc
        cls = getattr(module, attribute, None)
        if cls is None:
            raise TaskError(f"{module_name!r} has no attribute {attribute!r} (Task {ref!r})")
        if not isinstance(cls, type) or not issubclass(cls, Task):
            raise TaskError(f"{ref!r} is not a Task subclass")
        return cls()
    if ref not in TASKS:
        known = ", ".join(sorted(TASKS)) or "none"
        raise TaskError(f"no Task named {ref!r} is registered; registered: {known}")
    return TASKS[ref]()


def instance_of(item: dict[str, Any]) -> TaskInstance:
    """The Task instance one loaded item describes."""
    metadata = {
        key: value for key, value in item.items() if key not in ("id", "path")
    }
    return TaskInstance(
        id=str(item.get("id") or ""),
        path=str(item["path"]) if item.get("path") else None,
        metadata=metadata,
    )
