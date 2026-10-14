"""One actual source may support distinct conclusions without adding evidence."""
import copy
import hashlib
import json

import pytest

from refactory_dsh.create_fidelity_context import POSITIVE_REFERENCE_POLICY
from test_create_fidelity_adjudication import AdjudicationResponses, run_adjudication


@pytest.mark.parametrize("case", ["shared-support", "same-decision", "split-source-object",
                                  "missing-with-reuse", "unknown-reference", "packet-drift"])
def test_actual_adjudication_distinguishes_shared_support_from_duplicate_evidence(tmp_path, monkeypatch, case):
    original_complete = AdjudicationResponses.complete

    def respond(self, client, prompt, **kwargs):
        raw = original_complete(self, client, prompt, **kwargs)
        if "SCOPED ADJUDICATION REQUEST:\n" not in prompt:
            return raw
        packet_ref = json.JSONDecoder().raw_decode(prompt.split("READ-ONLY AUDIT PACKET SNAPSHOT:\n", 1)[1])[0]
        packet_path = tmp_path / packet_ref["snapshot"]
        packet_bytes = packet_path.read_bytes()
        assert hashlib.sha256(packet_bytes).hexdigest() == packet_ref["sha256"]
        supplied = json.JSONDecoder().raw_decode(prompt.split("COMPLETE PRIOR MODEL OUTPUTS:\n", 1)[1])[0]
        assert json.loads(packet_bytes) == supplied
        if case == "packet-drift":
            packet_path.write_text('"changed after delivery"')
            return raw
        value = json.loads(raw)
        if not value["decisions"]:
            return raw
        first = value["decisions"][0]
        source = first["sources"][0]
        shared = {"input_id": source["input_id"], "finding_ids": [source["finding_ids"][0]]}
        second = {"decision_id": "separate-supported-facet",
                  "conclusion": "The same source also establishes this scoped facet; this is not independent evidence or a global pass.",
                  "obligation_ids": [], "sources": [shared]}
        value["decisions"].append(second)
        if case == "same-decision":
            second["sources"][0]["finding_ids"].append(shared["finding_ids"][0])
        elif case == "split-source-object":
            second["sources"].append(copy.deepcopy(shared))
        elif case == "missing-with-reuse":
            # Reusing one known pair cannot compensate for an omitted pair.
            sources = first["sources"]
            if len(sources) > 1:
                sources.pop()
            elif len(source["finding_ids"]) > 1:
                source["finding_ids"].pop()
            else:
                return raw
        elif case == "unknown-reference":
            shared["finding_ids"].append("invented-positive")
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    monkeypatch.setattr(AdjudicationResponses, "complete", respond)
    result, manifest, responses, _ = run_adjudication(tmp_path, monkeypatch)
    if case != "shared-support":
        assert not result.exit_ok and manifest["status"] == "failed"
        assert not responses.final_called
        assert manifest["aggregations"][-1]["raw_response"]
        return
    assert result.exit_ok, result.error
    assert responses.final_called and manifest["status"] == "reconciled"
    reused = False
    for entry in manifest["aggregations"]:
        packet = json.loads((tmp_path / entry["input_packet"]["snapshot"]).read_text())
        delivery = json.loads((tmp_path / entry["delivery_receipt"]).read_text())
        assert delivery["execution"]["input_fingerprints"][entry["input_packet"]["snapshot"]] == entry["input_packet"]["sha256"]
        assert entry["positive_reference_policy"] == packet["positive_reference_policy"] == POSITIVE_REFERENCE_POLICY
        assert entry["review"]["retained_findings"] == packet["retained_findings"]
        assert entry["output_response_bytes"] < entry["input_response_bytes"]
        required = {(name, finding) for name, group in packet["positive_findings"].items()
                    for finding in group["findings"]}
        all_pairs = []
        for decision in entry["review"]["decisions"]:
            pairs = [(source["input_id"], finding) for source in decision["sources"]
                     for finding in source["finding_ids"]]
            assert len(pairs) == len(set(pairs))
            all_pairs.extend(pairs)
        assert set(all_pairs) == required
        reused |= len(all_pairs) > len(required)
    assert reused
