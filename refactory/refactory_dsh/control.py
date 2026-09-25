"""A running search's control file: stop, pause, resume.

There are two ways to steer a search and they travel differently. Words go through
the intent ledger, because a model reads them. Control is this small file, because
it has to work from another process (`refactory steer --pause`) and from the Web
panel, and because "stop" is not prose. The loop polls it at a generation boundary,
so a request is honored between generations and never in the middle of evaluating a
candidate.

The file is the whole state: absent means "run", and the loop clears a pause by
overwriting it with `resume`. Nothing keeps a second copy in memory, so a panel, a
terminal and a resumed process all read the same answer.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

#: The file's name inside a run directory.
CONTROL = "control.json"

#: The actions a caller may ask for. `resume` releases a `pause`; `stop` ends the run
#: with a reason naming the person's request.
ACTIONS = ("pause", "resume", "stop")


class ControlError(ValueError):
    """A control request that cannot be honored as written."""


class Control:
    """Read and write one run's control request."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    @property
    def path(self) -> Path:
        """Where the request lives."""
        return self.root / CONTROL

    def read(self) -> str | None:
        """The requested action, or ``None`` when there is none or it is unreadable."""
        if not self.path.exists():
            return None
        try:
            loaded = json.loads(self.path.read_text())
        except (OSError, json.JSONDecodeError):
            return None
        action = loaded.get("action") if isinstance(loaded, dict) else None
        return str(action) if action in ACTIONS else None

    def request(self, action: str) -> dict:
        """Ask for one action; refuses an unknown one by name."""
        if action not in ACTIONS:
            raise ControlError(f"unknown control action {action!r}; known: {list(ACTIONS)}")
        entry = {"action": action, "at": round(time.time(), 3)}
        self.root.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(entry))
        return entry

    def clear(self) -> None:
        """Forget the request: absent means the run continues."""
        self.path.unlink(missing_ok=True)


__all__ = ["ACTIONS", "CONTROL", "Control", "ControlError"]
