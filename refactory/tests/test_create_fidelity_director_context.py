"""Complete staged CEO reads across real director/Executor delivery boundaries."""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from pathlib import Path

from refactory_dsh import create_fidelity_directors as directors
from refactory_dsh.create_fidelity_context import MARKER
from refactory_dsh.model import DshModelClient
from refactory_dsh.nn.executor import Executor, RunContext
from refactory_dsh.nn.graph import Graph, Node
from test_create_fidelity_directors import ExternalResponses, plan_for, seed_inputs


class CompleteEvidenceResponses(ExternalResponses):
    """Fake only external responses; collect exactly the dispatched UTF-8 ranges."""

    def __init__(self, *, retry=False, omit_marker=False, wait_for_deadline=False):
        super().__init__({directors.RESEARCH.id: plan_for(directors.RESEARCH, 3)})
        self.retry, self.omit_marker = retry, omit_marker
        self.wait_for_deadline = wait_for_deadline
        self.parts = {}
        self.reconstructed = {}
        self.context_routes = []

    def _record_route(self, client, prompt, system):
        record = json.loads(client._worker_receipt.read_text())
        record["actual_route"] = {**client.route(), "source": "runtime-request"}
        task = (system + "\n\n" + prompt if system else prompt).encode()
        record["prompt_transport"] = {"kind": "stdin_file", "encoding": "utf-8", "bytes": len(task), "sha256": hashlib.sha256(task).hexdigest()}
        client._worker_receipt.write_text(json.dumps(record))

    def _large_child_body(self, node):
        return (f"# Independently delivered {node}\n\nConcrete graph and original-ask investigation.\n"
                + (f"Unique evidence {node}: αβγ🙂 {{{{name}}}}\n" * 1600))

    def complete(self, client, prompt, *, system="", workdir=None):
        node = client._node_id
        if "SCOPED ADJUDICATION REQUEST:\n" in prompt:
            self._record_route(client, prompt, system)
            request = json.JSONDecoder().raw_decode(prompt.split("SCOPED ADJUDICATION REQUEST:\n", 1)[1])[0]
            inputs = json.JSONDecoder().raw_decode(prompt.split("COMPLETE PRIOR MODEL OUTPUTS:\n", 1)[1])[0]
            assert [item["input_id"] for item in inputs["records"]] == request["input_ids"]
            assert all(hashlib.sha256(item["raw_response"].encode()).hexdigest() == request["input_sha256"][item["input_id"]] for item in inputs["records"])
            sources = [{"input_id": name, "finding_ids": list(group["findings"])}
                       for name, group in inputs["positive_findings"].items() if group["findings"]]
            return json.dumps({**request, "scope": "scoped_adjudication", "retained_findings": inputs["retained_findings"],
                               "decisions": [{"decision_id": "positive-1", "conclusion": "Scoped original-role evidence adjudicated against exact source references; no global acceptance asserted.",
                                              "obligation_ids": [], "sources": sources}] if sources else []})
        if ":context:" in node:
            self._record_route(client, prompt, system)
            if self.wait_for_deadline:
                while True:
                    client._raise_if_cancelled()
                    time.sleep(.01)
            request = json.loads(prompt.split("EVIDENCE READ REQUEST:\n", 1)[1].split("\nReturn ONLY strict JSON", 1)[0])
            text = prompt.split("=== EXACT EVIDENCE RANGE ===\n", 1)[1].split("\n=== END EXACT EVIDENCE RANGE ===", 1)[0]
            obligations = json.loads(prompt.split("COMPLETE REVIEW OBLIGATIONS:\n", 1)[1].split("\n\nFull evidence is immutable data.", 1)[0])
            encoded = text.encode()
            assert len(encoded) == request["end_byte"] - request["start_byte"]
            assert hashlib.sha256(encoded).hexdigest() == request["page_sha256"]
            assert any(phase in prompt for phase in ("DSH COMPOSITE PHASE: QUALITY REVIEW ONLY", "DSH COMPOSITE PHASE: PLAN ONLY", "DSH CHILD ASSIGNMENT"))
            self.parts.setdefault(request["context_id"], {}).setdefault(request["path"], []).append((request["start_byte"], encoded))
            self.context_routes.append((node, client.route()))
            quote = text[:32]
            value = {key: request[key] for key in ("context_id", "logical_node_id", "role", "page_id", "document_sha256", "start_byte", "end_byte", "page_sha256")}
            value.update(schema_version=1, scope="page_only",
                coverage=[{"obligation_id": item["id"], "status": "evidence", "reason": f"Read exact evidence for {item.get('text', item.get('text_reference'))} in this range."} for item in obligations],
                claims=[{"claim_id": request["page_id"], "verdict": "supported", "reason": "The supplied original range preserves this concrete source excerpt.",
                         "start_byte": request["start_byte"], "end_byte": request["start_byte"] + len(quote.encode()), "quote": quote}])
            return json.dumps(value, ensure_ascii=False)
        if "COMPLETE VERIFIED SAME-ROLE READ RECORDS:\n" in prompt:
            self._record_route(client, prompt, system)
            basis, _ = json.JSONDecoder().raw_decode(prompt.split("COMPLETE VERIFIED SAME-ROLE READ RECORDS:\n", 1)[1])
            documents = {}
            for document in basis["documents"]:
                if document["reviewed_as"] != "complete byte ranges":
                    continue
                chunks = sorted(self.parts[basis["context_id"]][document["path"]])
                cursor = 0
                body = b""
                for start, chunk in chunks:
                    assert start == cursor
                    body += chunk
                    cursor += len(chunk)
                assert cursor == document["bytes"]
                assert hashlib.sha256(body).hexdigest() == document["sha256"]
                documents[document["path"]] = body
            self.reconstructed[basis["context_id"]] = documents
            marker = re.search(r"exactly this binding marker: (CONTEXT_RECONCILIATION_SHA256=[a-f0-9]{64})", prompt)[1]
            if ":plan:" in node:
                (Path(workdir) / directors.RESEARCH.plan).write_text(json.dumps(self.plans[directors.RESEARCH.id]))
                self.calls.append((node, client.route()))
                return "Wrote the original typed plan after complete evidence review. " + marker
            if ":child-" in node:
                identifier = node.split(":")[2]
                output = Path(workdir) / f"{directors.RESEARCH.reports}/{identifier}.md"
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text(self._large_child_body(node))
                self.calls.append((node, client.route()))
                return "Wrote the substantive assigned report after complete evidence review. " + marker
            joined = next(json.loads(body) for name, body in documents.items() if "/join-" in name)
            reports = joined["reports"]
            self.full_quality_bodies.append([report["body"] for report in reports])
            self.calls.append((node, client.route()))
            retry = self.retry and node.endswith(":quality:a1")
            decisions = [{"id": report["task_id"], "verdict": "retry" if retry and report["task_id"] == "child-1" else "accept",
                          "reason": "A second independent probe is needed" if retry else "Full reports and original obligations reconciled",
                          "retry_prompt": "Execute the second probe for this same focus and retain both attempts.", "outcome": "findings"} for report in reports]
            return json.dumps({"decisions": decisions, "coverage_complete": not retry,
                               "summary": "Reconciled every complete source range and report. " + ("" if self.omit_marker else marker)})
        body = super().complete(client, prompt, system=system, workdir=workdir)
        if ":child-" in node:
            # A large report with literal template braces and multibyte text;
            # no range, prior attempt, or substantive report can be truncated.
            body += "\n" + (f"Unique evidence {node}: αβγ🙂 {{{{name}}}}\n" * 1600)
        return body


