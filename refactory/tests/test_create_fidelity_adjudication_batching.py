"""Actual same-role dispatch reaches later pairs without discarding singletons."""
import json

import pytest

from refactory_dsh.create_fidelity_context import ContextPolicy, adjudication_inputs, render_adjudication_inputs
from refactory_dsh.model import DshModelClient
from refactory_dsh.nn.executor import Executor, RunContext
from refactory_dsh.nn.graph import Graph, Node
from test_create_fidelity_context import ExternalModel
from test_create_fidelity_adjudication_budget import BudgetResponses, compact


class LaterPairResponses(BudgetResponses):
    def __init__(self, root, *, all_singletons=False, oversized_later=False):
        super().__init__(root)
        self.all_singletons = all_singletons
        self.oversized_later = oversized_later

    def complete(self, client, prompt, *, system="", workdir=None):
        if "EVIDENCE READ REQUEST:\n" not in prompt:
            return super().complete(client, prompt, system=system, workdir=workdir)
        value = json.loads(ExternalModel.complete(self, client, prompt, system=system, workdir=workdir))
        value["coverage"] = []
        size = 23000 if self.all_singletons or value["page_id"] in {"page-0001", "page-0002"} else 1000
        if self.oversized_later and value["page_id"] == "page-0002":
            size = 40000
        value["claims"][0].update(verdict="supported", reason="Exact source observation. " + "x" * size)
        return compact(value)


@pytest.mark.parametrize("shape", ["later-pair", "all-singletons", "oversized-later"])
def test_real_adjudication_carries_singleton_to_reach_later_fitting_pair(tmp_path, monkeypatch, shape):
    evidence = ("Complete original source evidence, with no omitted bytes.\n" * 14000).encode()
    (tmp_path / "evidence.md").write_bytes(evidence)
    responses = LaterPairResponses(tmp_path, all_singletons=shape == "all-singletons", oversized_later=shape == "oversized-later")
    monkeypatch.setattr(DshModelClient, "_complete", lambda client, prompt, **kwargs: responses.complete(client, prompt, **kwargs))
    graph = Graph()
    graph.add(Node("review", "agent", role="health_checker", prompt_template="Read every complete source range then write the assigned report.",
                   reads=("evidence.md",), writes=("report.md",), timeout_s=30))
    ctx = RunContext(tmp_path, facts={"create_fidelity": True}, model=DshModelClient(provider="fixture", model="default", attempts=1,
                     node_routes={"review": {"model": "original-route"}}))
    ctx._create_fidelity_context_policy = ContextPolicy(max_prompt_bytes=50000)
    result = Executor(graph, ctx).run()
    manifest = json.loads(next(tmp_path.glob(".refactory/create-fidelity-context/*/*/manifest.json")).read_text())
    assert len(manifest["reviews"]) > 3
    assert all(item["status"] == "reviewed" for item in manifest["reviews"])
    assert b"".join(body for _, _, body in responses.ranges) == evidence
    first = render_adjudication_inputs(adjudication_inputs(manifest, ["page-0001"], tmp_path))
    pair = render_adjudication_inputs(adjudication_inputs(manifest, ["page-0001", "page-0002"], tmp_path))
    # Before any final reconciliation, even the first two raw-output packets
    # differ enough that a singleton fits but their combined packet cannot.
    assert len(compact(first).encode()) < 38000
    assert len(compact(pair).encode()) + 12000 > 50000
    assert all(route["model"] == "original-route" and role == "health_checker" for _, role, route, _ in responses.calls)
    if shape != "later-pair":
        expected = "frontier made no progress" if shape == "all-singletons" else "complete prior adjudication output cannot fit"
        assert not result.exit_ok and expected in result.error
        assert manifest["status"] == "failed"
        assert not responses.aggregate_calls and not responses.final_called
    else:
        assert result.exit_ok, result.error
        assert manifest["status"] == "reconciled" and responses.final_called
        assert responses.aggregate_calls[0]["request"]["input_ids"][0] == "page-0002"
        assert len(responses.aggregate_calls[0]["request"]["input_ids"]) >= 2
        assert all("page-0001" not in item["input_ids"] for item in manifest["aggregations"] if item["round"] == 1)
        assert "page-0001" in manifest["frontier"] or any(
            "page-0001" in item["input_ids"] and item["round"] > 1 for item in manifest["aggregations"])
        for item, call in zip(manifest["aggregations"], responses.aggregate_calls, strict=True):
            assert item["status"] == "reviewed"
            assert (tmp_path / item["raw_response"]["snapshot"]).read_bytes() == call["raw"].encode()
            assert len(call["raw"].encode()) <= call["budget"]
            delivery = json.loads((tmp_path / item["delivery_receipt"]).read_text())
            assert delivery["status"] == "delivered" and item["worker_receipts"]
