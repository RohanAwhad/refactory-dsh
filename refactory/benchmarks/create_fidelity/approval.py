"""Explicit strategy decisions, bound to exact bytes and their actual authority."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import time


DEFAULT_INPUTS = (".factory/strategy/current.md", ".factory/strategy/user-intent.md")


def strategy_digest(project: Path, inputs=DEFAULT_INPUTS) -> str:
    paths = [project / name for name in inputs]
    if not paths or any(not p.resolve().is_relative_to(project.resolve()) for p in paths):
        raise ValueError("Approval inputs must be files inside the authoring project")
    return hashlib.sha256(json.dumps({str(p.relative_to(project)): hashlib.sha256(p.read_bytes()).hexdigest()
                                     for p in paths}, sort_keys=True).encode()).hexdigest()


def request(project: Path, prompt: str, *, inputs=DEFAULT_INPUTS, trial_id: str = "", lane_id: str = "") -> dict:
    record = {"gate": "gate_strategy", "strategy_digest": strategy_digest(project, inputs),
              "input_paths": list(inputs),
              "prompt": prompt, "status": "awaiting-human", "created_unix": time.time(),
              "trial_id": trial_id, "lane_id": lane_id}
    (project / "approval-required.json").write_text(json.dumps(record, indent=2) + "\n")
    return record


def deliver(project: Path, *, digest: str, verdict: str, approver: str, feedback: str) -> dict:
    pending = json.loads((project / "approval-required.json").read_text())
    if not approver.strip() or verdict not in {"proceed", "reloop", "halt"}:
        raise ValueError("An identified human and explicit verdict are required")
    if verdict == "reloop" and not feedback.strip():
        raise ValueError("A strategy revision requires the human's feedback")
    inputs = pending.get("input_paths", DEFAULT_INPUTS)
    if pending["strategy_digest"] != digest or strategy_digest(project, inputs) != digest:
        raise ValueError("Strategy/intent changed; review the new revision before answering")
    if pending.get("status") != "awaiting-human":
        raise ValueError("This approval request was already consumed; wait for a new gate visit")
    record = {"gate": "gate_strategy", "strategy_digest": digest, "verdict": verdict,
              "input_paths": list(inputs),
              "approver": approver, "feedback": feedback, "answered_unix": time.time(),
              "request_created_unix": pending["created_unix"], "source": "explicit-human"}
    path = project / "human-answer.json"
    if path.exists():
        raise ValueError("Pending answer already exists; it must be consumed before another answer")
    path.write_text(json.dumps(record, indent=2) + "\n")
    return record


def validate_delegation(reference: str, trusted: dict, *, trial_id: str, lane_id: str, operator_id: str) -> None:
    if (not reference or trusted.get("authorization") != "operator-strategy-review"
            or not isinstance(trusted.get("lane_ids"), list)
            or trusted.get("trial_id") != trial_id or lane_id not in trusted.get("lane_ids", [])
            or trusted.get("gate") != "gate_strategy" or trusted.get("operator_id") != operator_id
            or not trusted.get("user_turn_reference") or not trusted.get("user_message_reference") or not trusted.get("user_thread_reference")
            or not isinstance(trusted.get("authorization_text"), str) or not trusted["authorization_text"].strip()
            or "decision" in trusted or "verdict" in trusted):
        raise ValueError("Trusted user delegation must authorize this operator's exact trial/lane strategy review, not a standing verdict")


def operator_answer(project: Path, *, review: dict, delegation_reference: str, trusted_delegation: dict,
                    trial_id: str, lane_id: str, operator_id: str, review_sha256: str) -> dict:
    """Validate an actual externally held review; this function chooses no verdict."""
    validate_delegation(delegation_reference, trusted_delegation, trial_id=trial_id, lane_id=lane_id, operator_id=operator_id)
    pending_path = project / "approval-required.json"
    pending = json.loads(pending_path.read_text())
    if pending.get("status") != "awaiting-human":
        raise ValueError("There is no current unconsumed strategy review")
    expected = {"schema_version": 1, "decision_source": "delegated-operator", "human_inspected_strategy": False,
                "trial_id": trial_id, "lane_id": lane_id, "gate": "gate_strategy", "reviewer_id": operator_id,
                "strategy_digest": strategy_digest(project, pending["input_paths"]),
                "input_paths": pending["input_paths"], "request_created_unix": pending["created_unix"],
                "request_sha256": hashlib.sha256(pending_path.read_bytes()).hexdigest()}
    if (any(review.get(key) != value for key, value in expected.items())
            or pending.get("trial_id") != trial_id or pending.get("lane_id") != lane_id
            or pending["strategy_digest"] != expected["strategy_digest"]
            or review.get("verdict") not in {"proceed", "reloop", "halt"}
            or not isinstance(review.get("rationale"), str) or not review["rationale"].strip()
            or not review_sha256):
        raise ValueError("Operator decision must bind the actual current gate visit, bytes, reviewer and explicit reviewed verdict")
    if not isinstance(review.get("feedback", ""), str) or review["verdict"] == "reloop" and not review.get("feedback", "").strip():
        raise ValueError("Operator strategy rejection needs its specific review feedback")
    covered = set()
    for citation in review.get("evidence", []):
        name, quote = citation.get("path"), citation.get("quote")
        if name not in pending["input_paths"]:
            raise ValueError("Operator review citation is not a declared strategy/intent input")
        path = project / name
        if (hashlib.sha256(path.read_bytes()).hexdigest() != citation.get("sha256")
                or not isinstance(quote, str) or not quote.strip() or quote not in path.read_text()):
            raise ValueError("Operator review excerpt/hash is stale or unsupported")
        covered.add(name)
    if covered != set(pending["input_paths"]):
        raise ValueError("Operator review must cite the actual strategy and original intent")
    return {**expected, "source": "delegated-operator", "verdict": review["verdict"], "feedback": review.get("feedback", ""),
            "approver": operator_id, "rationale": review["rationale"], "evidence": review["evidence"],
            "answered_unix": time.time(), "delegation_reference": delegation_reference,
            "trusted_user_delegation": trusted_delegation, "operator_review_sha256": review_sha256,
            "human_inspected_strategy": False}


def take(project: Path, pending: dict, *, operator_answer: dict | None = None) -> dict | None:
    path = project / "human-answer.json"
    if operator_answer is None and not path.is_file():
        return None
    record = operator_answer if operator_answer is not None else json.loads(path.read_text())
    if operator_answer is None and record.get("source") == "delegated-operator":
        raise ValueError("Lane-authored delegated decisions are rejected; only the operator controller pipe can deliver them")
    archive = project / "raw/approvals"
    archive.mkdir(parents=True, exist_ok=True)
    target = archive / f"{time.time_ns()}.json"
    if operator_answer is None:
        path.replace(target)
    else:
        target.write_text(json.dumps(record, indent=2) + "\n")
    inputs = pending.get("input_paths", DEFAULT_INPUTS)
    if (tuple(record.get("input_paths", DEFAULT_INPUTS)) != tuple(inputs)
            or record.get("strategy_digest") != strategy_digest(project, inputs)
            or record.get("strategy_digest") != pending["strategy_digest"]
            or record.get("request_created_unix") != pending["created_unix"]):
        raise ValueError("Human answer is stale or belongs to another gate visit")
    current = json.loads((project / "approval-required.json").read_text())
    if current.get("status") != "awaiting-human" or current.get("created_unix") != pending["created_unix"]:
        raise ValueError("Strategy decision was already consumed or belongs to another gate visit")
    if record.get("source") not in {"explicit-human", "delegated-operator"} or not record.get("approver"):
        raise ValueError("Unattributed gate answer cannot authorize a strategy")
    if record["source"] == "delegated-operator":
        if record.get("human_inspected_strategy") is not False or record.get("request_sha256") != hashlib.sha256((project / "approval-required.json").read_bytes()).hexdigest():
            raise ValueError("Delegated decision misstates human review or changed its request")
        validate_delegation(record["delegation_reference"], record["trusted_user_delegation"],
                            trial_id=pending["trial_id"], lane_id=pending["lane_id"], operator_id=record["approver"])
    (project / "approval-required.json").write_text(json.dumps({
        **pending, "status": "consumed", "answer_path": str(target.relative_to(project)),
    }, indent=2) + "\n")
    return record


def wait_for_answer(project: Path, pending: dict, *, timeout_s: float) -> dict:
    """Bound direct-human waiting and consume only actual supplied decisions."""
    from .supervision import current
    if not math.isfinite(timeout_s) or timeout_s < 0:
        raise ValueError("Strategy wait requires a finite nonnegative remaining allowance")
    deadline = time.monotonic() + timeout_s
    while True:
        owner = current()
        supplied = owner.take_operator_answer() if owner is not None else None
        answer = take(project, pending, operator_answer=supplied) if supplied is not None else take(project, pending)
        if answer is not None:
            return answer
        if time.monotonic() >= deadline:
            raise TimeoutError("Benchmark strategy approval wait exceeded its remaining wall allowance")
        time.sleep(0.1)
