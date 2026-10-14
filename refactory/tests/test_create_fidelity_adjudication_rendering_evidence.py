"""Lossless positive indexes must bind actual raw bodies, including old packets."""
import copy
import hashlib
import json

import pytest

from benchmarks.create_fidelity.bounded_context_evidence import verify_bounded_context
from test_create_fidelity_adjudication_evidence import run_actual_controller


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def rebind_packet(root, entry, packet):
    old = (root / entry["input_packet"]["snapshot"]).read_bytes()
    new = canonical(packet)
    (root / entry["input_packet"]["snapshot"]).write_bytes(new)
    entry["input_packet"].update(bytes=len(new), sha256=hashlib.sha256(new).hexdigest())
    before = (root / entry["supplied_prompt"]["snapshot"]).read_bytes()
    assert old in before
    supplied = before.replace(old, new, 1)
    delivery = json.loads((root / entry["delivery_receipt"]).read_text())
    for name in (entry["supplied_prompt"]["snapshot"], delivery["contexts"][0]["prompt"]):
        (root / name).write_bytes(supplied)
    entry["supplied_prompt"].update(bytes=len(supplied), sha256=hashlib.sha256(supplied).hexdigest())
    name = entry["worker_receipts"][0]
    worker = json.loads((root / name).read_text())
    task = (entry["system"] + "\n\n").encode() + supplied
    worker["prompt_transport"].update(bytes=len(task), sha256=hashlib.sha256(task).hexdigest())
    (root / name).write_text(json.dumps(worker))
    entry["worker_receipt_sha256"][name] = hashlib.sha256((root / name).read_bytes()).hexdigest()


def test_real_recursive_rendering_keeps_raw_negative_content_and_complete_positive_indexes(tmp_path, monkeypatch):
    name, manifest, args = run_actual_controller(tmp_path, monkeypatch)
    refs = verify_bounded_context(tmp_path, name, **args)
    saw_leaf = saw_aggregate = False
    for entry in manifest["aggregations"]:
        packet = json.loads((tmp_path / entry["input_packet"]["snapshot"]).read_text())
        assert packet["input_rendering"] == entry["input_rendering"] == "positive-findings-json-pointers/v1"
        for record in packet["records"]:
            raw = record["raw_response"]
            assert hashlib.sha256(raw.encode()).hexdigest() == record["input_sha256"]
            value = json.loads(raw)
            group = packet["positive_findings"][record["input_id"]]
            for stable, pointer in group["findings"].items():
                array, index = pointer["json_pointer"].split("/")[1:]
                finding = value[array][int(index)]
                if value["scope"] == "page_only":
                    saw_leaf = True
                    assert pointer["kind"] in {"claim", "coverage"}
                    assert finding.get("verdict", finding.get("status")) in {"supported", "evidence"}
                    assert group["source"]["page_id"] == record["input_id"]
                    assert stable.endswith(finding.get("claim_id", finding.get("obligation_id")))
                else:
                    saw_aggregate = True
                    assert set(pointer) == {"json_pointer"} and "source" not in group
                    assert stable.endswith(finding["decision_id"])
        assert all(item["status"] in {"adverse", "unresolved", "not_in_page"} for item in packet["retained_findings"])
    assert saw_leaf and saw_aggregate and any("aggregate-" in ref for ref in refs)


