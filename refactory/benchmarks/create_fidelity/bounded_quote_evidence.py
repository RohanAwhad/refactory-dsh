"""Independent evidence for one completed, quote-only read correction.

This never retries a model or credits a rejected response as an accepted leaf.
Both real transports and the unchanged semantic content must be demonstrated.
"""
from __future__ import annotations

from datetime import datetime, timedelta
import json


def validate_page_read(raw, entry, page, manifest, obligation_ids, original):
    from .bounded_context_evidence import _require
    value = json.loads(raw)
    keys = {"schema_version", "scope", "context_id", "logical_node_id", "role", "page_id", "document_sha256",
            "start_byte", "end_byte", "page_sha256", "coverage", "claims"}
    _require(type(value) is dict and set(value) == keys and type(value.get("schema_version")) is int
             and value["schema_version"] == 1 and value.get("scope") == "page_only" and value == entry.get("review"),
             "context read record differs from the actual uttered typed model response")
    identity = {"context_id": manifest["context_id"], "logical_node_id": manifest["logical_node_id"], "role": manifest["role"],
                **{key: page[key] for key in ("page_id", "document_sha256", "start_byte", "end_byte", "page_sha256")}}
    _require(all(value[key] == expected and type(value[key]) is type(expected) for key, expected in identity.items()),
             "actual uttered context read has stale/forged range identity")
    coverage = value["coverage"]
    _require(type(coverage) is list and all(type(item) is dict for item in coverage)
             and len({item.get("obligation_id") for item in coverage}) == len(coverage)
             and {item.get("obligation_id") for item in coverage} <= obligation_ids
             and all(set(item) == {"obligation_id", "status", "reason"}
                     and item["status"] in {"evidence", "adverse", "not_in_page", "unresolved"}
                     and isinstance(item["reason"], str) and bool(item["reason"].strip()) for item in coverage),
             "actual page-only read has duplicate, unknown or invalid sparse obligation coverage")
    claims = value["claims"]
    _require(type(claims) is list and bool(claims), "actual read omitted concrete source excerpts")
    ids, failures = set(), []
    for claim in claims:
        _require(type(claim) is dict and set(claim) == {"claim_id", "verdict", "reason", "start_byte", "end_byte", "quote"}
                 and isinstance(claim["claim_id"], str) and bool(claim["claim_id"].strip()) and claim["claim_id"] not in ids
                 and claim["verdict"] in {"supported", "adverse", "unresolved"}
                 and isinstance(claim["reason"], str) and bool(claim["reason"].strip())
                 and type(claim["start_byte"]) is int and type(claim["end_byte"]) is int
                 and isinstance(claim["quote"], str) and bool(claim["quote"]),
                 "actual read has invalid strict claim fields")
        ids.add(claim["claim_id"])
        in_page = page["start_byte"] <= claim["start_byte"] < claim["end_byte"] <= page["end_byte"]
        if not in_page or claim["quote"].encode() != original[claim["start_byte"]:claim["end_byte"]]:
            failures.append({"claim_id": claim["claim_id"], "reason":
                "declared range is outside the supplied page" if not in_page else
                "literal quote does not equal its declared original UTF-8 byte slice"})
    return failures


def _semantics(value):
    return {**value, "claims": [{key: item for key, item in claim.items()
                                 if key not in {"quote", "start_byte", "end_byte"}} for claim in value["claims"]]}


def _task(prompt, has_steering):
    """Remove only delivery's explicitly delimited context, never task bytes."""
    from .bounded_context_evidence import _require
    start, end = "<!-- refactory worker execution -->", "<!-- end refactory worker execution -->"
    _require(prompt.startswith(start) and end + "\n\n" in prompt, "quote attempt lacks actual execution context")
    task = prompt.split(end + "\n\n", 1)[1]
    steering = "\n\n<!-- refactory steering context -->\n"
    if has_steering:
        _require(steering in task, "quote attempt omitted its original steering authority")
        task, block = task.rsplit(steering, 1)
        _require(block.endswith("<!-- end refactory steering context -->"), "quote attempt steering block is malformed")
    return task


