"""Prospective response budgets through actual Executor/client/delivery calls."""
import hashlib
import json
import re

import pytest

from refactory_dsh.create_fidelity_context import ContextPolicy
from refactory_dsh.model import DshModelClient
from refactory_dsh.nn.executor import Executor, RunContext
from refactory_dsh.nn.graph import Graph, Node
from test_create_fidelity_context import ExternalModel


def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class BudgetResponses(ExternalModel):
    """Only external model responses are controlled; ownership stays real."""

    def __init__(self, root, *, nonprogress=False):
        super().__init__(root)
        self.nonprogress = nonprogress
        self.aggregate_calls = []

    def complete(self, client, prompt, *, system="", workdir=None):
        if "SCOPED ADJUDICATION REQUEST:\n" not in prompt:
            raw = super().complete(client, prompt, system=system, workdir=workdir)
            if "EVIDENCE READ REQUEST:\n" not in prompt:
                return raw
            value = json.loads(raw)
            value["coverage"] = []
            positive = value["claims"][0]
            positive.update(verdict="supported", reason="Observed exact literal source; no global acceptance. " + "x" * 26000)
            if value["page_id"] == "page-0001":
                request = json.JSONDecoder().raw_decode(prompt.split("EVIDENCE READ REQUEST:\n", 1)[1])[0]
                body = (self.root / request["document_snapshot"]).read_bytes()
                text = body[request["start_byte"]:request["end_byte"]].decode("utf-8")
                for number in range(44):
                    start = number * 3
                    quote = text[start:start + 3]
                    offset = request["start_byte"] + len(text[:start].encode("utf-8"))
                    value["claims"].append({
                        "claim_id": f"negative-{number}",
                        "verdict": "adverse" if number < 20 else "unresolved",
                        "reason": f"Preserve complete finding {number}: café evidence requires final reconciliation.",
                        "start_byte": offset, "end_byte": offset + len(quote.encode("utf-8")), "quote": quote,
                    })
                value["coverage"] = [{
                    "obligation_id": "original-role-stage-and-user-contract", "status": "not_in_page",
                    "reason": "Literal page observations do not establish the complete original contract.",
                }]
            return compact(value)

        # Observer fields represent the external runtime; the real DSH client
        # still creates/settles this worker and its actual delivery receipt.
        task = (system + "\n\n" + prompt).encode("utf-8")
        receipt = json.loads(client._worker_receipt.read_text())
        receipt.update(actual_route={**client.route(), "source": "runtime-request"},
                       prompt_transport={"kind": "stdin_file", "encoding": "utf-8", "bytes": len(task),
                                         "sha256": hashlib.sha256(task).hexdigest()})
        client._worker_receipt.write_text(json.dumps(receipt))
        self.calls.append((client._node_id, client._role, client.route(), task))
        request = json.JSONDecoder().raw_decode(prompt.split("SCOPED ADJUDICATION REQUEST:\n", 1)[1])[0]
        inputs = json.JSONDecoder().raw_decode(prompt.split("COMPLETE PRIOR MODEL OUTPUTS:\n", 1)[1])[0]
        budget = int(re.search(r"at most (\d+) UTF-8 bytes", prompt)[1])
        assert budget == sum(len(item["raw_response"].encode("utf-8")) for item in inputs["records"]) - 1
        assert "Return compact JSON with no indentation or formatting whitespace outside strings." in prompt
        assert "Never shorten, omit or rewrite mandatory retained adverse/unresolved/not_in_page data" in prompt
        assert all(hashlib.sha256(item["raw_response"].encode("utf-8")).hexdigest()
                   == request["input_sha256"][item["input_id"]] for item in inputs["records"])
        sources = [{"input_id": identifier, "finding_ids": list(findings)}
                   for identifier, findings in inputs["positive_findings"].items() if findings]
        response = {**request, "scope": "scoped_adjudication", "retained_findings": inputs["retained_findings"],
                    "decisions": [{"decision_id": "positive-sources", "conclusion": "Scoped positive sources remain linked; global acceptance remains unproven.",
                                   "obligation_ids": [], "sources": sources}] if sources else []}
        if self.nonprogress:
            response["decisions"][0]["conclusion"] += "x" * (budget + 1)
        raw = compact(response)
        self.aggregate_calls.append({"request": request, "inputs": inputs, "budget": budget, "raw": raw,
                                     "node": client._node_id, "prompt": prompt})
        return raw


