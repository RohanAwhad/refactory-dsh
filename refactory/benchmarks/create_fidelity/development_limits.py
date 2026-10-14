"""Explicit development-only dispatch cap; never enabled for historical trials."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import threading


@contextmanager
def dispatch_limit(project: Path, maximum: int | None):
    """Count actual CLI launches, including transport retries, across copied clients.

    One benchmark lane owns this process. A locked reservation at the real
    transport boundary also covers clients reconstructed by native Mode.resume.
    This is a diagnostic stop, not a shorter default workflow timeout.
    """
    if maximum is None:
        yield
        return
    if type(maximum) is not int or maximum < 1:
        raise ValueError("development dispatch cap must be a positive integer")
    from refactory_dsh.model import DshModelClient
    original = DshModelClient._run_completion
    lock = threading.Lock()
    path = project / "raw/development-dispatches.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {"kind": "development-dispatch-cap/v1", "maximum": maximum, "dispatches": [],
              "scope": "Actual model CLI transport launches, including retries; individual provider requests inside one CLI are metered separately by RunBudget."}

    def limited(client, *args, **kwargs):
        with lock:
            if len(record["dispatches"]) >= maximum:
                raise RuntimeError(f"development smoke stopped before exceeding {maximum} model CLI dispatches")
            record["dispatches"].append({"number": len(record["dispatches"]) + 1,
                "node": client._node_id, "role": client._role,
                "reserved_at": datetime.now(timezone.utc).isoformat()})
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps(record, indent=2) + "\n")
            temporary.replace(path)
        return original(client, *args, **kwargs)

    DshModelClient._run_completion = limited
    try:
        yield
    finally:
        DshModelClient._run_completion = original