def verify_quote_attempt(root, manifest, entry, page, *, dispatch, snapshot, obligation_ids, source_bytes):
    from .bounded_context_evidence import _bytes, _canonical, _record, _require, _sha
    from .deadline_evidence import remaining_clock_samples
    correction = entry["quote_correction"]
    keys = {"schema_version", "kind", "max_extra_requests", "semantic_projection_sha256", "rejected_response_sha256",
            "validation_failures", "rejected_attempt", "request"}
    _require(type(correction) is dict and set(correction) == keys
             and type(correction["schema_version"]) is int and correction["schema_version"] == 1
             and correction["kind"] == "quote-anchor-correction/v1"
             and type(correction["max_extra_requests"]) is int and correction["max_extra_requests"] == 1,
             "quote correction is not the single explicit versioned extra request")
    rejected = correction["rejected_attempt"]
    original_node = f"{manifest['logical_node_id']}:context:{manifest['context_id']}:{page['page_id']}"
    _require(type(rejected) is dict and rejected.get("status") == "quote_rejected"
             and rejected.get("page_id") == page["page_id"] and rejected.get("execution_node_id") == original_node
             and "quote_correction" not in rejected and "quote_rejection" not in rejected
             and entry.get("execution_node_id") == original_node + ":quote-correction-1"
             and entry.get("system") == rejected.get("system") == "Evidence-only read by the original logical role.",
             "quote correction invented a prior attempt or changed original ownership")
    old_prompt, raw = dispatch(rejected, original_node)
    _require(rejected.get("worker_receipt_sha256") == {name: _sha(_bytes(root, name)) for name in rejected["worker_receipts"]}
             and rejected.get("delivery_receipt_sha256") == _sha(_bytes(root, rejected["delivery_receipt"])),
             "rejected quote attempt raw worker/delivery changed")
    page_data = _bytes(root, page["snapshot"])
    _require("=== EXACT EVIDENCE RANGE ===\n" + page_data.decode() + "\n=== END EXACT EVIDENCE RANGE ===" in old_prompt,
             "rejected quote attempt did not consume complete original page")
    failures = validate_page_read(raw, rejected, page, manifest, obligation_ids, _bytes(root, page["document_snapshot"]))
    _require(bool(failures) and correction["validation_failures"] == failures
             and rejected.get("error") == "_QuoteAnchoringError: context evidence read has forged or invalid exact claim excerpt: "
                 + "; ".join(item["claim_id"] + ": " + item["reason"] for item in failures),
             "quote correction is not an otherwise-valid actual quote anchoring rejection")
    projection = _canonical(_semantics(json.loads(raw)))
    _require(correction["semantic_projection_sha256"] == projection
             and correction["rejected_response_sha256"] == _sha(raw)
             and _semantics(entry["review"]) == _semantics(rejected["review"]),
             "quote correction changed or dropped original model-uttered semantics")
    request = {"schema_version": 1, "kind": "quote-anchor-correction/v1", "context_id": manifest["context_id"],
               "logical_node_id": manifest["logical_node_id"], "role": manifest["role"], "page_id": page["page_id"],
               "rejected_response_sha256": _sha(raw), "semantic_projection_sha256": projection, "validation_failures": failures}
    _require(correction["request"] == request, "quote correction request changed rejection identity or precise feedback")
    old_delivery, new_delivery = (_record(root, item["delivery_receipt"]) for item in (rejected, entry))
    old_ledger = _bytes(root, old_delivery["contexts"][0]["ledger"])
    _require(old_ledger == _bytes(root, new_delivery["contexts"][0]["ledger"]),
             "quote correction changed its original steering authority")
    original_task = _task(old_prompt, bool(old_ledger))
    instruction = ("\nThe prior completed response has otherwise valid strict fields but invalid quote/range anchoring. "
        "Return the complete same strict JSON response, changing ONLY quote, start_byte and end_byte inside claims. "
        "Preserve every other field/value, coverage item, claim ID/order/verdict/reason and all adverse/unresolved content EXACTLY. "
        "Use read-only byte computations on the SAME supplied immutable page and validate every final quote/range. "
        "Do not repair source evidence, omit findings, change semantics, or invent a final verdict. This is the only extra request.")
    expected_task = (original_task + "\n\nQUOTE ANCHOR CORRECTION REQUEST:\n"
        + json.dumps(request, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + instruction
        + "\nCOMPLETE REJECTED MODEL RESPONSE:\n" + raw.decode() + "\n=== END COMPLETE REJECTED MODEL RESPONSE ===")
    actual_task = _task(snapshot(entry["supplied_prompt"]).decode(), bool(old_ledger))
    _require(actual_task == expected_task, "corrected model did not receive complete unchanged original task and rejected raw response")
    for key in ("supplied_prompt", "raw_response"):
        _require(entry[key]["snapshot"] != rejected[key]["snapshot"], "quote correction overwrote its actual first attempt")
    _require(entry["delivery_receipt"] != rejected["delivery_receipt"]
             and not set(entry["worker_receipts"]) & set(rejected["worker_receipts"]), "quote correction reused prior raw dispatch")
    _require(datetime.fromisoformat(new_delivery["started"]) >= datetime.fromisoformat(old_delivery["finished"]),
             "quote correction was dispatched before its known-completed rejection")
    _require(all(old_delivery["execution"]["input_fingerprints"].get(name) == _sha(data)
                 for name, data in source_bytes.items()), "rejected quote attempt input fingerprints stale or incomplete")
    workers = [(item["worker_receipts"][0], _record(root, item["worker_receipts"][0])) for item in (rejected, entry)]
    # This check binds retries to the original wall clock. Actual transport
    # activity is assessed separately by F.deadlines.remaining.
    for _, worker in workers:
        remaining_clock_samples(worker)
    expiries = []
    for _, worker in workers:
        samples = worker["effective_deadlines"]["dispatches"]
        for sample in samples:
            timers = [clock for clock in sample["enclosing_deadlines"] if clock["scope"] == "bounded_context"]
            _require(len(timers) == 1, "quote correction omitted original absolute context timer")
            expiries.append(datetime.fromisoformat(sample["sampled_at"]) + timedelta(seconds=timers[0]["remaining_s"]))
    # UTC recording and monotonic remaining samples occur on adjacent reads.
    _require((max(expiries) - min(expiries)).total_seconds() <= .1,
             "quote correction reset or extended its original absolute deadline")
    reserve = manifest["policy"].get("applied_quote_correction_reserve_bytes")
    configured = manifest["policy"].get("quote_correction_reserve_bytes")
    room = manifest["policy"].get("quote_correction_page_room_bytes")
    fixed = original_task.split("\n\nEVIDENCE READ REQUEST:\n", 1)[0]
    _require(type(configured) is int and configured >= 0 and type(room) is int and room > 0
             and room == manifest["policy"]["max_prompt_bytes"] - len(fixed.encode()) - 12000
             and type(reserve) is int and reserve == min(configured, manifest["policy"]["max_prompt_bytes"] // 6, room // 3)
             and manifest["policy"].get("quote_correction_reserve_rule") ==
                 "min(configured_bytes, max_prompt_bytes//6, original_page_room_bytes//3)"
             and len((rejected["system"] + "\n\n" + old_prompt).encode()) <= manifest["policy"]["max_prompt_bytes"] - reserve,
             "initial quote attempt exceeded its recorded correction byte reserve")
    return original_node + ":quote-correction-1"
