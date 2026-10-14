"""Actual same-role model boundaries; controlled scoped responses only."""
import hashlib
import json
import time

import pytest

from refactory_dsh.create_fidelity_context import ContextPolicy, MARKER
from refactory_dsh.model import DshModelClient
from refactory_dsh.nn.executor import Executor, RunContext
from refactory_dsh.nn.graph import Graph, Node
from test_create_fidelity_context import ExternalModel


class AdjudicationResponses(ExternalModel):
    def __init__(self, root, problem=None):
        super().__init__(root, role="health_checker")
        self.problem = problem
        self.aggregate_calls = []

    def complete(self, client, prompt, *, system="", workdir=None):
        if "SCOPED ADJUDICATION REQUEST:\n" not in prompt:
            raw = super().complete(client, prompt, system=system, workdir=workdir)
            if "EVIDENCE READ REQUEST:\n" in prompt:
                value = json.loads(raw)
                value["coverage"] = []
                number = int(value["page_id"].split("-")[-1])
                value["claims"][0]["verdict"] = "adverse" if number == 1 else "unresolved" if number == 2 else "supported"
                value["claims"][0]["reason"] = f"Controlled scoped finding {number}: no semantic or artifact PASS asserted. "
                if number == 2:
                    value["coverage"] = [{"obligation_id": "original-role-stage-and-user-contract", "status": "not_in_page", "reason": "This page does not establish the original role contract."}]
                encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
                value["claims"][0]["reason"] += "x" * (2000 - len(encoded.encode()))
                raw = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
                assert len(raw.encode()) == 2000
            return raw
        task = (system + "\n\n" + prompt if system else prompt).encode()
        receipt = json.loads(client._worker_receipt.read_text())
        receipt.update(actual_route={**client.route(), "source": "runtime-request"}, prompt_transport={"kind": "stdin_file", "encoding": "utf-8", "bytes": len(task), "sha256": hashlib.sha256(task).hexdigest()})
        client._worker_receipt.write_text(json.dumps(receipt))
        self.calls.append((client._node_id, client._role, client.route(), task))
        request = json.JSONDecoder().raw_decode(prompt.split("SCOPED ADJUDICATION REQUEST:\n", 1)[1])[0]
        inputs = json.JSONDecoder().raw_decode(prompt.split("COMPLETE PRIOR MODEL OUTPUTS:\n", 1)[1])[0]
        allowed = json.JSONDecoder().raw_decode(prompt.split("CLOSED COVERAGE ID VOCABULARY:\n", 1)[1])[0]
        assert allowed == ["original-role-stage-and-user-contract"]
        self.aggregate_calls.append((request, inputs))
        assert [item["input_id"] for item in inputs["records"]] == request["input_ids"]
        assert all(hashlib.sha256(item["raw_response"].encode()).hexdigest() == request["input_sha256"][item["input_id"]] for item in inputs["records"])
        if self.problem == "deadline":
            while True:
                client._raise_if_cancelled()
                time.sleep(.01)
        sources = [{"input_id": identifier, "finding_ids": list(findings)} for identifier, findings in inputs["positive_findings"].items() if findings]
        value = {**request, "scope": "scoped_adjudication", "retained_findings": inputs["retained_findings"],
                 "decisions": [{"decision_id": "scoped-positive", "conclusion": "Original-role scoped positive evidence adjudicated using every exact source reference. Global coverage remains unproven.", "obligation_ids": [], "sources": sources}] if sources else []}
        if self.problem == "drop-negative":
            value["retained_findings"] = []
        elif self.problem == "rewrite-negative" and value["retained_findings"]:
            value["retained_findings"][0]["content"]["finding"]["reason"] = "Erased original adverse reason."
        elif self.problem == "unknown-positive" and sources:
            sources[0]["finding_ids"].append("unknown-positive")
        elif self.problem == "drop-positive":
            value["decisions"] = []
        elif self.problem == "unknown-obligation" and value["decisions"]:
            value["decisions"][0]["obligation_ids"] = ["B08"]
        elif self.problem == "no-progress" and value["decisions"]:
            value["decisions"][0]["conclusion"] += "x" * sum(len(item["raw_response"].encode()) for item in inputs["records"])
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def run_adjudication(tmp_path, monkeypatch, problem=None):
    evidence = ("Complete original source café 漢字🙂 {{literal}}\n" * 12000).encode()
    (tmp_path / "evidence.md").write_bytes(evidence)
    responses = AdjudicationResponses(tmp_path, problem)
    monkeypatch.setattr(DshModelClient, "_complete", lambda client, prompt, **kwargs: responses.complete(client, prompt, **kwargs))
    graph = Graph()
    graph.add(Node("review", "agent", role="health_checker", prompt_template="Original role: inspect all complete evidence, then perform final assigned report.", reads=("evidence.md",), writes=("report.md",), timeout_s=1 if problem == "deadline" else 30))
    ctx = RunContext(tmp_path, facts={"create_fidelity": True}, model=DshModelClient(provider="fixture", model="default", effort="high", attempts=1, node_routes={"review": {"model": "original-route"}}))
    ctx._create_fidelity_context_policy = ContextPolicy(max_prompt_bytes=32768)
    result = Executor(graph, ctx).run()
    manifest = json.loads(next(tmp_path.glob(".refactory/create-fidelity-context/*/*/manifest.json")).read_text())
    return result, manifest, responses, evidence


