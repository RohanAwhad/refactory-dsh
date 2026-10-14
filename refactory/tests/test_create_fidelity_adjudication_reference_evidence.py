"""Actual same-role multi-conclusion evidence reuse with explicit policy scope."""
import copy
import hashlib
import json

import pytest

from benchmarks.create_fidelity.bounded_context_evidence import verify_bounded_context
from test_create_fidelity_adjudication_evidence import run_actual_controller
from test_create_fidelity_adjudication_rendering_evidence import canonical, rebind_packet


def controller(root, monkeypatch):
    import test_create_fidelity_adjudication_evidence as fixture
    from test_create_fidelity_adjudication import AdjudicationResponses
    class SharedEvidenceResponses(AdjudicationResponses):
        reused = False
        def complete(self, client, prompt, *, system="", workdir=None):
            raw = super().complete(client, prompt, system=system, workdir=workdir)
            if "SCOPED ADJUDICATION REQUEST:\n" not in prompt or self.reused:
                return raw
            value = json.loads(raw)
            pairs = [(source["input_id"], name) for decision in value["decisions"]
                     for source in decision["sources"] for name in source["finding_ids"]]
            if len(pairs) >= 2:
                identifier, finding = pairs[0]
                value["decisions"].append({"decision_id": "related-documentation",
                    "conclusion": "The same actual source also supports a distinct documentation handoff.",
                    "obligation_ids": [], "sources": [{"input_id": identifier, "finding_ids": [finding]}]})
                self.reused = True
            return canonical(value).decode()
    monkeypatch.setattr(fixture, "AdjudicationResponses", SharedEvidenceResponses)
    name, manifest, args = run_actual_controller(root, monkeypatch)
    entry = next(item for item in manifest["aggregations"] if len(item["review"]["decisions"]) == 2)
    return name, manifest, args, entry


def rebind_response(root, entry):
    raw = canonical(entry["review"])
    (root / entry["raw_response"]["snapshot"]).write_bytes(raw)
    entry["raw_response"].update(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
    delivery = json.loads((root / entry["delivery_receipt"]).read_text())
    (root / delivery["final_response"]).write_bytes(raw)
    entry["output_response_bytes"] = len(raw)


def test_real_original_role_reuses_known_source_for_distinct_conclusions_without_losing_any_input(tmp_path, monkeypatch):
    name, manifest, args, entry = controller(tmp_path, monkeypatch)
    refs = verify_bounded_context(tmp_path, name, **args)
    assert entry["positive_reference_policy"] == "per-decision-unique/v1"
    packet = json.loads((tmp_path / entry["input_packet"]["snapshot"]).read_text())
    assert packet["positive_reference_policy"] == entry["positive_reference_policy"]
    first, related = entry["review"]["decisions"]
    assert first["decision_id"] != related["decision_id"]
    citation = related["sources"][0]
    assert any(source["input_id"] == citation["input_id"] and citation["finding_ids"][0] in source["finding_ids"]
               for source in first["sources"])
    required = {(identifier, name) for identifier, group in packet["positive_findings"].items()
                for name in group["findings"]}
    actual = [(source["input_id"], finding) for decision in entry["review"]["decisions"]
              for source in decision["sources"] for finding in source["finding_ids"]]
    assert set(actual) == required and len(actual) > len(required)
    assert any("aggregate-" in ref for ref in refs)
    # Raw leaves and every adverse/unresolved/not_in_page item remain in the
    # real aggregate inputs and exact output ledgers; citations are not truth.
    assert packet["retained_findings"] == entry["review"]["retained_findings"]


@pytest.mark.parametrize("problem", ["duplicate-in-array", "duplicate-across-source-objects", "unknown-finding",
    "missing-global-coverage", "duplicate-decision-id", "rewrite-negative", "unknown-policy",
    "missing-entry-policy", "missing-packet-policy", "entry-packet-mismatch", "legacy-both-absent",
    "missing-packet-fingerprint", "forged-packet-fingerprint"])
def test_complete_rebound_actual_records_still_refuse_invalid_source_dispositions(tmp_path, monkeypatch, problem):
    name, manifest, args, entry = controller(tmp_path, monkeypatch)
    value = entry["review"]
    first, related = value["decisions"]
    citation = copy.deepcopy(related["sources"][0])
    marker_problem = problem in {"unknown-policy", "missing-entry-policy", "missing-packet-policy",
                                "entry-packet-mismatch", "legacy-both-absent"}
    if problem == "duplicate-in-array":
        related["sources"][0]["finding_ids"].append(citation["finding_ids"][0])
    elif problem == "duplicate-across-source-objects":
        related["sources"].append(citation)
    elif problem == "unknown-finding":
        related["sources"][0]["finding_ids"][0] = "invented:claim:unknown"
    elif problem == "missing-global-coverage":
        used = (citation["input_id"], citation["finding_ids"][0])
        other = next((source["input_id"], finding) for source in first["sources"] for finding in source["finding_ids"]
                     if (source["input_id"], finding) != used)
        first["sources"] = [{"input_id": source["input_id"], "finding_ids": [finding for finding in source["finding_ids"]
                             if (source["input_id"], finding) != other]} for source in first["sources"]]
        first["sources"] = [source for source in first["sources"] if source["finding_ids"]]
    elif problem == "duplicate-decision-id":
        related["decision_id"] = first["decision_id"]
    elif problem == "rewrite-negative":
        target = next(item for item in manifest["aggregations"] if item["review"]["retained_findings"])
        target["review"]["retained_findings"][0]["content"]["finding"]["reason"] = "Softened actual adverse evidence."
        rebind_response(tmp_path, target)
    elif problem in {"missing-packet-fingerprint", "forged-packet-fingerprint"}:
        path = tmp_path / entry["delivery_receipt"]
        delivery = json.loads(path.read_text())
        fingerprints = delivery["execution"]["input_fingerprints"]
        if problem == "missing-packet-fingerprint":
            fingerprints.pop(entry["input_packet"]["snapshot"])
        else:
            fingerprints[entry["input_packet"]["snapshot"]] = "0" * 64
        path.write_text(json.dumps(delivery))
        entry["delivery_receipt_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    else:
        packet = json.loads((tmp_path / entry["input_packet"]["snapshot"]).read_text())
        if problem == "unknown-policy":
            entry["positive_reference_policy"] = packet["positive_reference_policy"] = "allow-any-reference/v999"
        elif problem == "missing-entry-policy":
            entry.pop("positive_reference_policy")
        elif problem == "missing-packet-policy":
            packet.pop("positive_reference_policy")
        elif problem == "entry-packet-mismatch":
            packet["positive_reference_policy"] = "allow-any-reference/v999"
        else:
            # Complete old-shaped packet is genuinely supplied in the rebound
            # actual worker transport; absence still imposes old GLOBAL
            # uniqueness, so this cross-decision reuse cannot get retrocredit.
            entry.pop("positive_reference_policy")
            packet.pop("positive_reference_policy")
        rebind_packet(tmp_path, entry, packet)
    if not marker_problem and problem not in {"rewrite-negative", "missing-packet-fingerprint", "forged-packet-fingerprint"}:
        rebind_response(tmp_path, entry)
    (tmp_path / name).write_text(json.dumps(manifest))
    expected = "duplicated positive evidence" if problem == "legacy-both-absent" else (
        "packet|policy" if marker_problem or problem.endswith("packet-fingerprint") else "positive evidence|positive input|decision identities|adverse/unresolved")
    with pytest.raises(ValueError, match=expected):
        verify_bounded_context(tmp_path, name, **args)
