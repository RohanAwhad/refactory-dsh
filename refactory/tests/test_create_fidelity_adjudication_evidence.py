"""Independent proof and negative controls over actual recursive controllers."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from benchmarks.create_fidelity.bounded_context_evidence import verify_bounded_context
from benchmarks.create_fidelity.contract import reference
from benchmarks.create_fidelity.evidence import _delivery_prompt
from refactory_dsh.create_fidelity_context import ContextPolicy
from refactory_dsh.model import DshModelClient
from refactory_dsh.nn.executor import Executor, RunContext
from refactory_dsh.nn.graph import Graph, Node
from test_create_fidelity_adjudication import AdjudicationResponses


def run_actual_controller(root, monkeypatch):
    source = reference()
    role_text = next(body for name, body in source["role_prompts"].items()
                     if Path(name).stem == "health_checker")
    stage_text = source["graph"]["nodes"]["health_checker"]["prompt_template"]
    data = ("Complete source café 漢字🙂 {{literal}}\n" * 12000).encode()
    (root / "evidence.md").write_bytes(data)
    external = AdjudicationResponses(root)
    monkeypatch.setattr(DshModelClient, "_complete", lambda client, prompt, **kwargs:
                        external.complete(client, prompt, **kwargs))
    graph = Graph()
    graph.add(Node("health_checker", "agent", role="health_checker",
                   prompt_template=role_text + "\n\n" + stage_text,
                   reads=("evidence.md",), writes=("report.md",), timeout_s=30))
    ctx = RunContext(root, facts={"create_fidelity": True}, model=DshModelClient(
        provider="fixture", model="default", effort="high", attempts=1,
        node_routes={"health_checker": {"model": "original-health-route"}}))
    # This fixture exercises recursive adjudication with exact, already-valid
    # quotes. Quote-only reserve/attempts have their own real-delivery suite.
    ctx._create_fidelity_context_policy = ContextPolicy(max_prompt_bytes=32768, quote_correction_reserve_bytes=0)
    outcome = Executor(graph, ctx).run()
    assert outcome.exit_ok, outcome.error
    path = next(root.glob(".refactory/create-fidelity-context/*/*/manifest.json"))
    manifest = json.loads(path.read_text())
    assert max(item["round"] for item in manifest["aggregations"]) >= 2
    return str(path.relative_to(root)), manifest, dict(consumer_node="health_checker", role="health_checker",
        role_text=role_text, stage_text=stage_text, required_documents={"evidence.md": data},
        required_bodies=[data.decode()])


def test_recursive_consumption_is_proven_without_semantic_acceptance(tmp_path, monkeypatch):
    name, manifest, args = run_actual_controller(tmp_path, monkeypatch)
    refs = verify_bounded_context(tmp_path, name, **args)
    assert any("aggregate-" in ref and ref.endswith(".response.json") for ref in refs)
    packet = json.loads((tmp_path / manifest["final_packet"]["snapshot"]).read_text())
    retained = [item for record in packet["reviews"]
                for item in json.loads(record["raw_response"]).get("retained_findings", [])]
    assert {item["status"] for item in retained} == {"adverse", "unresolved", "not_in_page"}
    pair = manifest["final"]["delivery_receipt"]
    delivery = json.loads((tmp_path / pair).read_text())
    workers = [(str(path.relative_to(tmp_path)), json.loads(path.read_text()))
               for path in (tmp_path / ".refactory/workers").glob("*.json")]
    _, evidence = _delivery_prompt(tmp_path, (pair, delivery), workers)
    assert any("aggregate-" in path for path in evidence)
    # This fixture never performed a health eval. Its actual scoped conclusions
    # and original final response cannot certify semantic health adequacy.
    assert all("Global coverage remains unproven" in decision["conclusion"]
               for entry in manifest["aggregations"] for decision in entry["review"]["decisions"])


@pytest.mark.parametrize("problem", ["missing-input", "unknown-input", "reused-input", "drop-adverse",
    "rewrite-adverse", "drop-positive", "duplicate-positive", "unknown-positive", "no-progress",
    "bad-frontier", "summary-packet", "wrong-role", "wrong-route", "missing-worker", "missing-obligations"])
def test_recursive_aggregate_claims_cannot_replace_complete_bound_evidence(tmp_path, monkeypatch, problem):
    name, manifest, args = run_actual_controller(tmp_path, monkeypatch)
    entry = manifest["aggregations"][0]
    value = entry["review"]
    rewrite_response = False
    if problem == "missing-input":
        entry["input_ids"].pop()
    elif problem == "unknown-input":
        entry["input_ids"][0] = "unknown-input"
    elif problem == "reused-input":
        manifest["aggregations"][1]["input_ids"][0] = entry["input_ids"][0]
    elif problem == "drop-adverse":
        value["retained_findings"] = []
        rewrite_response = True
    elif problem == "rewrite-adverse":
        value["retained_findings"][0]["content"]["finding"]["reason"] = "Softened original finding."
        rewrite_response = True
    elif problem in ("drop-positive", "duplicate-positive", "unknown-positive"):
        sources = value["decisions"][0]["sources"]
        if problem == "drop-positive":
            sources.pop()
        elif problem == "duplicate-positive":
            sources[0]["finding_ids"].append(sources[0]["finding_ids"][0])
        else:
            sources[0]["finding_ids"].append("unknown-finding")
        rewrite_response = True
    elif problem == "no-progress":
        value["decisions"][0]["conclusion"] += "x" * entry["input_response_bytes"]
        rewrite_response = True
    elif problem == "bad-frontier":
        manifest["frontier"].pop()
    elif problem == "summary-packet":
        data = b'{"summary":"All prior reports reviewed; PASS"}'
        (tmp_path / entry["input_packet"]["snapshot"]).write_bytes(data)
        entry["input_packet"].update(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
    elif problem == "missing-obligations":
        data = (tmp_path / entry["supplied_prompt"]["snapshot"]).read_bytes()
        rendering = json.dumps(manifest["obligation_rendering"], ensure_ascii=False,
                               sort_keys=True, separators=(",", ":")).encode()
        data = data.replace(b"COMPLETE REVIEW OBLIGATIONS:\n" + rendering, b"Criteria omitted.")
        delivery = json.loads((tmp_path / entry["delivery_receipt"]).read_text())
        for prompt_name in (entry["supplied_prompt"]["snapshot"], delivery["contexts"][0]["prompt"]):
            (tmp_path / prompt_name).write_bytes(data)
        entry["supplied_prompt"].update(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
        worker_path = tmp_path / entry["worker_receipts"][0]
        worker = json.loads(worker_path.read_text())
        task = entry["system"].encode() + b"\n\n" + data
        worker["prompt_transport"].update(bytes=len(task), sha256=hashlib.sha256(task).hexdigest())
        worker_path.write_text(json.dumps(worker))
        entry["worker_receipt_sha256"][entry["worker_receipts"][0]] = hashlib.sha256(worker_path.read_bytes()).hexdigest()
    else:
        path = tmp_path / entry["worker_receipts"][0]
        if problem == "missing-worker":
            path.unlink()
        else:
            worker = json.loads(path.read_text())
            if problem == "wrong-role":
                worker["role"] = "builder"
            else:
                worker["actual_route"]["model"] = "other-route"
            path.write_text(json.dumps(worker))
            entry["worker_receipt_sha256"][entry["worker_receipts"][0]] = hashlib.sha256(path.read_bytes()).hexdigest()
    if rewrite_response:
        # Rebind mutable assertions to the forged actual response as well. The
        # independent checker must still reject lost/altered semantic items or
        # missing positive dispositions, rather than relying only on SHA drift.
        raw = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
        (tmp_path / entry["raw_response"]["snapshot"]).write_bytes(raw)
        entry["raw_response"].update(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
        delivery_path = tmp_path / entry["delivery_receipt"]
        delivery = json.loads(delivery_path.read_text())
        (tmp_path / delivery["final_response"]).write_bytes(raw)
        entry["delivery_receipt_sha256"] = hashlib.sha256(delivery_path.read_bytes()).hexdigest()
        entry["output_response_bytes"] = len(raw)
    (tmp_path / name).write_text(json.dumps(manifest))
    with pytest.raises((ValueError, OSError), match="lossless review obligations" if problem == "missing-obligations" else None):
        verify_bounded_context(tmp_path, name, **args)