def test_bounded_recursive_original_role_adjudication_keeps_all_adverse_and_source_links(tmp_path, monkeypatch):
    result, manifest, responses, evidence = run_adjudication(tmp_path, monkeypatch)
    assert result.exit_ok, result.error
    assert manifest["status"] == "reconciled" and manifest["aggregations"]
    assert max(item["round"] for item in manifest["aggregations"]) >= 2
    assert b"".join(part for _, _, part in responses.ranges) == evidence
    assert all(role == "health_checker" and route["model"] == "original-route" and len(task) <= 32768 for _, role, route, task in responses.calls)
    assert all(item["output_response_bytes"] < item["input_response_bytes"] for item in manifest["aggregations"])
    packet = json.loads((tmp_path / manifest["final_packet"]["snapshot"]).read_text())
    retained = [finding for item in packet["reviews"] for finding in json.loads(item["raw_response"]).get("retained_findings", [])]
    assert {item["status"] for item in retained} == {"adverse", "unresolved", "not_in_page"}
    original_findings = {item["review"]["claims"][0]["claim_id"]: item["review"]["claims"][0] for item in manifest["reviews"][:2]}
    for finding in retained:
        if finding["content"]["kind"] == "claim":
            content = finding["content"]["finding"]
            assert content == original_findings[content["claim_id"]]
    assert manifest["final"]["uttered_reconciliation_marker"] == MARKER + manifest["reconciliation_digest"]


@pytest.mark.parametrize("problem", ["drop-negative", "rewrite-negative", "unknown-positive", "drop-positive", "unknown-obligation", "no-progress", "deadline"])
def test_unproven_or_nonprogressing_adjudication_never_reaches_final(tmp_path, monkeypatch, problem):
    result, manifest, responses, _ = run_adjudication(tmp_path, monkeypatch, problem)
    assert not result.exit_ok and manifest["status"] == "failed"
    assert responses.aggregate_calls and not responses.final_called
    assert manifest["aggregations"][-1]["status"] == "failed"
    assert manifest["aggregations"][-1]["worker_receipts"]


@pytest.mark.parametrize("kwargs", [{"max_adjudications": 129}, {"max_adjudication_rounds": 17}])
def test_adjudication_hard_caps_match_independent_evidence_validator(kwargs):
    with pytest.raises(ValueError, match="128 calls and 16 rounds"):
        ContextPolicy(**kwargs).validate()
