"""Complete actual model outputs survive duplicate-positive rendering removal."""
import hashlib
import json

from refactory_dsh.create_fidelity_context import adjudication_inputs, render_adjudication_inputs
from test_create_fidelity_adjudication import run_adjudication


def expand_positive_packet(packet):
    raw = {item["input_id"]: json.loads(item["raw_response"]) for item in packet["records"]}
    expanded = {}
    for identifier, group in packet["positive_findings"].items():
        expanded[identifier] = {}
        for stable_id, pointer in group["findings"].items():
            _, array, index = pointer["json_pointer"].split("/")
            finding = raw[identifier][array][int(index)]
            expanded[identifier][stable_id] = (
                {**group["source"], "kind": pointer["kind"], "finding": finding}
                if "source" in group else finding)
    return expanded


def test_actual_adjudications_receive_all_raw_outputs_and_exact_positive_source_map(tmp_path, monkeypatch):
    result, manifest, responses, _ = run_adjudication(tmp_path, monkeypatch)
    assert result.exit_ok, result.error
    assert any(item["round"] > 1 for item in manifest["aggregations"])
    for entry, (_, packet) in zip(manifest["aggregations"], responses.aggregate_calls, strict=True):
        expected = adjudication_inputs(manifest, entry["input_ids"], tmp_path)
        assert entry["input_rendering"] == packet["input_rendering"] == "positive-findings-json-pointers/v1"
        assert packet["records"] == expected["records"]
        assert packet["retained_findings"] == expected["retained_findings"]
        assert expand_positive_packet(packet) == expected["positive_findings"]
        assert json.loads((tmp_path / entry["input_packet"]["snapshot"]).read_text()) == packet
        delivered = json.loads((tmp_path / entry["delivery_receipt"]).read_text())
        prompt = (tmp_path / delivered["contexts"][0]["prompt"]).read_text()
        actual_packet = json.JSONDecoder().raw_decode(prompt.split("COMPLETE PRIOR MODEL OUTPUTS:\n", 1)[1])[0]
        assert actual_packet == packet
        for item in actual_packet["records"]:
            assert hashlib.sha256(item["raw_response"].encode()).hexdigest() == item["input_sha256"]


def test_positive_coverage_and_empty_groups_reconstruct_without_losing_provenance():
    claim = {"claim_id": "c1", "verdict": "supported", "reason": "Full café🙂 observation.",
             "quote": "café🙂", "start_byte": 10, "end_byte": 19}
    coverage = {"obligation_id": "original", "status": "evidence", "reason": "Complete scoped coverage observation."}
    value = {"scope": "page_only", "claims": [claim], "coverage": [coverage]}
    raw = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    source = {"page_id": "page-0001", "path": "original/完整.md", "document_sha256": "a" * 64}
    full = {"records": [{"input_id": "page-0001", "input_sha256": hashlib.sha256(raw.encode()).hexdigest(), "raw_response": raw},
                        {"input_id": "aggregate-0001", "input_sha256": "b" * 64,
                         "raw_response": '{"scope":"scoped_adjudication","decisions":[]}'}],
            "retained_findings": [{"finding_id": "retained", "status": "adverse", "content": {"reason": "Keep every exact field."}}],
            "positive_findings": {"page-0001": {
                "page-0001:claim:c1": {**source, "kind": "claim", "finding": claim},
                "page-0001:coverage:original": {**source, "kind": "coverage", "finding": coverage}}, "aggregate-0001": {}}}
    original = json.dumps(full, ensure_ascii=False)
    packet = render_adjudication_inputs(full)
    assert json.dumps(full, ensure_ascii=False) == original
    assert packet["records"] == full["records"] and packet["retained_findings"] == full["retained_findings"]
    assert expand_positive_packet(packet) == full["positive_findings"]
    assert packet["positive_findings"]["page-0001"]["source"] == source
    assert packet["positive_findings"]["aggregate-0001"] == {"findings": {}}
