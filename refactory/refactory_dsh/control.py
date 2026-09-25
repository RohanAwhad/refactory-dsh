"""A running search's control file: stop, pause, resume, and the population acts.

There are two ways to steer a search and they travel differently. Words go through
the intent ledger, because a model reads them. Control is this small file, because
it has to work from another process (`refactory steer --pause`) and from the Web
panel, and because "stop" is not prose. The loop polls it at a generation boundary,
so a request is honored between generations and never in the middle of evaluating a
candidate.

The file is the whole state: absent means "run", and the loop clears a pause by
overwriting it with `resume`. Nothing keeps a second copy in memory, so a panel, a
terminal and a resumed process all read the same answer.

Three acts carry a target and are one-shot:

* ``keep <id>`` -- pin a candidate into the parent set, and clear a rejection on it.
* ``reject <id>`` -- drop it from the leaderboard, optionally with its descendants.
* ``adopt`` -- write the current winner as a package without ending the search.

``keep`` and ``reject`` name a candidate because a person who read the leaderboard
has an opinion about *that* candidate; ``adopt`` carries the destination name for
the same reason. The loop consumes a one-shot request so the next boundary does not
repeat it.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

#: The file's name inside a run directory.
CONTROL = "control.json"

#: The actions a caller may ask for. `resume` releases a `pause`; `stop` ends the run
#: with a reason naming the person's request.
ACTIONS = ("pause", "resume", "stop", "keep", "reject", "adopt")

#: Actions that name a candidate. Without a target they cannot be honored, and
#: guessing one -- the leader, say -- would be the opposite of what was asked.
TARGETED = ("keep", "reject")

#: Actions honored once and then cleared. `pause` holds until released and `stop`
#: ends the run, so neither can be consumed; adopting the same winner every epoch,
#: or re-rejecting the same candidate, is a request that never ends.
ONE_SHOT = ("keep", "reject", "adopt")


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

    def read_request(self) -> dict | None:
        """The whole request, or ``None`` when there is none or it is unreadable.

        A file holding an unknown action reads as no request at all: a caller that
        wrote it is told so by :meth:`request`, and a run that met one would only be
        able to guess.
        """
        if not self.path.exists():
            return None
        try:
            loaded = json.loads(self.path.read_text())
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(loaded, dict):
            return None
        return loaded if loaded.get("action") in ACTIONS else None

    def read(self) -> str | None:
        """The requested action, or ``None`` when there is none or it is unreadable."""
        request = self.read_request()
        return None if request is None else str(request["action"])

    def request(
        self,
        action: str,
        *,
        target: str | None = None,
        lineage: bool = False,
        as_name: str | None = None,
    ) -> dict:
        """Ask for one action; refuses one that cannot be honored as written."""
        if action not in ACTIONS:
            raise ControlError(f"unknown control action {action!r}; known: {list(ACTIONS)}")
        target = (target or "").strip() or None
        if action in TARGETED and target is None:
            raise ControlError(
                f"{action} names a candidate; pass its id, or read the leaderboard "
                "to find one"
            )
        if target is not None and action not in TARGETED:
            raise ControlError(f"{action} does not take a candidate id")
        if lineage and action not in TARGETED:
            raise ControlError(f"{action} does not take --lineage")
        as_name = (as_name or "").strip() or None
        if as_name is not None and action != "adopt":
            raise ControlError(f"{action} does not take a destination name")

        entry: dict = {"action": action, "at": round(time.time(), 3)}
        if target is not None:
            entry["target"] = target
        if lineage:
            entry["lineage"] = True
        if as_name is not None:
            entry["as"] = as_name

        # One act at a time, and the file is where that is enforced. A one-shot act
        # that is still in the file has not been honored yet, so a second one would
        # silently replace it: a person queuing keep-then-reject would watch one of
        # their two acts disappear. Pause, resume and stop may overwrite anyway --
        # a stop is not something to hold up behind a candidate act.
        pending = self.read_request()
        if (
            pending is not None
            and pending["action"] in ONE_SHOT
            and action in ONE_SHOT
            and _without_time(pending) != _without_time(entry)
        ):
            raise ControlError(
                f"a {pending['action']} request is still waiting for the run's next "
                f"generation boundary, and a {action} now would drop it; wait for it "
                "to land, or read the run to see what it did"
            )

        self.root.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(entry))
        return entry

    def clear(self) -> None:
        """Forget the request: absent means the run continues."""
        self.path.unlink(missing_ok=True)


def _without_time(entry: dict) -> dict:
    """A request as identity, without the moment it was asked at."""
    return {key: value for key, value in entry.items() if key != "at"}


__all__ = ["ACTIONS", "CONTROL", "ONE_SHOT", "TARGETED", "Control", "ControlError"]
