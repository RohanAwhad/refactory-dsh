"""Independent graph and preservation checks for same-role adjudication.

All preceding responses must actually reach each aggregate. A scoped model
conclusion is data, never independent proof that the conclusion is correct.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from .bounded_context_evidence import _bytes, _require, _sha, _supplied


INPUT_RENDERING = "positive-findings-json-pointers/v1"
REFERENCE_POLICY = "per-decision-unique/v1"


def _render_inputs(expected: dict) -> dict:
    """Reconstruct the compact input independently from every actual raw body."""
    indexed, restored = {}, {}
    for record in expected["records"]:
        identifier = record["input_id"]
        value = json.loads(record["raw_response"])
        group, original = {"findings": {}}, {}
        if value["scope"] == "page_only":
            for kind, array, status_key, positive_status, id_key in (
                    ("claim", "claims", "verdict", "supported", "claim_id"),
                    ("coverage", "coverage", "status", "evidence", "obligation_id")):
                for index, finding in enumerate(value[array]):
                    if finding[status_key] != positive_status:
                        continue
                    stable = f"{identifier}:{kind}:{finding[id_key]}"
                    content = expected["positive_findings"][identifier][stable]
                    source = {key: content[key] for key in ("page_id", "path", "document_sha256")}
                    _require("source" not in group or group["source"] == source,
                             "compact input conflicts with original page provenance")
                    group["source"] = source
                    group["findings"][stable] = {"kind": kind, "json_pointer": f"/{array}/{index}"}
                    original[stable] = {"kind": kind, **source, "finding": finding}
        else:
            _require(value["scope"] == "scoped_adjudication", "unknown compact input source scope")
            for index, decision in enumerate(value["decisions"]):
                stable = f"{identifier}:decision:{decision['decision_id']}"
                group["findings"][stable] = {"json_pointer": f"/decisions/{index}"}
                original[stable] = decision
        indexed[identifier], restored[identifier] = group, original
    _require(restored == expected["positive_findings"],
             "compact positive input does not reconstruct every canonical original finding")
    return {**expected, "input_rendering": INPUT_RENDERING, "positive_findings": indexed}


def verify_adjudications(root: Path, manifest: dict, *, snapshot: Callable,
                         dispatch: Callable, source_bytes: dict[str, bytes],
                         obligation_ids: set[str]) -> list[dict]:
    reviews, pages = manifest["reviews"], {page["page_id"]: page for page in manifest["pages"]}
    aggregates, final_frontier = manifest.get("aggregations"), manifest.get("frontier")
    _require(type(aggregates) is list and type(final_frontier) is list,
             "bounded context omitted its adjudication graph/frontier")
    policy = manifest["policy"]
    _require(type(policy.get("max_adjudications")) is int and 0 < policy["max_adjudications"] <= 128
             and type(policy.get("max_adjudication_rounds")) is int
             and 0 < policy["max_adjudication_rounds"] <= 16
             and len(aggregates) <= policy["max_adjudications"],
             "adjudication exceeded its independent bounded node/round limits")
    records = {entry["page_id"]: entry for entry in reviews}
    rounds = {identifier: 0 for identifier in records}
    frontier = [entry["page_id"] for entry in reviews]
    last_round = 0

    def inputs(ids):
        raw_records, retained, positives = [], {}, {}
        for identifier in ids:
            entry = records[identifier]
            raw = snapshot(entry["raw_response"]).decode("utf-8")
            value = json.loads(raw)
            raw_records.append({"input_id": identifier, "input_sha256": _sha(raw.encode()), "raw_response": raw})
            positive = {}
            if value["scope"] == "page_only":
                page = pages[identifier]
                for kind, findings in (("claim", value["claims"]), ("coverage", value["coverage"])):
                    for finding in findings:
                        suffix = finding["claim_id"] if kind == "claim" else finding["obligation_id"]
                        status = finding["verdict"] if kind == "claim" else finding["status"]
                        stable = f"{identifier}:{kind}:{suffix}"
                        content = {"kind": kind, "page_id": identifier, "path": page["path"],
                                   "document_sha256": page["document_sha256"], "finding": finding}
                        if status in {"adverse", "unresolved", "not_in_page"}:
                            retained[stable] = {"finding_id": stable, "status": status, "content": content}
                        elif status in {"supported", "evidence"}:
                            positive[stable] = content
            else:
                _require(value["scope"] == "scoped_adjudication", "unknown prior adjudication output scope")
                for finding in value["retained_findings"]:
                    previous = retained.get(finding["finding_id"])
                    _require(previous is None or previous == finding, "conflicting adverse evidence identities")
                    retained[finding["finding_id"]] = finding
                for decision in value["decisions"]:
                    positive[f"{identifier}:decision:{decision['decision_id']}"] = decision
            positives[identifier] = positive
        return {"records": raw_records, "retained_findings": [retained[key] for key in sorted(retained)],
                "positive_findings": positives}

    for index, entry in enumerate(aggregates, 1):
        identifier = f"aggregate-{index:04d}"
        _require(type(entry) is dict and entry.get("aggregation_id") == identifier
                 and entry.get("status") == "reviewed", "adjudication lacks a completed uniquely ordered aggregate")
        ids = entry.get("input_ids")
        _require(type(ids) is list and len(ids) >= 2 and all(type(name) is str for name in ids)
                 and len(set(ids)) == len(ids) and all(name in frontier for name in ids),
                 "adjudication reused, duplicated, invented or omitted active input identities")
        locations = [frontier.index(name) for name in ids]
        _require(locations == list(range(locations[0], locations[0] + len(ids))),
                 "adjudication changed the source ordered input group")
        round_number = entry.get("round")
        _require(type(round_number) is int and last_round <= round_number <= policy["max_adjudication_rounds"]
                 and round_number > 0 and all(rounds[name] < round_number for name in ids),
                 "adjudication graph is cyclic or exceeds its scoped round bound")
        last_round = round_number
        expected = inputs(ids)
        hashes = {item["input_id"]: item["input_sha256"] for item in expected["records"]}
        _require(entry.get("input_sha256") == hashes, "adjudication input digests differ from actual prior responses")
        rendered = expected
        if "input_rendering" in entry:
            _require(entry["input_rendering"] == INPUT_RENDERING,
                     "adjudication declares an unsupported or retroactively upgraded input rendering")
            rendered = _render_inputs(expected)
        per_decision = "positive_reference_policy" in entry
        if per_decision:
            _require(entry["positive_reference_policy"] == REFERENCE_POLICY,
                     "adjudication declares an unsupported positive reference policy")
            rendered = {**rendered, "positive_reference_policy": REFERENCE_POLICY}
        packet = json.dumps(rendered, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        _require(snapshot(entry.get("input_packet")) == packet, "adjudication packet replaced or dropped actual full prior outputs")
        node = f"{manifest['logical_node_id']}:context:{manifest['context_id']}:{identifier}"
        _require(entry.get("execution_node_id") == node, "adjudication changed original logical ownership")
        prompt, raw = dispatch(entry, node)
        _require(_supplied(prompt, packet.decode()), "actual adjudication model did not receive every complete prior output")
        _require(entry.get("worker_receipt_sha256") == {name: _sha(_bytes(root, name)) for name in entry["worker_receipts"]}
                 and entry.get("delivery_receipt_sha256") == _sha(_bytes(root, entry["delivery_receipt"])),
                 "adjudication raw worker/delivery changed from captured original evidence")
        delivery = json.loads(_bytes(root, entry["delivery_receipt"]))
        if per_decision:
            _require(delivery["execution"]["input_fingerprints"].get(entry["input_packet"]["snapshot"])
                     == entry["input_packet"]["sha256"],
                     "fresh adjudication omitted or changed its declared complete input packet fingerprint")
        _require(all(delivery["execution"]["input_fingerprints"].get(name) == _sha(data)
                     for name, data in source_bytes.items()), "adjudication omitted or changed original raw source inputs")
        _require(snapshot(entry.get("application_before")) == snapshot(entry.get("application_after")),
                 "adjudication recorded application drift")
        value = json.loads(raw)
        keys = {"schema_version", "scope", "context_id", "logical_node_id", "role", "aggregation_id",
                "input_ids", "input_sha256", "retained_findings", "decisions"}
        _require(type(value) is dict and set(value) == keys and type(value.get("schema_version")) is int
                 and value["schema_version"] == 1 and value["scope"] == "scoped_adjudication"
                 and value == entry.get("review"), "adjudication is not its actual typed model-uttered output")
        expected_identity = {"context_id": manifest["context_id"], "logical_node_id": manifest["logical_node_id"],
                             "role": manifest["role"], "aggregation_id": identifier, "input_ids": ids,
                             "input_sha256": hashes}
        _require(all(value[key] == expected_value for key, expected_value in expected_identity.items()),
                 "adjudication uttered another input group or owner identity")
        _require(value["retained_findings"] == expected["retained_findings"],
                 "adjudication softened, changed or dropped adverse/unresolved complete semantic content")
        required = {(name, finding) for name, findings in expected["positive_findings"].items() for finding in findings}
        decisions, seen, decision_ids = value["decisions"], set(), set()
        _require(type(decisions) is list, "adjudication scoped decisions must be typed")
        for decision in decisions:
            _require(type(decision) is dict and set(decision) == {"decision_id", "conclusion", "obligation_ids", "sources"}
                     and type(decision["decision_id"]) is str and bool(decision["decision_id"].strip())
                     and decision["decision_id"] not in decision_ids and type(decision["conclusion"]) is str
                     and bool(decision["conclusion"].strip()) and type(decision["obligation_ids"]) is list
                     and all(type(name) is str for name in decision["obligation_ids"])
                     and len(set(decision["obligation_ids"])) == len(decision["obligation_ids"])
                     and set(decision["obligation_ids"]) <= obligation_ids
                     and type(decision["sources"]) is list and bool(decision["sources"]),
                     "adjudication emitted invalid or out-of-scope decision identities")
            decision_ids.add(decision["decision_id"])
            decision_seen = set()
            for source in decision["sources"]:
                _require(type(source) is dict and set(source) == {"input_id", "finding_ids"}
                         and type(source["input_id"]) is str and type(source["finding_ids"]) is list
                         and bool(source["finding_ids"]) and all(type(name) is str for name in source["finding_ids"]),
                         "adjudication lacks exact positive input references")
                for finding in source["finding_ids"]:
                    pair = (source["input_id"], finding)
                    _require(pair in required and pair not in (decision_seen if per_decision else seen),
                             "adjudication invented or duplicated positive evidence")
                    decision_seen.add(pair)
                    seen.add(pair)
        _require(seen == required, "adjudication dropped positive input evidence without source disposition")
        input_size = sum(len(item["raw_response"].encode()) for item in expected["records"])
        _require(type(entry.get("input_response_bytes")) is int and entry["input_response_bytes"] == input_size
                 and type(entry.get("output_response_bytes")) is int and entry["output_response_bytes"] == len(raw)
                 and len(raw) < input_size, "adjudication made no independently verified size progress")
        frontier[locations[0]:locations[0] + len(ids)] = [identifier]
        records[identifier] = entry
        rounds[identifier] = round_number
    _require(final_frontier == frontier, "final adjudication frontier omitted, duplicated or invented preceding records")
    return inputs(frontier)["records"]
