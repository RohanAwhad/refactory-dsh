"""Real Executor/client/delivery ownership; only external model I/O is fake."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import time

import pytest

from refactory_dsh.create_fidelity_context import ContextPolicy, MARKER
from refactory_dsh.model import DshModelClient
from refactory_dsh.nn.executor import Executor, RunContext
from refactory_dsh.nn.graph import Graph, Node


class ExternalModel:
    def __init__(self, root, problem=None, role="health_checker"):
        self.root, self.problem, self.role = root, problem, role
        self.calls, self.ranges = [], []
        self.final_called = False

    def complete(self, client, prompt, *, system="", workdir=None):
        # These fields are observations normally supplied by the external DSH
        # observer. Worker begin/finish, routes, cancellation and delivery stay real.
        task = (system + "\n\n" + prompt if system else prompt).encode()
        record = json.loads(client._worker_receipt.read_text())
        record.update(actual_route={**client.route(), "source": "runtime-request"},
                      prompt_transport={"kind": "stdin_file", "encoding": "utf-8",
                                        "bytes": len(task), "sha256": hashlib.sha256(task).hexdigest()})
        if self.problem == "forged-transport":
            record["prompt_transport"]["sha256"] = "0" * 64
        client._worker_receipt.write_text(json.dumps(record))
        self.calls.append((client._node_id, client._role, client.route(), task))
        if "EVIDENCE READ REQUEST:\n" in prompt:
            request = json.JSONDecoder().raw_decode(prompt.split("EVIDENCE READ REQUEST:\n", 1)[1])[0]
            data = (self.root / request["document_snapshot"]).read_bytes()
            text = data[request["start_byte"]:request["end_byte"]].decode()
            assert text in prompt
            self.ranges.append((request["start_byte"], request["end_byte"], text.encode()))
            if self.problem == "read-deadline":
                while True:
                    client._raise_if_cancelled()
                    time.sleep(.01)
            if self.problem == "source-drift":
                (self.root / "evidence.md").write_text("Different evidence after read dispatch.")
            if self.problem == "manifest-drift":
                manifest = next(self.root.glob(".refactory/create-fidelity-context/*/*/manifest.json"))
                manifest.write_text("{}")
            if self.problem == "output-edit":
                (self.root / "report.md").write_text("Read pass improperly wrote the final artifact.")
            if self.problem == "cache-edit":
                (self.root / ".factory/packages/generated/__pycache__/authored.txt").write_text("Changed authored cache data.")
            if self.problem == "archive-peer":
                (self.root / ".factory/packages/generated/new-builder-file.txt").write_text("Legitimate concurrent builder file.")
                (self.root / ".factory/archive/peer-note.md").write_text("Other nonblocking archive owner.")
                (self.root / ".factory/performance_report.json").write_text("{}")
            obligations = json.JSONDecoder().raw_decode(prompt.split("COMPLETE REVIEW OBLIGATIONS:\n", 1)[1])[0]
            quote = text[:32]
            value = {"schema_version": 1, "scope": "page_only", **{key: request[key] for key in (
                "context_id", "logical_node_id", "role", "page_id", "document_sha256", "start_byte", "end_byte", "page_sha256")},
                "coverage": [{"obligation_id": item["id"], "status": "unresolved",
                              "reason": "Full source range inspected; final reconciliation still required."} for item in obligations],
                "claims": [{"claim_id": request["page_id"], "verdict": "unresolved",
                            "reason": "Concrete literal source evidence observed, not a stage PASS.",
                            "start_byte": request["start_byte"], "end_byte": request["start_byte"] + len(quote.encode()),
                            "quote": quote}]}
            if self.problem == "unknown-coverage":
                value["coverage"][0]["obligation_id"] = "Unknown-obligation"
            if self.problem == "forge-range":
                value["end_byte"] -= 1
            if self.problem == "forge-quote":
                value["claims"][0]["quote"] = "An invented supporting claim."
            if self.problem == "aggregate-overflow":
                value["claims"][0]["reason"] += " Retained extensive concrete evidence detail." * 500
            return json.dumps(value, ensure_ascii=False)
        self.final_called = True
        if self.problem == "final-deadline":
            while True:
                client._raise_if_cancelled()
                time.sleep(.01)
        marker = re.findall(re.escape(MARKER) + r"[a-f0-9]{64}", prompt)
        if self.problem == "stale-read-response":
            path = next(self.root.glob(".refactory/create-fidelity-context/*/*/page-0001.response.json"))
            path.write_text("Changed after the actual evidence read.")
        (self.root / "report.md").write_text("Original final artifact schema and concrete findings remain unchanged.\n")
        if self.role == "archivist":
            (self.root / ".factory/archive/own-note.md").write_text("Final archive owner performed its actual task.\n")
            (self.root / ".factory/performance_report.json").write_text('{"final_owner":"archivist"}')
        return "Final role reconciled the complete evidence. " + (marker[-1] if marker and self.problem != "missing-marker" else "")


def run_stage(tmp_path, monkeypatch, *, problem=None, large=True, timeout=20, role="health_checker"):
    evidence = ("Original obligation café 漢字🙂 {{literal}}\r\n" * 1400) if large else "Exact old small evidence.\n"
    (tmp_path / "evidence.md").write_bytes(evidence.encode())
    for name, body in (("user-intent.md", "Preserve all exact original reports and role duties.\n"),
                       ("current.md", "- [ ] Review the original complete evidence.\n")):
        path = tmp_path / ".factory/strategy" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
    (tmp_path / ".factory/reviews").mkdir(parents=True)
    (tmp_path / ".factory/reviews/native-admission.json").write_text("{}")
    package = tmp_path / ".factory/packages/generated/__pycache__"
    package.mkdir(parents=True)
    (package / "authored.txt").write_text("Authored cache-named data stays protected.")
    if role == "archivist":
        (tmp_path / ".factory/archive").mkdir()
    external = ExternalModel(tmp_path, problem, role)
    monkeypatch.setattr(DshModelClient, "_complete", lambda client, prompt, **kwargs: external.complete(client, prompt, **kwargs))
    client = DshModelClient(provider="fixture", model="gpt-6-sol", effort="high", attempts=1,
                            node_routes={"review": {"model": "specific-model", "effort": "high"}})
    ctx = RunContext(tmp_path, facts={"create_fidelity": True}, model=client)
    ctx._create_fidelity_context_policy = ContextPolicy(max_prompt_bytes=32768)
    graph = Graph()
    writes = ("report.md",) if role != "archivist" else ("report.md", ".factory/archive/", ".factory/performance_report.json")
    graph.add(Node("review", "agent", role=role, prompt_template="Original role: inspect complete evidence, then write the final report.",
                   reads=("evidence.md",), writes=writes, timeout_s=timeout, notes="Original stage system duty."))
    result = Executor(graph, ctx).run()
    manifests = list(tmp_path.glob(".refactory/create-fidelity-context/*/*/manifest.json"))
    manifest = json.loads(manifests[0].read_text()) if manifests else None
    return result, manifest, external, evidence


def test_multibyte_exact_ranges_original_route_and_real_final_delivery(tmp_path, monkeypatch):
    result, manifest, external, evidence = run_stage(tmp_path, monkeypatch)
    assert result.exit_ok, result.error
    assert manifest["status"] == "reconciled" and len(manifest["pages"]) > 1
    assert b"".join(data for _, _, data in external.ranges) == evidence.encode()
    assert all(role == "health_checker" and route["model"] == "specific-model" for _, role, route, _ in external.calls)
    assert all(len(task) <= 32768 for _, _, _, task in external.calls)
    receipt = json.loads((tmp_path / manifest["final"]["delivery_receipt"]).read_text())
    assert receipt["bounded_context"]["digest"] == manifest["reconciliation_digest"]
    assert "CONTEXT_RECONCILIATION" not in (tmp_path / "report.md").read_text()
    assert all(review["review"]["claims"][0]["verdict"] == "unresolved" for review in manifest["reviews"])


def test_small_inputs_keep_exact_old_prompt_and_single_model_dispatch(tmp_path, monkeypatch):
    result, manifest, external, _ = run_stage(tmp_path, monkeypatch, large=False)
    assert result.exit_ok, result.error
    assert manifest is None and len(external.calls) == 1
    delivered = next(tmp_path.glob(".refactory/agent-delivery/*/context-0.prompt.txt")).read_text()
    old = "Original role: inspect complete evidence, then write the final report.\n\n=== COMPLETE DECLARED INPUT evidence.md ===\nExact old small evidence.\n"
    assert delivered.endswith(old) and delivered.count(old) == 1


@pytest.mark.parametrize("problem", ["unknown-coverage", "forge-range", "forge-quote", "forged-transport", "source-drift", "manifest-drift", "output-edit", "cache-edit"])
def test_incomplete_forged_stale_or_mutating_reads_refuse_final_task(tmp_path, monkeypatch, problem):
    result, manifest, external, _ = run_stage(tmp_path, monkeypatch, problem=problem)
    assert not result.exit_ok and manifest["status"] == "failed"
    assert not external.final_called
    assert manifest["reviews"][0]["status"] == "failed"
    assert manifest["reviews"][0]["worker_receipts"]


def test_oversize_final_keeps_all_actual_reviews_and_refuses_final_dispatch(tmp_path, monkeypatch):
    result, manifest, external, evidence = run_stage(tmp_path, monkeypatch, problem="aggregate-overflow")
    assert not result.exit_ok and "ceiling" in result.error
    assert not external.final_called and manifest["status"] == "failed"
    assert len(manifest["pages"]) == len(manifest["reviews"]) > 1
    assert b"".join(data for _, _, data in external.ranges) == evidence.encode()
    assert all(review["status"] == "reviewed" for review in manifest["reviews"])


@pytest.mark.parametrize("problem", ["missing-marker", "stale-read-response"])
def test_final_must_utter_exact_reconciliation_and_retain_actual_read_bytes(tmp_path, monkeypatch, problem):
    result, manifest, external, _ = run_stage(tmp_path, monkeypatch, problem=problem)
    assert external.final_called and not result.exit_ok
    assert manifest["status"] == "failed"


@pytest.mark.parametrize("problem", ["read-deadline", "final-deadline"])
def test_one_original_stage_deadline_cancels_read_or_final_and_settles_workers(tmp_path, monkeypatch, problem):
    started = time.monotonic()
    result, manifest, external, _ = run_stage(tmp_path, monkeypatch, problem=problem, timeout=1)
    assert not result.exit_ok and time.monotonic() - started < 3
    assert manifest["status"] == "failed"
    workers = [json.loads(path.read_text()) for path in (tmp_path / ".refactory/workers").glob("*.json")]
    assert any(worker["state"] == "cancelled" for worker in workers)
    assert not any(worker["state"] == "starting" for worker in workers)
    assert external.final_called == (problem == "final-deadline")


def test_nonblocking_archivist_does_not_freeze_concurrently_owned_package_or_shared_reports(tmp_path, monkeypatch):
    result, manifest, external, _ = run_stage(tmp_path, monkeypatch, role="archivist", problem="archive-peer")
    assert result.exit_ok, result.error
    assert manifest["status"] == "reconciled"
    assert all(set(review["application_before"]) == {"sha256", "bytes", "snapshot"} for review in manifest["reviews"])
    inventory = json.loads((tmp_path / manifest["reviews"][0]["application_before"]["snapshot"]).read_text())
    assert set(inventory) == {"report.md"}


def test_native_worker_bridge_retains_role_outputs_and_bounded_final_binding(tmp_path, monkeypatch):
    from refactory_dsh import create_fidelity_context as contexts, worker_node
    (tmp_path / "facts.json").write_text('{"create_fidelity":true}')
    body = "Native source café🙂\r\n" * 3000
    (tmp_path / "evidence.md").write_bytes(body.encode())
    external = ExternalModel(tmp_path)
    monkeypatch.setattr(DshModelClient, "_complete", lambda client, prompt, **kw: external.complete(client, prompt, **kw))
    policy = ContextPolicy(max_prompt_bytes=32768)
    monkeypatch.setattr(contexts, "ContextPolicy", lambda: policy)
    client = DshModelClient(provider="fixture", model="gpt-6-sol", effort="high", attempts=1).with_node_timeout(20)
    receipt = worker_node.execute({"schema_version": 1, "node_id": "native-review", "role": "health_checker",
        "reads": ["evidence.md"], "writes": ["report.md"], "prompt": "Original native role review.",
        "model_context": client.context(), "produces": [], "post_checks": []}, tmp_path)
    assert receipt["status"] == "delivered" and receipt["bounded_context"]["protocol"].endswith("/v1")
    assert b"".join(chunk for _, _, chunk in external.ranges) == body.encode()
    assert all(role == "health_checker" for _, role, _, _ in external.calls)
    manifest = json.loads((tmp_path / receipt["bounded_context"]["manifest"]).read_text())
    before = json.loads((tmp_path / manifest["reviews"][0]["application_before"]["snapshot"]).read_text())
    assert before == {"report.md": None}


@pytest.mark.parametrize("missing_marker", [False, True])
def test_large_agent_gate_keeps_native_strict_json_and_real_final_delivery(tmp_path, monkeypatch, missing_marker):
    from refactory_dsh import create_fidelity_gates as gates
    (tmp_path / "evidence.md").write_text("Exact gate evidence café.\n" * 2800)
    external = ExternalModel(tmp_path, role="ceo")

    def response(client, prompt, **kw):
        if "EVIDENCE READ REQUEST:\n" in prompt:
            return external.complete(client, prompt, **kw)
        # Preserve the original strict schema; marker lives in rationale.
        task = (kw.get("system", "") + "\n\n" + prompt).encode()
        worker = json.loads(client._worker_receipt.read_text())
        worker.update(actual_route={**client.route(), "source": "runtime-request"}, prompt_transport={
            "kind": "stdin_file", "encoding": "utf-8", "bytes": len(task), "sha256": hashlib.sha256(task).hexdigest()})
        client._worker_receipt.write_text(json.dumps(worker))
        marker = re.findall(re.escape(MARKER) + r"[a-f0-9]{64}", prompt)[-1]
        return json.dumps({"schema_version": 1, "gate_id": "gate-check", "input_digest": gates._digest({"evidence.md": "bound"}),
                           "evidence_digest": gates._digest({"inputs": {"evidence.md": "bound"}}), "verdict": "proceed",
                           "rationale": "Original CEO reconciled actual whole source evidence. " + ("" if missing_marker else marker)})

    monkeypatch.setattr(DshModelClient, "_complete", response)
    monkeypatch.setattr(gates, "_evidence", lambda ctx, node: ({"inputs": {"evidence.md": "bound"}}, []))
    ctx = RunContext(tmp_path, model=DshModelClient(provider="fixture", model="gpt-6-sol", effort="high", attempts=1))
    ctx._gate_labels = ["proceed", "reloop"]
    ctx._create_fidelity_context_policy = ContextPolicy(max_prompt_bytes=32768)
    node = Node("gate-check", "gate", role="ceo", gate_kind="agent", reads=("evidence.md",),
                prompt_template="Apply the original CEO gate question and complete evidence.", timeout_s=20)
    if missing_marker:
        with pytest.raises(ValueError, match="did not reconcile"):
            gates.evaluate(ctx, node)
        verdict = json.loads(next((tmp_path / gates.ROOT).glob("*/verdict.json")).read_text())
        assert verdict["status"] == "failed"
        failed = json.loads((tmp_path / verdict["bounded_context"]["manifest"]).read_text())
        assert failed["status"] == "failed" and failed["final"]["delivery_receipt"]
        return
    assert gates.evaluate(ctx, node) == "proceed"
    manifest = json.loads(next(tmp_path.glob(".refactory/create-fidelity-context/*/*/manifest.json")).read_text())
    assert manifest["status"] == "reconciled"
    request = next(item for item in manifest["documents"] if item["source_kind"] == "derived_gate_request")
    assert (tmp_path / request["provenance"]["parent_receipt"]).is_file()
    final = json.loads((tmp_path / manifest["final"]["delivery_receipt"]).read_text())
    assert final["bounded_context"]["digest"] == manifest["reconciliation_digest"]
