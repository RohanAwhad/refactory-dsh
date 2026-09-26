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

A finished search rejects new live controls. Adoption runs immediately through
the ordinary winner/audit guard, first recovering any legacy queued selection.
"""

from __future__ import annotations

import errno
import json
import os
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

#: The file's name inside a run directory.
CONTROL = "control.json"
SEARCH_STATE = Path(".refactory/optimizer-status.json")
SEARCH_LOCK = Path(".refactory/optimizer.lock")


def search_owner(root: Path) -> dict:
    """Read the actual strategy owner's lifecycle; init/report alone has none."""
    path = Path(root) / SEARCH_STATE
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def search_phase(root: Path) -> str | None:
    owner = search_owner(root)
    phase = owner.get("status")
    if phase == "running":
        try:
            os.kill(int(owner["pid"]), 0)
        except (ProcessLookupError, ValueError, KeyError, TypeError):
            return "died"
        except PermissionError:
            pass  # another user's live process still owns its run
        return "running"
    return str(phase) if phase in ("finished", "failed") else None


def search_finished(root: Path) -> bool:
    phase = search_phase(root)
    if phase is not None:
        return phase != "running"
    return (Path(root) / "report.json").exists()


def request_identity(request: dict) -> dict:
    """A particular request, including its time and complete target semantics."""
    return {key: request[key] for key in ('action', 'at', 'target', 'lineage', 'as') if key in request}


@contextmanager
def _search_lock(root: Path):
    """Hold one OS-owned lock until strategy execution ends, including on death.

    The persistent file is only the lock's inode: never unlink it, since another
    process may already have opened it. The OS releases ownership on process exit.
    """
    path = root / SEARCH_LOCK
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as handle:
        if os.name == 'nt':
            import msvcrt

            if handle.tell() == 0:
                handle.write(b'\0')
                handle.flush()
            handle.seek(0)
            lock = lambda: msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            unlock = lambda: msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            lock = lambda: fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            unlock = lambda: fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        try:
            lock()
        except OSError as error:
            if error.errno not in (errno.EACCES, errno.EAGAIN):
                raise
            raise ControlError(f"an optimizer already owns {root}") from error
        try:
            yield
        finally:
            unlock()


@contextmanager
def running_search(root: Path):
    """Exclusively own actual strategy execution and retire a terminal report."""
    root = Path(root)
    with _search_lock(root), _running_search(root):
        yield


@contextmanager
def _running_search(root: Path):
    # Retain the PID check inside the lock for an owner started by an older
    # version that wrote lifecycle state but did not acquire the OS lock.
    if search_phase(root) == "running":
        raise ControlError(f"an optimizer already owns {root}")
    path = root / SEARCH_STATE
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {"status": "running", "pid": os.getpid(), "started": time.time(), "id": uuid.uuid4().hex}

    def save() -> None:
        temporary = path.with_suffix(f'.{record["id"]}.tmp')
        temporary.write_text(json.dumps(record) + '\n')
        temporary.replace(path)

    save()
    try:
        previous = path.parent / 'previous-reports' / record['id']
        for name in ('report.json', 'report.md'):
            old = root / name
            if old.exists():
                previous.mkdir(parents=True, exist_ok=True)
                old.replace(previous / name)
        yield
    except BaseException:
        record.update(status="failed", ended=time.time())
        save()
        raise
    else:
        record.update(status="finished" if (root / 'report.json').exists() else "failed", ended=time.time())
        save()

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
        project: Path | None = None,
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

        if search_finished(self.root):
            if action != "adopt":
                raise ControlError(
                    f"this search is finished; {action} requires a live optimizer "
                    "and was not queued. Adopt can apply the audited winner now."
                )
            from refactory_dsh.finished_control import adopt_finished

            return adopt_finished(self.root, entry, project=project)

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