def test_absent_version_retains_original_full_positive_body_packet_path(tmp_path, monkeypatch):
    import refactory_dsh.create_fidelity_context as runtime
    import test_create_fidelity_adjudication_evidence as fixture
    from test_create_fidelity_adjudication import AdjudicationResponses
    class OldResponses(AdjudicationResponses):
        def complete(self, client, prompt, *, system="", workdir=None):
            if "SCOPED ADJUDICATION REQUEST:\n" not in prompt:
                return super().complete(client, prompt, system=system, workdir=workdir)
            task = (system + "\n\n" + prompt if system else prompt).encode()
            worker = json.loads(client._worker_receipt.read_text())
            worker.update(actual_route={**client.route(), "source": "runtime-request"}, prompt_transport={
                "kind": "stdin_file", "encoding": "utf-8", "bytes": len(task), "sha256": hashlib.sha256(task).hexdigest()})
            client._worker_receipt.write_text(json.dumps(worker))
            request = json.JSONDecoder().raw_decode(prompt.split("SCOPED ADJUDICATION REQUEST:\n", 1)[1])[0]
            packet = json.JSONDecoder().raw_decode(prompt.split("COMPLETE PRIOR MODEL OUTPUTS:\n", 1)[1])[0]
            sources = [{"input_id": identifier, "finding_ids": list(group)}
                       for identifier, group in packet["positive_findings"].items() if group]
            return canonical({**request, "scope": "scoped_adjudication", "retained_findings": packet["retained_findings"],
                "decisions": [{"decision_id": "positive", "conclusion": "Scoped old positive findings retain every source.",
                    "obligation_ids": [], "sources": sources}] if sources else []}).decode()
    monkeypatch.setattr(fixture, "AdjudicationResponses", OldResponses)
    # Produce real old-shaped model inputs/receipts without rewriting their
    # later immutable evidence or granting them the new rendering identifier.
    monkeypatch.setattr(runtime, "render_adjudication_inputs", lambda value: value)
    original = runtime._Session.adjudicate
    def old_dispatch(self, ids, round_number):
        result = original(self, ids, round_number)
        self.record["aggregations"][-1].pop("input_rendering")
        self.flush()
        return result
    monkeypatch.setattr(runtime._Session, "adjudicate", old_dispatch)
    name, manifest, args = run_actual_controller(tmp_path, monkeypatch)
    assert all("input_rendering" not in entry for entry in manifest["aggregations"])
    verify_bounded_context(tmp_path, name, **args)
    entry = manifest["aggregations"][0]
    packet = json.loads((tmp_path / entry["input_packet"]["snapshot"]).read_text())
    assert "input_rendering" not in packet
    assert any("finding" in content for group in packet["positive_findings"].values() for content in group.values())


@pytest.mark.parametrize("problem", ["unknown-version", "missing-entry-version", "missing-finding", "wrong-pointer",
    "wrong-kind", "changed-source", "unknown-finding", "negative-as-positive", "raw-summary", "softened-retained"])
def test_rebound_compact_packets_cannot_omit_or_invent_any_original_evidence(tmp_path, monkeypatch, problem):
    name, manifest, args = run_actual_controller(tmp_path, monkeypatch)
    entry = manifest["aggregations"][0]
    packet = json.loads((tmp_path / entry["input_packet"]["snapshot"]).read_text())
    group = next(value for value in packet["positive_findings"].values() if value["findings"])
    stable = next(iter(group["findings"]))
    if problem == "unknown-version":
        entry["input_rendering"] = packet["input_rendering"] = "positive-summaries/v999"
    elif problem == "missing-entry-version":
        entry.pop("input_rendering")
    elif problem == "missing-finding":
        group["findings"].pop(stable)
    elif problem == "wrong-pointer":
        group["findings"][stable]["json_pointer"] = "/claims/999"
    elif problem == "wrong-kind":
        group["findings"][stable]["kind"] = "coverage"
    elif problem == "changed-source":
        group["source"]["document_sha256"] = "0" * 64
    elif problem == "unknown-finding":
        group["findings"]["invented:claim:unknown"] = copy.deepcopy(group["findings"][stable])
    elif problem == "negative-as-positive":
        negative = packet["retained_findings"][0]
        source = negative["content"]
        target = packet["positive_findings"][source["page_id"]]
        raw = next(record["raw_response"] for record in packet["records"] if record["input_id"] == source["page_id"])
        array = "claims" if source["kind"] == "claim" else "coverage"
        index = json.loads(raw)[array].index(source["finding"])
        target["findings"][negative["finding_id"]] = {"kind": source["kind"], "json_pointer": f"/{array}/{index}"}
    elif problem == "raw-summary":
        packet["records"][0]["raw_response"] = '{"summary":"complete original report reviewed"}'
        packet["records"][0]["input_sha256"] = hashlib.sha256(packet["records"][0]["raw_response"].encode()).hexdigest()
    else:
        packet["retained_findings"][0]["content"]["finding"]["reason"] = "Softened mandatory adverse finding."
    rebind_packet(tmp_path, entry, packet)
    (tmp_path / name).write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="packet|rendering"):
        verify_bounded_context(tmp_path, name, **args)