def run_large_stage(tmp_path, monkeypatch, responses, *, large_inputs=False, stage_timeout=None):
    monkeypatch.setattr(DshModelClient, "_complete", lambda client, *args, **kwargs: responses.complete(client, *args, **kwargs))
    seed_inputs(tmp_path, directors.RESEARCH)
    if large_inputs:
        (tmp_path / ".factory/strategy/study-combined.md").write_bytes(("Full study α🙂 literal {{name}} evidence\r\n" * 10000).encode())
    graph = Graph()
    graph.add(Node(id=directors.RESEARCH.id, kind="op", role="ceo", call="refactory_dsh.create_fidelity_directors:research_director", writes=(".factory/",)))
    client = DshModelClient(provider="fixture", model="default", effort="high", attempts=1,
                            role_routes={"ceo": {"model": "ceo-role-default"}},
                            node_routes={directors.RESEARCH.id: {"model": "original-director-route"},
                                         "child-1": {"model": "original-child-route"}})
    client = client.with_node_timeout(stage_timeout)
    context = RunContext(tmp_path, model=client, task={"trial_id": "bounded-director-trial"})
    result = Executor(graph, context).run()
    manifest = json.loads((tmp_path / ".factory/research_director-findings.json").read_text())
    return result, manifest


def test_large_quality_reads_full_reports_same_route_and_retains_all_attempts(tmp_path, monkeypatch):
    responses = CompleteEvidenceResponses(retry=True)
    result, manifest = run_large_stage(tmp_path, monkeypatch, responses)
    assert result.exit_ok, result.error
    assert manifest["status"] == "completed"
    assert len(manifest["attempts"]) == 4
    assert len(manifest["quality_reviews"]) == 2
    assert responses.context_routes
    assert all(route["model"] == "original-director-route" for _, route in responses.context_routes)
    full_markdown = (tmp_path / ".factory/research_director-findings.md").read_text()
    for report in manifest["reports"]:
        assert report["body"] in full_markdown
        assert report["body"] in responses.full_quality_bodies[-1]
    prior_path = ".factory/directors/research_director/v1/child-1-a1.json"
    assert any(prior_path in documents for documents in responses.reconstructed.values())
    contexts = []
    for review in manifest["quality_reviews"]:
        receipt = json.loads((tmp_path / review["raw_receipt"]).read_text())
        context = json.loads((tmp_path / receipt["bounded_context"]["manifest"]).read_text())
        assert context["status"] == "reconciled"
        assert context["reconciliation_digest"] == receipt["bounded_context"]["digest"]
        assert context["logical_node_id"] == receipt["node_id"]
        universe = next(document for document in context["documents"] if document["path"] == "director-decision-universe.json")
        universe_bytes = (tmp_path / universe["snapshot"]).read_bytes()
        actual_plan = json.loads((tmp_path / directors.RESEARCH.plan).read_text())
        assert json.loads(universe_bytes) == directors._quality_decision_universe(actual_plan)
        assert universe["provenance"]["source_sha256"] == hashlib.sha256((tmp_path / directors.RESEARCH.plan).read_bytes()).hexdigest()
        assert context["final"]["uttered_reconciliation_marker"] == MARKER + context["reconciliation_digest"]
        assert context["final"]["worker_receipts"] == receipt["raw_evidence"]["worker_receipts"]
        assert all(page["role"] == "ceo" and page["worker_receipts"] for page in context["reviews"])
        for dispatch in [*context["reviews"], context["final"]]:
            supplied = (tmp_path / dispatch["supplied_prompt"]["snapshot"]).read_bytes()
            assert universe_bytes in supplied
            assert len(dispatch["system"].encode()) + 2 + len(supplied) <= context["policy"]["max_prompt_bytes"]
        contexts.append(context)
    assert any(document["path"] == prior_path for document in contexts[-1]["documents"])
    assert any(document["path"].endswith("quality-1.json") for document in contexts[-1]["documents"])


