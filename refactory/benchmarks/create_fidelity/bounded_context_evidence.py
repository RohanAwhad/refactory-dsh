"""Independent inspection of versioned, raw bounded-context read evidence.

This module does not call production validators: asserted manifests, filenames,
digests and model summaries are not proof that document bytes reached a model.
Legacy direct-delivery evidence remains governed by its existing checks.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
import re
import stat
from typing import Any, Mapping, Sequence


PROTOCOL = "create-fidelity-full-context/v1"


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _path(root: Path, name: Any) -> Path:
    _require(isinstance(name, str) and bool(name) and not Path(name).is_absolute()
             and ".." not in Path(name).parts, "context evidence path must remain relative to its run")
    path = root / name
    _require(path.resolve().is_relative_to(root.resolve()), "context evidence path escapes its run")
    _require(not any(part.is_symlink() for part in (path, *path.parents)
                     if part.is_relative_to(root)), "context evidence cannot use symlinks")
    return path


def _bytes(root: Path, name: Any) -> bytes:
    path = _path(root, name)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as handle:
        _require(stat.S_ISREG(os.fstat(handle.fileno()).st_mode), "context evidence must be a regular file")
        return handle.read()


def _text(root: Path, name: Any) -> str:
    return _bytes(root, name).decode("utf-8")


def _record(root: Path, name: Any) -> dict:
    value = json.loads(_text(root, name))
    _require(type(value) is dict, "context evidence record must be an object")
    return value


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical(value: Any) -> str:
    return _sha(json.dumps(value, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":")).encode("utf-8"))


def _supplied(prompt: str, body: str) -> bool:
    return body in prompt or any(json.dumps(body, ensure_ascii=ascii_only) in prompt
                                 for ascii_only in (False, True))


def _dispatch(root: Path, *, node: str, role: str, requested_route: dict,
              worker_path: str, delivery_path: str, prompt_path: str,
              response_path: str, system: str, max_prompt_bytes: int) -> tuple[str, bytes, list[str]]:
    """Check a real dispatch, independently of the manifest's self-reported state."""
    worker, delivery = _record(root, worker_path), _record(root, delivery_path)
    _require(worker.get("node_id") == node and worker.get("role") == role
             and worker.get("state") == "completed" and worker.get("run_id") == str(root)
             and worker.get("cwd") == str(root) and not worker.get("native_observations"),
             "context reader did not complete the original DSH role in this run")
    actual, requested = worker.get("actual_route"), worker.get("requested_route")
    _require(type(actual) is dict and type(requested) is dict
             and requested == requested_route and requested.get("worker_backend") == "dsh"
             and actual.get("worker_backend") == "dsh" and actual.get("source") == "runtime-request"
             and all(requested.get(key) and requested[key] == actual.get(key)
                     for key in ("provider", "model", "effort")),
             "context reader served a different logical owner route")
    _require(delivery.get("node") == node and delivery.get("status") == "delivered"
             and not delivery.get("missing_or_unchanged")
             and delivery.get("execution", {}).get("node") == node,
             "context reader delivery is incomplete, stale or belongs to another dispatch")
    times = [datetime.fromisoformat(record[key]) for record, key in
             ((delivery, "started"), (worker, "started"), (worker, "finished"), (delivery, "finished"))]
    _require(times == sorted(times), "context reader worker falls outside its actual delivery interval")
    contexts = delivery.get("contexts")
    _require(type(contexts) is list and len(contexts) == 1
             and isinstance(contexts[0].get("prompt"), str),
             "context reader omitted or substituted its actual supplied prompt")
    prompt_bytes = _bytes(root, prompt_path)
    actual_prompt_path = contexts[0]["prompt"]
    _require(_bytes(root, actual_prompt_path) == prompt_bytes,
             "context prompt snapshot differs from the actual delivered model input")
    _require(type(system) is str, "context reader system prefix must be explicit")
    transport = (system.encode() + b"\n\n" if system else b"") + prompt_bytes
    receipt = worker.get("prompt_transport", {})
    _require(receipt.get("kind") in {"argv", "stdin_file"} and receipt.get("encoding") == "utf-8"
             and type(receipt.get("bytes")) is int and receipt["bytes"] == len(transport)
             and receipt.get("sha256") == _sha(transport),
             "context reader prompt bytes do not match actual worker transport")
    _require(type(max_prompt_bytes) is int and max_prompt_bytes > 0
             and len(transport) <= max_prompt_bytes,
             "context reader exceeded its declared UTF-8 transport ceiling")
    response = _bytes(root, response_path)
    artifact = delivery.get("artifacts", {}).get(response_path)
    if type(artifact) is dict:
        info = _path(root, response_path).stat()
        _require(artifact.get("kind") == "file" and artifact.get("sha256") == _sha(response)
                 and all(artifact.get(key) == value for key, value in
                         {"bytes": len(response), "mtime_ns": info.st_mtime_ns,
                          "ctime_ns": info.st_ctime_ns, "inode": info.st_ino}.items()),
                 "context reader output differs from its actual delivered artifact")
    else:
        final_path = delivery.get("final_response")
        _require(isinstance(final_path, str) and _bytes(root, final_path) == response,
                 "context reader response has no actual delivered output binding")
    _require(type(delivery.get("execution", {}).get("input_fingerprints")) is dict,
             "context reader delivery omitted raw input fingerprints")
    return prompt_bytes.decode("utf-8"), response, [worker_path, delivery_path, prompt_path,
                                                  actual_prompt_path, response_path]


