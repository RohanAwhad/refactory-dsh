"""Independent proof for actual quote attempts, never accepted invalid leaves."""
import hashlib
import json
from pathlib import Path

import pytest

from benchmarks.create_fidelity.bounded_context_evidence import verify_bounded_context
from benchmarks.create_fidelity.contract import reference
from test_create_fidelity_quote_correction import run_quote_stage


def evidence(root, monkeypatch):
    role_text = next(body for name, body in reference()["role_prompts"].items() if Path(name).stem == "health_checker")
    outcome, manifest, _, body, _ = run_quote_stage(root, monkeypatch,
        prompt_template=role_text + "\n\nOriginal stage: review complete actual evidence.")
    assert outcome.exit_ok, outcome.error
    path = next(root.glob(".refactory/create-fidelity-context/*/*/manifest.json"))
    args = dict(consumer_node="review", role="health_checker", role_text=role_text,
                stage_text="Original stage: review complete actual evidence.",
                required_documents={"evidence.md": body}, required_bodies=[body])
    return str(path.relative_to(root)), manifest, args


def write(root, name, value):
    (root / name).write_text(json.dumps(value))


def rebind_snapshot(root, info, raw):
    (root / info["snapshot"]).write_bytes(raw)
    info.update(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())


def test_actual_quote_correction_binds_both_transports_preserves_every_finding(tmp_path, monkeypatch):
    name, manifest, args = evidence(tmp_path, monkeypatch)
    refs = verify_bounded_context(tmp_path, name, **args)
    correction = manifest["reviews"][0]["quote_correction"]
    prior = correction["rejected_attempt"]
    assert prior["worker_receipts"][0] in refs
    assert prior["raw_response"]["snapshot"] in refs
    assert prior["supplied_prompt"]["snapshot"] in refs
    assert len(manifest["reviews"][0]["review"]["claims"]) == 45
    assert {item["verdict"] for item in manifest["reviews"][0]["review"]["claims"]} == {"adverse", "unresolved"}
    assert len(refs) > len(manifest["pages"])
    # Complete-range transport is verified; no health correctness PASS inferred.
    assert all(item["status"] == "unresolved" for item in prior["review"]["coverage"])


@pytest.mark.parametrize("problem", ["two-corrections", "drop-adverse", "reorder-claims", "semantic-change", "wrong-request",
    "false-rejection", "missing-worker", "wrong-role", "changed-prior-response", "unknown-prior-coverage",
    "malformed-later-claim", "actually-valid-prior-quote", "shortened-prior-prompt", "deadline-reset", "source-drift"])
def test_rebound_or_incomplete_quote_history_cannot_get_complete_read_credit(tmp_path, monkeypatch, problem):
    name, manifest, args = evidence(tmp_path, monkeypatch)
    accepted = manifest["reviews"][0]
    correction = accepted["quote_correction"]
    prior = correction["rejected_attempt"]
    if problem == "two-corrections":
        correction["max_extra_requests"] = 2
    elif problem == "drop-adverse":
        accepted["review"]["claims"].pop()
    elif problem == "reorder-claims":
        accepted["review"]["claims"].reverse()
    elif problem == "semantic-change":
        accepted["review"]["claims"][0]["reason"] = "Softened actual adverse observation."
    elif problem == "wrong-request":
        correction["request"]["role"] = "ceo"
    elif problem == "false-rejection":
        prior["error"] = "TimeoutError: incomplete model execution"
    elif problem == "missing-worker":
        (tmp_path / prior["worker_receipts"][0]).unlink()
    elif problem in {"wrong-role", "deadline-reset"}:
        target = prior if problem == "wrong-role" else accepted
        worker_name = target["worker_receipts"][0]
        worker = json.loads((tmp_path / worker_name).read_text())
        if problem == "wrong-role":
            worker["role"] = "ceo"
        else:
            sample = worker["effective_deadlines"]["dispatches"][0]
            timer = next(item for item in sample["enclosing_deadlines"] if item["scope"] == "bounded_context")
            timer["remaining_s"] += 10
            # Leave all internal clock arithmetic coherent: the original
            # absolute expiry is still demonstrably extended by ten seconds.
            sample["stage_remaining_s"] += 10
            worker["effective_deadlines"]["stage_timeout_s"] += 10
            worker["effective_deadlines"]["declared_stage_timeout_s"] += 10
            sample["effective_remaining_s"] += 10
        write(tmp_path, worker_name, worker)
        target["worker_receipt_sha256"][worker_name] = hashlib.sha256((tmp_path / worker_name).read_bytes()).hexdigest()
    elif problem in {"changed-prior-response", "unknown-prior-coverage", "malformed-later-claim", "actually-valid-prior-quote"}:
        value = prior["review"]
        if problem == "unknown-prior-coverage":
            value["coverage"][0]["obligation_id"] = "unknown-source-label"
        elif problem == "malformed-later-claim":
            value["claims"][-1]["reason"] = ""
        elif problem == "actually-valid-prior-quote":
            value["claims"][0].update({key: accepted["review"]["claims"][0][key]
                                       for key in ("quote", "start_byte", "end_byte")})
        else:
            value["claims"][0]["verdict"] = "supported"
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        rebind_snapshot(tmp_path, prior["raw_response"], raw)
        delivery = json.loads((tmp_path / prior["delivery_receipt"]).read_text())
        (tmp_path / delivery["final_response"]).write_bytes(raw)
        correction["rejected_response_sha256"] = hashlib.sha256(raw).hexdigest()
    elif problem == "shortened-prior-prompt":
        delivery = json.loads((tmp_path / accepted["delivery_receipt"]).read_text())
        raw = (tmp_path / accepted["supplied_prompt"]["snapshot"]).read_bytes()
        start = raw.index(b"COMPLETE REJECTED MODEL RESPONSE:\n") + len(b"COMPLETE REJECTED MODEL RESPONSE:\n")
        end = raw.index(b"\n=== END COMPLETE REJECTED MODEL RESPONSE ===", start)
        raw = raw[:start] + b'{"summary":"all prior findings kept"}' + raw[end:]
        rebind_snapshot(tmp_path, accepted["supplied_prompt"], raw)
        (tmp_path / delivery["contexts"][0]["prompt"]).write_bytes(raw)
        worker_name = accepted["worker_receipts"][0]
        worker = json.loads((tmp_path / worker_name).read_text())
        task = (accepted["system"] + "\n\n").encode() + raw
        worker["prompt_transport"].update(bytes=len(task), sha256=hashlib.sha256(task).hexdigest())
        write(tmp_path, worker_name, worker)
        accepted["worker_receipt_sha256"][worker_name] = hashlib.sha256((tmp_path / worker_name).read_bytes()).hexdigest()
    else:
        (tmp_path / "evidence.md").write_text("Later changed source")
    write(tmp_path, name, manifest)
    with pytest.raises((ValueError, OSError, KeyError)):
        verify_bounded_context(tmp_path, name, **args)