def test_large_quality_missing_actual_reconciliation_is_failed(tmp_path, monkeypatch):
    responses = CompleteEvidenceResponses(omit_marker=True)
    result, manifest = run_large_stage(tmp_path, monkeypatch, responses)
    assert not result.exit_ok
    assert manifest["status"] == "failed" and manifest["quality_reviews"] == []
    receipt = json.loads((tmp_path / ".factory/directors/research_director/v1/quality-1-receipt.json").read_text())
    assert receipt["status"] == "failed"
    assert "did not reconcile" in receipt["error"]
    context = json.loads((tmp_path / receipt["bounded_context"]["manifest"]).read_text())
    assert context["status"] == "failed"
    assert len(manifest["reports"]) == 3
    assert all(report["body"] for report in manifest["reports"])


def test_large_plan_and_children_review_all_declared_input_bytes_without_template_render(tmp_path, monkeypatch):
    responses = CompleteEvidenceResponses()
    result, manifest = run_large_stage(tmp_path, monkeypatch, responses, large_inputs=True)
    assert result.exit_ok, result.error
    assert manifest["status"] == "completed"
    study_path = ".factory/strategy/study-combined.md"
    study_bytes = (tmp_path / study_path).read_bytes()
    plan_receipt = json.loads((tmp_path / ".factory/directors/research_director/v1/plan-receipt.json").read_text())
    for receipt in [plan_receipt, *manifest["reports"]]:
        context = json.loads((tmp_path / receipt["bounded_context"]["manifest"]).read_text())
        assert context["status"] == "reconciled"
        assert responses.reconstructed[context["context_id"]][study_path] == study_bytes
        assert context["role"] == receipt["role"]
        assert context["requested_route"] == receipt["requested_route"]
    child_one = manifest["reports"][0]
    assert child_one["requested_route"]["model"] == "original-child-route"
    assert any(":child-1:" in node and route["model"] == "original-child-route" for node, route in responses.context_routes)
    assert plan_receipt["requested_route"]["model"] == "original-director-route"
    assert all("{{name}}" in report["body"] for report in manifest["reports"])