@pytest.mark.parametrize("nonprogress", [False, True])
def test_actual_adjudication_dispatch_has_exact_budget_and_preserves_dense_findings(tmp_path, monkeypatch, nonprogress):
    evidence = ("Original source café 漢字🙂 has complete literal facts.\n" * 30000).encode("utf-8")
    (tmp_path / "evidence.md").write_bytes(evidence)
    responses = BudgetResponses(tmp_path, nonprogress=nonprogress)
    monkeypatch.setattr(DshModelClient, "_complete", lambda client, prompt, **kwargs: responses.complete(client, prompt, **kwargs))
    graph = Graph()
    graph.add(Node("review", "agent", role="health_checker", prompt_template="Read all complete source evidence, then write the final assigned report.",
                   reads=("evidence.md",), writes=("report.md",), timeout_s=30))
    ctx = RunContext(tmp_path, facts={"create_fidelity": True}, model=DshModelClient(provider="fixture", model="default", effort="high", attempts=1,
                    node_routes={"review": {"model": "original-route"}}))
    ctx._create_fidelity_context_policy = ContextPolicy()
    result = Executor(graph, ctx).run()
    manifest = json.loads(next(tmp_path.glob(".refactory/create-fidelity-context/*/*/manifest.json")).read_text())
    assert responses.aggregate_calls
    assert b"".join(body for _, _, body in responses.ranges) == evidence
    dense = next(call for call in responses.aggregate_calls if len(call["inputs"]["retained_findings"]) > 40)
    dense_response = json.loads(dense["raw"])
    assert len(dense_response["retained_findings"]) == 45
    assert dense_response["retained_findings"] == dense["inputs"]["retained_findings"]
    assert {finding["status"] for finding in dense_response["retained_findings"]} == {"adverse", "unresolved", "not_in_page"}
    required = {(name, finding) for name, findings in dense["inputs"]["positive_findings"].items() for finding in findings}
    refs = [(source["input_id"], finding) for decision in dense_response["decisions"]
            for source in decision["sources"] for finding in source["finding_ids"]]
    assert set(refs) == required and len(refs) == len(set(refs))
    assert all(role == "health_checker" and route["model"] == "original-route" for _, role, route, _ in responses.calls)
    for item, call in zip(manifest["aggregations"], responses.aggregate_calls, strict=True):
        assert (tmp_path / item["raw_response"]["snapshot"]).read_bytes() == call["raw"].encode("utf-8")
        delivered = json.loads((tmp_path / item["delivery_receipt"]).read_text())
        assert (tmp_path / delivered["contexts"][0]["prompt"]).read_text() == call["prompt"]
        assert item["worker_receipts"] and delivered["status"] == "delivered"
    if nonprogress:
        assert not result.exit_ok and "made no lossless semantic progress" in result.error
        assert manifest["status"] == "failed" and not responses.final_called
        assert len(dense["raw"].encode("utf-8")) > dense["budget"]
        assert manifest["aggregations"][-1]["status"] == "failed"
    else:
        assert result.exit_ok, result.error
        assert manifest["status"] == "reconciled" and responses.final_called
        assert all(len(call["raw"].encode("utf-8")) <= call["budget"] for call in responses.aggregate_calls)
        packet = json.loads((tmp_path / manifest["final_packet"]["snapshot"]).read_text())
        retained = [finding for item in packet["reviews"] for finding in json.loads(item["raw_response"]).get("retained_findings", [])]
        assert retained == dense_response["retained_findings"]
