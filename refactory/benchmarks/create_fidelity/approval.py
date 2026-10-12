"""Explicit human answer delivery, bound to the exact pending strategy bytes."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import time


def strategy_digest(project: Path) -> str:
    paths = [project / ".factory/strategy/current.md", project / ".factory/strategy/user-intent.md"]
    return hashlib.sha256(json.dumps({str(p.relative_to(project)): hashlib.sha256(p.read_bytes()).hexdigest()
                                     for p in paths}, sort_keys=True).encode()).hexdigest()


def request(project: Path, prompt: str) -> dict:
    record = {"gate": "gate_strategy", "strategy_digest": strategy_digest(project),
              "prompt": prompt, "status": "awaiting-human", "created_unix": time.time()}
    (project / "approval-required.json").write_text(json.dumps(record, indent=2) + "\n")
    return record


def deliver(project: Path, *, digest: str, verdict: str, approver: str, feedback: str) -> dict:
    pending = json.loads((project / "approval-required.json").read_text())
    if not approver.strip() or verdict not in {"proceed", "reloop", "halt"}:
        raise ValueError("An identified human and explicit verdict are required")
    if verdict == "reloop" and not feedback.strip():
        raise ValueError("A strategy revision requires the human's feedback")
    if pending["strategy_digest"] != digest or strategy_digest(project) != digest:
        raise ValueError("Strategy/intent changed; review the new revision before answering")
    record = {"gate": "gate_strategy", "strategy_digest": digest, "verdict": verdict,
              "approver": approver, "feedback": feedback, "answered_unix": time.time(),
              "request_created_unix": pending["created_unix"], "source": "explicit-human"}
    path = project / "human-answer.json"
    if path.exists():
        raise ValueError("Pending answer already exists; it must be consumed before another answer")
    path.write_text(json.dumps(record, indent=2) + "\n")
    return record


def take(project: Path, pending: dict) -> dict | None:
    path = project / "human-answer.json"
    if not path.is_file():
        return None
    record = json.loads(path.read_text())
    archive = project / "raw/approvals"
    archive.mkdir(parents=True, exist_ok=True)
    target = archive / f"{time.time_ns()}.json"
    path.replace(target)
    if (record.get("strategy_digest") != strategy_digest(project)
            or record.get("strategy_digest") != pending["strategy_digest"]
            or record.get("request_created_unix") != pending["created_unix"]):
        raise ValueError("Human answer is stale or belongs to another gate visit")
    if record.get("source") != "explicit-human" or not record.get("approver"):
        raise ValueError("Unattributed gate answer cannot authorize a strategy")
    return record