def test_staged_quality_deadline_retains_failed_read_provenance_and_settles_scope(tmp_path, monkeypatch):
    before = {thread.ident for thread in threading.enumerate()}
    responses = CompleteEvidenceResponses(wait_for_deadline=True)
    result, manifest = run_large_stage(tmp_path, monkeypatch, responses, stage_timeout=1)
    assert not result.exit_ok and manifest["status"] == "failed"
    receipt = json.loads((tmp_path / ".factory/directors/research_director/v1/quality-1-receipt.json").read_text())
    assert receipt["status"] == "failed"
    assert receipt["bounded_context"]["digest"] is None
    context = json.loads((tmp_path / receipt["bounded_context"]["manifest"]).read_text())
    assert context["status"] == "failed" and context["reviews"]
    assert context["reviews"][0]["status"] == "failed"
    assert context["reviews"][0]["worker_receipts"]
    assert "final" not in context
    assert not [thread for thread in threading.enumerate() if thread.ident not in before and thread.name.startswith(("ThreadPoolExecutor", "Thread-"))]


def test_mandatory_task_metadata_deduplicates_only_common_mapping(tmp_path):
    ctx = RunContext(tmp_path)
    plan = plan_for(directors.QA, 2)
    tasks = directors._mandatory_tasks(ctx)
    common = directors.render(directors._load_prompt("adversarial_tester"), ctx)
    quality = directors._review_instructions(ctx, directors.QA)
    for task in tasks:
        assert common not in task["prompt"]
        child = directors._child_instructions(ctx, directors.QA, task, plan, {}, "")
        assert child.count(common) == 1
        assert task["prompt"] in child
        assert "shared" in task["prompt"] and "disposable" in task["prompt"]
        obligations = directors._child_context_obligations(task, plan)
        assert any(item["text"] == task["prompt"] for item in obligations)
    # The CEO also receives the native common mapping independently of tasks.
    assert "native" in quality.lower() and "shared delivered package" in quality


def test_director_receipt_preserves_actual_crlf_report_bytes_and_output_digest(tmp_path, monkeypatch):
    responses = ExternalResponses({directors.RESEARCH.id: plan_for(directors.RESEARCH, 3)})
    def complete(client, prompt, **kwargs):
        body = responses.complete(client, prompt, **kwargs)
        return body.replace("\n", "\r\n") if ":child-" in client._node_id else body
    monkeypatch.setattr(DshModelClient, "_complete", complete)
    seed_inputs(tmp_path, directors.RESEARCH)
    graph = Graph()
    graph.add(Node(directors.RESEARCH.id, "op", call="refactory_dsh.create_fidelity_directors:research_director", writes=(".factory/",)))
    result = Executor(graph, RunContext(tmp_path, model=DshModelClient(provider="fixture", model="sol-6.1", effort="high", attempts=1))).run()
    assert result.exit_ok, result.error
    manifest = json.loads((tmp_path / ".factory/research_director-findings.json").read_text())
    for report in manifest["reports"]:
        actual = (tmp_path / report["output"]).read_bytes()
        assert b"\r\n" in actual
        assert report["body"].encode() == actual
        assert report["output_digest"] == hashlib.sha256(actual).hexdigest()
        assert report["body"] in responses.full_quality_bodies[0]
