"""Declared memory, with the retention policy enforced.

A mode declares what it may remember and for how long. This is the store that
honours it: a ``run`` memory dies with the run, a ``project`` memory lives with the
project, a ``global`` memory lives across projects.

The point of declaring it rather than inventing a file per optimizer is that a
cross-run prior becomes a named, inspectable thing instead of a private convention,
and its life is stated rather than accidental.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from refactory_dsh.nn.declarations import Memory

#: Where each retention lives, relative to its root.
_LAYOUT = {
    "run": ".memory",
    "project": None,  # resolved by the caller
    "global": None,
}


class MemoryError_(RuntimeError):
    """Memory was used in a way its declaration does not allow."""


class MemoryStore:
    """Read and write the namespaces a mode declared, subject to their retention.

    Roots are injected rather than discovered, so a test can put all three
    retentions somewhere temporary and assert what survives.
    """

    def __init__(
        self,
        declarations: Iterable[Memory],
        *,
        run_root: Path,
        project_root: Path | None = None,
        global_root: Path | None = None,
    ) -> None:
        self._declared: dict[str, Memory] = {}
        for declaration in declarations:
            if declaration.namespace in self._declared:
                raise MemoryError_(f"memory {declaration.namespace!r} is declared twice")
            self._declared[declaration.namespace] = declaration
        self._roots: dict[str, Path] = {
            "run": Path(run_root) / _LAYOUT["run"],
            "project": Path(project_root or Path(".factory")) / "memory",
            "global": Path(global_root or (Path.home() / ".refactory" / "memory")),
        }

    # ── what is declared ───────────────────────────────────────────────────
    def declarations(self) -> list[dict[str, Any]]:
        return [memory.to_state() for memory in self._declared.values()]

    def namespaces(self) -> list[str]:
        return sorted(self._declared)

    def declaration(self, namespace: str) -> Memory:
        try:
            return self._declared[namespace]
        except KeyError as exc:
            known = ", ".join(self.namespaces()) or "none"
            raise MemoryError_(
                f"memory {namespace!r} was not declared by the mode; declared: {known}"
            ) from exc

    def path(self, namespace: str) -> Path:
        declaration = self.declaration(namespace)
        return self._roots[declaration.retention] / f"{namespace}.json"

    # ── reading and writing ────────────────────────────────────────────────
    def read(self, namespace: str, default: Any = None) -> Any:
        declaration = self.declaration(namespace)
        path = self.path(namespace)
        if not path.exists():
            return [] if declaration.kind == "log" else default
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError:
            return [] if declaration.kind == "log" else default

    def append(self, namespace: str, item: Any, *, limit: int | None = None) -> None:
        if self.declaration(namespace).kind != "log":
            raise MemoryError_(f"memory {namespace!r} is not a log; use set() or write()")
        entries = self.read(namespace, [])
        entries.append(item)
        if limit is not None and len(entries) > limit:
            entries = entries[-limit:]
        self._write(namespace, entries)

    def set(self, namespace: str, key: str, value: Any) -> None:
        if self.declaration(namespace).kind != "map":
            raise MemoryError_(f"memory {namespace!r} is not a map; use append() or write()")
        entries = self.read(namespace, {})
        entries[str(key)] = value
        self._write(namespace, entries)

    def get(self, namespace: str, key: str, default: Any = None) -> Any:
        return self.read(namespace, {}).get(str(key), default)

    def write(self, namespace: str, value: Any) -> None:
        if self.declaration(namespace).kind != "value":
            raise MemoryError_(f"memory {namespace!r} is not a value; use append() or set()")
        self._write(namespace, value)

    def _write(self, namespace: str, value: Any) -> None:
        path = self.path(namespace)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, indent=2, default=str))

    # ── retention ──────────────────────────────────────────────────────────
    def forget_run_scoped(self) -> list[str]:
        """Delete every ``run``-retention memory. Called when the run ends.

        Returns the namespaces forgotten, so a test — or a report — can see that
        the policy was actually applied rather than merely declared.
        """
        forgotten: list[str] = []
        for namespace, declaration in sorted(self._declared.items()):
            if declaration.retention != "run":
                continue
            path = self.path(namespace)
            if path.exists():
                path.unlink()
                forgotten.append(namespace)
        return forgotten

    def describe(self) -> Mapping[str, str]:
        return {
            namespace: declaration.retention
            for namespace, declaration in sorted(self._declared.items())
        }