def verify_bounded_context(root: Path, manifest_path: str, *, consumer_node: str,
                           role: str, role_text: str, stage_text: str = "",
                           required_documents: Mapping[str, bytes | str] | None = None,
                           required_bodies: Sequence[str] = (), historical: bool = False,
                           historical_inputs: Mapping[str, str] | None = None) -> list[str]:
    """Prove complete current bytes reached the original role across real reads.

    This establishes transport/range consumption and a model-uttered final
    binding, never the semantic correctness of observations or acceptance.
    Callers supply the source-pinned role/stage and current required bodies;
    the manifest cannot choose or waive the expected logical owner.
    """
    root = Path(root).resolve()
    _require(type(historical) is bool and (not historical or bool(historical_inputs)),
             "historical context requires independently captured original input digests")
    manifest = _record(root, manifest_path)
    _require(type(manifest.get("schema_version")) is int and manifest["schema_version"] == 1
             and manifest.get("protocol") == PROTOCOL and manifest.get("status") == "reconciled",
             "bounded context is not a reconciled versioned protocol")
    _require(manifest.get("logical_node_id") == consumer_node and manifest.get("role") == role
             and isinstance(manifest.get("context_id"), str)
             and bool(re.fullmatch(r"[a-f0-9]{32}", manifest["context_id"])),
             "bounded context belongs to another original role or logical consumer")
    requested = manifest.get("requested_route")
    _require(type(requested) is dict and requested.get("worker_backend") == "dsh",
             "bounded context did not retain the original DSH route")
    policy = manifest.get("policy", {})
    _require(type(policy) is dict and policy.get("accounting") == "configured_utf8_byte_ceiling"
             and type(policy.get("max_prompt_bytes")) is int and policy["max_prompt_bytes"] > 0
             and all(type(policy.get(key)) is int and policy[key] > 0
                     for key in ("runtime_reserve_tokens", "output_reserve_tokens")),
             "bounded context omitted its explicit configured ceiling and reserves")
    _require(manifest.get("deadline_scope") == "one absolute scope across reads and final call",
             "bounded context omitted the original shared stage scope")
    refs = [manifest_path]

    def snapshot(item: Any, *, page: bool = False) -> bytes:
        _require(type(item) is dict, "context snapshot metadata must be typed")
        data = _bytes(root, item.get("snapshot"))
        data.decode("utf-8")
        _require(type(item.get("bytes")) is int and item["bytes"] == len(data)
                 and item.get("sha256") == _sha(data)
                 and (not page or item.get("page_sha256") == _sha(data)),
                 "immutable context snapshot bytes differ from asserted digest/size")
        refs.append(item["snapshot"])
        return data

    instructions = snapshot(manifest.get("instructions"))
    instruction_text = instructions.decode()
    _require(bool(role_text) and role_text in instruction_text and (not stage_text or stage_text in instruction_text),
             "bounded context omitted source-pinned original role/stage instructions")
    groups = {key: manifest.get(key) for key in ("fixed_documents", "documents", "pages", "reviews", "obligations")}
    _require(all(type(value) is list for value in groups.values()), "bounded context arrays are missing or untyped")
    obligations = groups["obligations"]
    _require(bool(obligations) and all(type(item) is dict and isinstance(item.get("id"), str)
             and bool(item["id"].strip()) and isinstance(item.get("text"), str) and bool(item["text"].strip())
             for item in obligations) and len({item["id"] for item in obligations}) == len(obligations),
             "bounded context omitted complete typed review obligations")
    obligation_ids = {item["id"] for item in obligations}
    required = {name: body.encode() if isinstance(body, str) else body
                for name, body in (required_documents or {}).items()}
    source_bytes: dict[str, bytes] = {}
    fixed: dict[str, bytes] = {}
    documents: dict[tuple[str, str], tuple[dict, bytes]] = {}

    def plan_universe(item: dict, body: bytes) -> bool:
        provenance = item.get("provenance")
        if not isinstance(provenance, dict) or provenance.get("kind") != "derived_plan_universe":
            return False
        stage = consumer_node.split(":", 1)[0]
        plans = {"research_director": ".factory/strategy/research-plan.json",
                 "strategy_director": ".factory/strategy/strategy-plan.json",
                 "qa_director": ".factory/reviews/qa-plan.json"}
        source = provenance.get("source_path")
        _require(":quality:" in consumer_node and plans.get(stage) == source
                 and provenance.get("projection") == "quality-decision-universe/v1",
                 "fixed decision universe has no original typed-plan provenance")
        raw = _bytes(root, source)
        _require(_sha(raw) == provenance.get("source_sha256"), "fixed universe source plan is stale")
        plan = json.loads(raw)
        projection = {"tasks": [{key: task[key] for key in ("id", "role", "focus", "output", "mandatory", "coverage")}
                                for task in plan["tasks"]],
                      "intent_items": plan["intent_items"], "coverage_items": plan["coverage_items"]}
        _require(json.dumps(projection, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode() == body,
                 "fixed decision universe changed original tasks or full requirement metadata")
        source_bytes[source] = raw
        refs.append(source)
        return True

    for item in groups["fixed_documents"]:
        _require(type(item) is dict, "fixed context document must be typed")
        name = item.get("path")
        _require(isinstance(name, str) and name not in fixed, "bounded context duplicated fixed authority")
        body = snapshot(item)
        if plan_universe(item, body):
            fixed[name] = body
            continue
        _require(name in (".factory/strategy/user-intent.md", ".factory/strategy/current.md"),
                 "bounded context substituted fixed authority without reviewed derivation")
        if not historical:
            _require(_bytes(root, name) == body, "bounded context fixed authority is stale")
        elif name in historical_inputs:
            _require(_sha(body) == historical_inputs[name], "historical fixed authority differs from original raw input")
        fixed[name] = source_bytes[name] = body
        refs.append(name)
    for name in (".factory/strategy/user-intent.md", ".factory/strategy/current.md"):
        if not historical and _path(root, name).is_file():
            _require(name in fixed, "bounded context dropped complete available authority")
    for item in groups["documents"]:
        _require(type(item) is dict, "context document must be typed")
        body = snapshot(item)
        name, source = item.get("path"), item.get("source_path")
        _require(isinstance(name, str) and bool(name) and type(body) is bytes,
                 "bounded context document identity is invalid")
        if source is not None:
            _require(type(source) is str, "bounded context source identity is invalid")
            if not historical:
                _require(_bytes(root, source) == body, "bounded context document changed from its current source")
            elif source in historical_inputs:
                _require(_sha(body) == historical_inputs[source], "historical document differs from original raw input")
            source_bytes[source] = body
            refs.append(source)
        else:
            _require(plan_universe(item, body) or (name in required and required[name] == body),
                     "derived context document has no independently reconstructed current bytes")
            if item.get("source_kind") == "derived_gate_request":
                provenance = item.get("provenance")
                _require(type(provenance) is dict and provenance.get("kind") == "derived_gate_request"
                         and provenance.get("gate_id") == consumer_node and name == "runtime-gate-evidence.json",
                         "derived gate request has no original gate provenance")
                parent = _record(root, provenance.get("parent_receipt"))
                _require(parent.get("gate_id") == consumer_node and parent.get("owner") == "agent"
                         and parent.get("requested_route") == requested
                         and parent.get("input_digest") == provenance.get("input_digest")
                         and parent.get("evidence_digest") == provenance.get("evidence_digest")
                         and parent.get("guard_problems", []) == provenance.get("guard_problems"),
                         "derived gate request differs from its actual parent verdict evidence")
                expected = {"schema_version": 1, "gate_id": consumer_node,
                            "input_digest": parent["input_digest"], "evidence_digest": parent["evidence_digest"],
                            "verdict_labels": provenance.get("verdict_labels"),
                            "guard_problems": parent.get("guard_problems", []), "evidence": parent.get("evidence")}
                _require(json.dumps(expected, indent=2).encode() == body,
                         "derived gate request bytes were not reconstructed from original evidence")
                refs.append(provenance["parent_receipt"])
        if name in required:
            _require(required[name] == body, "bounded context supplied stale required document")
        key = (name, item["snapshot"])
        _require(key not in documents, "bounded context duplicated a document snapshot")
        documents[key] = (item, body)
    for name, body in required.items():
        _require(fixed.get(name) == body or any(item["path"] == name and data == body
                 for item, data in documents.values()), "bounded context dropped a required complete input")
    rendering = manifest.get("obligation_rendering")
    _require(type(rendering) is list and len(rendering) == len(obligations),
             "bounded context omitted lossless obligation rendering")
    for original, rendered in zip(obligations, rendering):
        if rendered == original:
            continue
        _require(type(rendered) is dict and set(rendered) == {"id", "text_reference", "source"}
                 and rendered.get("id") == original["id"]
                 and rendered["source"] == "Exact complete obligation dict appears in the fixed authoritative universe above.",
                 "bounded context replaced unique obligation text with an unsupported reference")
        match = re.fullmatch(r"(.+)#/coverage_items/([0-9]+)", rendered.get("text_reference", ""))
        _require(match is not None and match[1] in fixed, "bounded obligation reference has no complete fixed authority")
        universe = json.loads(fixed[match[1]])
        index = int(match[2])
        _require(index < len(universe["coverage_items"]) and universe["coverage_items"][index] == original,
                 "bounded obligation reference changed original requirement metadata")
    if historical:
        _require(all(name in source_bytes and _sha(source_bytes[name]) == digest
                     for name, digest in historical_inputs.items()),
                 "historical context omitted or forged an original input snapshot")
    bodies = [data.decode() for _, data in documents.values()] + [data.decode() for data in fixed.values()]
    _require(all(any(_supplied(body, required_body) for body in bodies) for required_body in required_bodies),
             "bounded context documents omit required original full report bodies")
    pages_by_document: dict[tuple[str, str], list[dict]] = {key: [] for key in documents}
    pages_by_id = {}
    for page in groups["pages"]:
        _require(type(page) is dict and isinstance(page.get("page_id"), str)
                 and bool(re.fullmatch(r"page-[0-9]{4,}", page["page_id"])) and page["page_id"] not in pages_by_id,
                 "bounded context has duplicate or invalid page identity")
        key = (page.get("path"), page.get("document_snapshot"))
        _require(key in documents, "context page names a substituted document")
        item, body = documents[key]
        start, end = page.get("start_byte"), page.get("end_byte")
        _require(type(start) is int and type(end) is int and 0 <= start < end <= len(body)
                 and page.get("document_sha256") == _sha(body) and snapshot(page, page=True) == body[start:end],
                 "context page is not the exact original UTF-8 range")
        pages_by_document[key].append(page)
        pages_by_id[page["page_id"]] = page
    for key, (item, body) in documents.items():
        pages = pages_by_document[key]
        if item.get("reviewed_as") == "complete fixed authority on every read and final call":
            _require(fixed.get(item["path"]) == body and not pages,
                     "fixed context document bypassed actual complete authority reads")
            continue
        _require(item.get("reviewed_as") == "complete byte ranges", "context document has unknown read semantics")
        cursor = 0
        for page in sorted(pages, key=lambda value: value["start_byte"]):
            _require(page["start_byte"] == cursor, "context ranges have a gap or overlap")
            cursor = page["end_byte"]
        _require(cursor == len(body), "context ranges did not cover the entire original document")

    application_baseline: bytes | None = None

    def dispatch(entry: dict, node: str) -> tuple[str, bytes]:
        nonlocal application_baseline
        _require(entry.get("logical_node_id") == consumer_node and entry.get("role") == role
                 and type(entry.get("worker_receipts")) is list and len(entry["worker_receipts"]) == 1,
                 "context read/final lacks one bound original-role worker")
        prompt_data = snapshot(entry.get("supplied_prompt"))
        response_data = snapshot(entry.get("raw_response"))
        prompt, response, evidence = _dispatch(root, node=node, role=role, requested_route=requested,
            worker_path=entry["worker_receipts"][0], delivery_path=entry["delivery_receipt"],
            prompt_path=entry["supplied_prompt"]["snapshot"], response_path=entry["raw_response"]["snapshot"],
            system=entry.get("system"), max_prompt_bytes=policy["max_prompt_bytes"])
        _require(prompt.encode() == prompt_data and response == response_data, "context dispatch snapshot changed")
        _require(instruction_text in prompt and all(data.decode() in prompt for data in fixed.values()),
                 "actual context reader/final omitted full original instructions or authority")
        rendered = json.dumps(rendering, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        _require("COMPLETE REVIEW OBLIGATIONS:\n" + rendered in prompt,
                 "actual context reader/final omitted complete lossless review obligations")
        if node != consumer_node:
            before, after = snapshot(entry.get("application_before")), snapshot(entry.get("application_after"))
            _require(before == after and (application_baseline is None or before == application_baseline),
                     "evidence-only dispatch recorded persistent application drift across its read scope")
            application_baseline = before
        refs.extend(evidence)
        return prompt, response

    seen_pages = set()
    for entry in groups["reviews"]:
        _require(type(entry) is dict and entry.get("status") == "reviewed", "context part did not complete a validated read")
        page_id = entry.get("page_id")
        _require(page_id in pages_by_id and page_id not in seen_pages, "context read duplicated or omitted a page")
        seen_pages.add(page_id)
        page = pages_by_id[page_id]
        node = f"{consumer_node}:context:{manifest['context_id']}:{page_id}"
        from .bounded_quote_evidence import validate_page_read, verify_quote_attempt
        if "quote_correction" in entry:
            node = verify_quote_attempt(root, manifest, entry, page, dispatch=dispatch, snapshot=snapshot,
                                        obligation_ids=obligation_ids, source_bytes=source_bytes)
        _require(entry.get("execution_node_id") == node, "context read execution is bound to another logical owner")
        prompt, raw = dispatch(entry, node)
        _require(entry.get("worker_receipt_sha256") == {name: _sha(_bytes(root, name)) for name in entry["worker_receipts"]}
                 and entry.get("delivery_receipt_sha256") == _sha(_bytes(root, entry["delivery_receipt"])),
                 "context reader raw receipts changed from their captured read binding")
        page_data = _bytes(root, page["snapshot"])
        _require("=== EXACT EVIDENCE RANGE ===\n" + page_data.decode() + "\n=== END EXACT EVIDENCE RANGE ===" in prompt,
                 "actual model input omitted the complete assigned page bytes")
        _require(not validate_page_read(raw, entry, page, manifest, obligation_ids,
                                       _bytes(root, page["document_snapshot"])),
                 "actual read claims have fabricated, missing or out-of-page quotes")
        delivery = _record(root, entry["delivery_receipt"])
        _require(all(delivery["execution"]["input_fingerprints"].get(name) == _sha(data)
                     for name, data in source_bytes.items()), "context reader raw input fingerprints are stale/incomplete")
    _require(seen_pages == set(pages_by_id), "bounded context has missing actual completed page readers")
    from .bounded_adjudication_evidence import verify_adjudications
    root_records = verify_adjudications(root, manifest, snapshot=snapshot, dispatch=dispatch,
                                        source_bytes=source_bytes, obligation_ids=obligation_ids)
    basis = {key: manifest[key] for key in ("protocol", "context_id", "logical_node_id", "role", "requested_route", "policy", "deadline_scope",
             "instructions", "fixed_documents", "documents", "pages", "reviews", "aggregations", "frontier", "obligations", "obligation_rendering")}
    digest = _canonical(basis)
    _require(manifest.get("reconciliation_digest") == digest, "bounded context reconciliation digest is stale or forged")
    final = manifest.get("final")
    _require(type(final) is dict, "bounded context omitted its actual final consumer")
    prompt, response = dispatch(final, consumer_node)
    # Independently rebuild the lossless semantic projection. No production
    # final_packet import and no credit for an asserted packet hash alone.
    packet = {"protocol": manifest["protocol"], "context_id": manifest["context_id"],
              "logical_node_id": consumer_node, "role": role, "reconciliation_digest": digest,
              "documents": [{key: item[key] for key in ("path", "sha256", "bytes", "snapshot", "reviewed_as", "source_kind")}
                            for item in groups["documents"]],
              "pages": [{key: page[key] for key in ("page_id", "path", "document_sha256", "start_byte", "end_byte", "page_sha256")}
                        for page in groups["pages"]],
              "reviews": [{"input_id": item["input_id"], "raw_response_sha256": item["input_sha256"],
                           "raw_response": item["raw_response"]} for item in root_records]}
    packet_bytes = json.dumps(packet, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    _require(snapshot(manifest.get("final_packet")) == packet_bytes and _supplied(prompt, packet_bytes.decode()),
             "actual final consumer omitted or replaced complete original model-uttered read records")
    marker = "CONTEXT_RECONCILIATION_SHA256=" + digest
    markers = re.findall(r"CONTEXT_RECONCILIATION_SHA256=[a-f0-9]{64}", response.decode())
    _require(markers == [marker] and final.get("uttered_reconciliation_marker") == marker,
             "actual final model did not utter the exact complete current read-record binding")
    if historical:
        refs.append(manifest_path + "#scope=historical-repair-only")
    return sorted(set(refs))
