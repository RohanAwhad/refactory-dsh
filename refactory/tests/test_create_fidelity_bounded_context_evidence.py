"""Independent evidence checks over real bounded reads; only model I/O is fake."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

import pytest

from benchmarks.create_fidelity.bounded_context_evidence import verify_bounded_context
from benchmarks.create_fidelity.contract import reference
from benchmarks.create_fidelity.evidence import _director_evidence_score, _delivery_prompt, authoring_score
from refactory_dsh.agent_delivery import begin, finish
from refactory_dsh.create_fidelity_context import (ContextPolicy, Document, PreparedPrompt,
    bind_final, close_context, prepare_context, validate_final)
from refactory_dsh.model import DshModelClient
from refactory_dsh.nn.executor import RunContext, _ask_model
from refactory_dsh.nn.graph import Node


def build_evidence(root, monkeypatch, stage="health_checker"):
    source = reference()
    definition = source["graph"]["nodes"][stage]
    role = definition.get("role") or "ceo"
    role_text = next(body for name, body in source["role_prompts"].items()
                     if Path(name).stem == role)
    stage_text = definition.get("prompt_template", definition.get("gate_prompt"))
    logical = "qa_director:v1:quality:a1" if stage == "qa_director" else stage
    original = "First complete source evidence — café 漢字.\n" + ("1234567890 café 漢字 evidence.\n" * 5000)
    source_path = ".factory/reviews/full-source.md"
    path = root / source_path
    path.parent.mkdir(parents=True)
    path.write_text(original)
    for name, body in (("user-intent.md", "Preserve every integer and every full original report.\n"),
                       ("current.md", "- [ ] Preserve the complete source and original role.\n")):
        target = root / ".factory/strategy" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body)

    def external_completion(client, prompt, *, system="", workdir=None):
        # The controlled external observer supplies served configuration and
        # exact task-byte telemetry. Worker ownership, delivery files, immutable
        # snapshots, page splits, read validation and final binding are real.
        worker = json.loads(client._worker_receipt.read_text())
        task = (system + "\n\n" + prompt if system else prompt).encode()
        worker.update(actual_route={**client.route(), "source": "runtime-request"}, prompt_transport={"kind": "stdin_file",
            "encoding": "utf-8", "bytes": len(task), "sha256": hashlib.sha256(task).hexdigest()})
        client._worker_receipt.write_text(json.dumps(worker))
        if "EVIDENCE READ REQUEST:\n" in prompt:
            request = json.JSONDecoder().raw_decode(prompt.split("EVIDENCE READ REQUEST:\n", 1)[1])[0]
            full = (root / request["document_snapshot"]).read_bytes()
            quote = full[request["start_byte"]:request["end_byte"]].decode()[:80]
            return json.dumps({"schema_version": 1, "scope": "page_only",
                **{key: request[key] for key in ("context_id", "logical_node_id", "role", "page_id",
                    "document_sha256", "start_byte", "end_byte", "page_sha256")},
                "coverage": [{"obligation_id": "original-role-stage-and-user-contract", "status": "evidence",
                    "reason": "This exact source range was supplied for review; semantic acceptance remains separate."}],
                "claims": [{"claim_id": "source-excerpt", "verdict": "unresolved",
                    "reason": "Literal evidence observed; no final role verdict inferred.",
                    "start_byte": request["start_byte"], "end_byte": request["start_byte"] + len(quote.encode()),
                    "quote": quote}]}, ensure_ascii=False)
        marker = re.findall(r"CONTEXT_RECONCILIATION_SHA256=[a-f0-9]{64}", prompt)[-1]
        return "Independent semantic review remains required. " + marker

    monkeypatch.setattr(DshModelClient, "_complete", external_completion)
    client = DshModelClient(provider="fixture", model="gpt-6-sol", effort="high", attempts=1)
    ctx = RunContext(root, model=client)
    node = Node(logical, "agent", role=role, prompt_template="Original final task remains separate.")
    instructions = role_text + "\n\n" + stage_text
    prepared = prepare_context(ctx, node=node, instructions=instructions,
        documents=[Document(source_path, original)], direct_prompt=instructions + original,
        policy=ContextPolicy(max_prompt_bytes=131072))
    assert isinstance(prepared, PreparedPrompt)
    selected = prepared.client
    delivery = begin(root, logical, prepared, [], reads=[source_path], model_context=selected.context())
    bind_final(ctx, prepared, delivery)
    try:
        response = _ask_model(selected, delivery["prompt"], system="", workdir=root)
        actual = finish(root, logical, final_response=response, capture_final=False)
        validate_final(ctx, prepared, response, actual)
    finally:
        close_context(prepared)
    return prepared.manifest_path, dict(consumer_node=logical, role=role,
        role_text=role_text, stage_text=stage_text, required_documents={source_path: original},
        required_bodies=[original]), original


@pytest.mark.parametrize("stage", ["health_checker", "code_reviewer", "qa_director", "gate_qa"])
def test_complete_actual_same_role_reads_and_final_binding_are_verified(tmp_path, monkeypatch, stage):
    manifest, args, _ = build_evidence(tmp_path, monkeypatch, stage)
    refs = verify_bounded_context(tmp_path, manifest, **args)
    assert any("worker-" in name for name in refs)
    assert any("context-0.prompt" in name for name in refs)
    assert any("page-" in name and name.endswith(".response.json") for name in refs)
    record = json.loads((tmp_path / manifest).read_text())
    assert len(record["pages"]) > 1
    # Transport verification does not claim that these unresolved observations
    # constituted a correct health/code/director/AI-gate acceptance decision.
    assert all(review["review"]["claims"][0]["verdict"] == "unresolved" for review in record["reviews"])


@pytest.mark.parametrize("problem", ["gap", "overlap", "missing-read", "forged-quote", "stale-source",
    "changed-snapshot", "wrong-role", "wrong-route", "failed-worker", "missing-worker",
    "transport-hash", "prompt-only-summary", "forged-part-output", "stale-fingerprint",
    "missing-final-marker", "final-only-digest", "missing-source-duty", "legacy-version",
    "missing-obligations", "scope-drift"])
def test_missing_forged_stale_or_summary_only_reads_never_receive_full_body_credit(tmp_path, monkeypatch, problem):
    manifest_path, args, _ = build_evidence(tmp_path, monkeypatch)
    path = tmp_path / manifest_path
    manifest = json.loads(path.read_text())
    first = manifest["reviews"][0]
    worker_path = tmp_path / first["worker_receipts"][0]
    worker = json.loads(worker_path.read_text())
    if problem in ("gap", "overlap"):
        manifest["pages"][1]["start_byte"] += 1 if problem == "gap" else -1
    elif problem == "missing-read":
        manifest["reviews"].pop()
    elif problem == "forged-quote":
        manifest["reviews"][0]["review"]["claims"][0]["quote"] = "Summary claimed instead of exact source."
    elif problem == "stale-source":
        (tmp_path / manifest["documents"][0]["source_path"]).write_text("Changed source after actual reads.")
    elif problem == "changed-snapshot":
        (tmp_path / manifest["pages"][0]["snapshot"]).write_text("Changed frozen bytes.")
    elif problem == "wrong-role":
        worker["role"] = "builder"
    elif problem == "wrong-route":
        worker["actual_route"]["model"] = "different-model"
    elif problem == "failed-worker":
        worker["state"] = "failed"
    elif problem == "missing-worker":
        worker_path.unlink()
    elif problem == "transport-hash":
        worker["prompt_transport"]["sha256"] = "0" * 64
    elif problem == "prompt-only-summary":
        (tmp_path / first["supplied_prompt"]["snapshot"]).write_text("A summary claims every full file was reviewed.")
    elif problem == "forged-part-output":
        (tmp_path / first["raw_response"]["snapshot"]).write_text(json.dumps(first["review"]) + " changed")
    elif problem == "stale-fingerprint":
        delivery_path = tmp_path / first["delivery_receipt"]
        delivery = json.loads(delivery_path.read_text())
        delivery["execution"]["input_fingerprints"][manifest["documents"][0]["source_path"]] = "0" * 64
        delivery_path.write_text(json.dumps(delivery))
    elif problem == "missing-final-marker":
        (tmp_path / manifest["final"]["raw_response"]["snapshot"]).write_text("All done — PASS.")
    elif problem == "final-only-digest":
        (tmp_path / manifest["final"]["supplied_prompt"]["snapshot"]).write_text(manifest["reconciliation_digest"])
    elif problem == "missing-source-duty":
        args["role_text"] = "Source duty never supplied to this role."
    elif problem == "missing-obligations":
        # Rebind the actual delivered prompt and worker task bytes too: the
        # checker must independently require the complete criteria, rather
        # than rejecting only a stale asserted hash.
        data = (tmp_path / first["supplied_prompt"]["snapshot"]).read_bytes()
        rendering = json.dumps(manifest["obligation_rendering"], ensure_ascii=False,
                               sort_keys=True, separators=(",", ":")).encode()
        data = data.replace(b"COMPLETE REVIEW OBLIGATIONS:\n" + rendering,
                            b"Review criteria omitted.")
        delivery_path = tmp_path / first["delivery_receipt"]
        delivery = json.loads(delivery_path.read_text())
        for name in (first["supplied_prompt"]["snapshot"], delivery["contexts"][0]["prompt"]):
            (tmp_path / name).write_bytes(data)
        first["supplied_prompt"].update(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
        task = first["system"].encode() + b"\n\n" + data
        worker["prompt_transport"].update(bytes=len(task), sha256=hashlib.sha256(task).hexdigest())
        worker_path.write_text(json.dumps(worker))
        first["worker_receipt_sha256"][first["worker_receipts"][0]] = hashlib.sha256(worker_path.read_bytes()).hexdigest()
    elif problem == "scope-drift":
        entry = manifest["reviews"][1]
        changed = entry["application_before"]["snapshot"] + ".changed"
        data = b'{"report.md":{"sha256":"changed-application-between-reads"}}'
        (tmp_path / changed).write_bytes(data)
        for key in ("application_before", "application_after"):
            entry[key] = {"snapshot": changed, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    else:
        manifest["protocol"] = "legacy-unversioned-hash-claims"
    if problem in ("wrong-role", "wrong-route", "failed-worker", "transport-hash"):
        worker_path.write_text(json.dumps(worker))
    path.write_text(json.dumps(manifest))
    message = {"missing-obligations": "lossless review obligations", "scope-drift": "across its read scope"}.get(problem)
    with pytest.raises((ValueError, OSError), match=message):
        verify_bounded_context(tmp_path, manifest_path, **args)


def test_context_snapshot_symlink_and_fifo_are_rejected_without_reading(tmp_path, monkeypatch):
    manifest_path, args, _ = build_evidence(tmp_path, monkeypatch)
    manifest = json.loads((tmp_path / manifest_path).read_text())
    snapshot = tmp_path / manifest["instructions"]["snapshot"]
    actual = snapshot.with_name("actual-instructions.txt")
    snapshot.rename(actual)
    snapshot.symlink_to(actual)
    with pytest.raises(ValueError, match="symlink"):
        verify_bounded_context(tmp_path, manifest_path, **args)
    snapshot.unlink()
    import os
    os.mkfifo(snapshot)
    with pytest.raises(ValueError, match="regular file"):
        verify_bounded_context(tmp_path, manifest_path, **args)


@pytest.mark.parametrize("retry", [False, True])
def test_real_large_director_manifest_receives_strict_independent_evidence_credit(tmp_path, monkeypatch, retry):
    from test_create_fidelity_director_context import CompleteEvidenceResponses, run_large_stage
    responses = CompleteEvidenceResponses(retry=retry)
    outcome, manifest = run_large_stage(tmp_path, monkeypatch, responses, large_inputs=True)
    assert outcome.exit_ok, outcome.error
    result = _director_evidence_score("research_director", manifest, tmp_path, 3, 7, [])
    assert result["status"] == "passed", result
    assert any("create-fidelity-context" in path for path in result["evidence"])
    quality = json.loads((tmp_path / manifest["quality_reviews"][-1]["raw_receipt"]).read_text())
    raw = json.loads((tmp_path / quality["bounded_context"]["manifest"]).read_text())
    fixed = next(item for item in raw["fixed_documents"] if item.get("source_kind") == "derived_plan_universe")
    assert fixed["provenance"]["source_path"] == ".factory/strategy/research-plan.json"
    final_delivery_path = raw["final"]["delivery_receipt"]
    receipt = json.loads((tmp_path / final_delivery_path).read_text())
    workers = [(str(path.relative_to(tmp_path)), json.loads(path.read_text()))
               for path in (tmp_path / ".refactory/workers").glob("*.json")]
    _, refs = _delivery_prompt(tmp_path, (final_delivery_path, receipt), workers)
    assert any("page-" in path for path in refs)


def test_historical_original_reads_survive_authorized_later_bytes_without_current_credit(tmp_path, monkeypatch):
    manifest, args, original = build_evidence(tmp_path, monkeypatch)
    source = ".factory/reviews/full-source.md"
    old_inputs = {source: hashlib.sha256(original.encode()).hexdigest()}
    (tmp_path / source).write_text("Actual later authorized revised evidence.\n")
    with pytest.raises(ValueError, match="current source"):
        verify_bounded_context(tmp_path, manifest, **args)
    refs = verify_bounded_context(tmp_path, manifest, **args, historical=True, historical_inputs=old_inputs)
    assert any("scope=historical-repair-only" in ref for ref in refs)
    with pytest.raises(ValueError, match="original raw input"):
        verify_bounded_context(tmp_path, manifest, **args, historical=True,
                               historical_inputs={source: "0" * 64})


@pytest.mark.parametrize("corruption", [None, "old-input-hash", "gap", "missing-causal-builder"])
def test_actual_bounded_gate_reloop_retains_historical_proof_and_requires_causal_repair(tmp_path, monkeypatch, corruption):
    from test_create_fidelity_orchestration import begin_trial, approve_and_resume
    graph, ctx, responses, _ = begin_trial(tmp_path, monkeypatch, rework=True)
    pieces = {}

    def completion(client, prompt, *, system="", workdir=None):
        node = client._node_id
        if ":context:" in node:
            request = json.JSONDecoder().raw_decode(prompt.split("EVIDENCE READ REQUEST:\n", 1)[1])[0]
            page = prompt.split("=== EXACT EVIDENCE RANGE ===\n", 1)[1].split("\n=== END EXACT EVIDENCE RANGE ===", 1)[0]
            pieces.setdefault(request["context_id"], {}).setdefault(request["path"], []).append((request["start_byte"], page.encode()))
            quote = page[:32]
            raw = json.dumps({"schema_version": 1, "scope": "page_only",
                **{key: request[key] for key in ("context_id", "logical_node_id", "role", "page_id", "document_sha256", "start_byte", "end_byte", "page_sha256")},
                "coverage": [], "claims": [{"claim_id": "actual-source", "verdict": "unresolved",
                    "reason": "Observed exact input; final source gate must decide.", "start_byte": request["start_byte"],
                    "end_byte": request["start_byte"] + len(quote.encode()), "quote": quote}]}, ensure_ascii=False)
        elif "COMPLETE VERIFIED SAME-ROLE READ RECORDS:\n" in prompt:
            packet = json.JSONDecoder().raw_decode(prompt.split("COMPLETE VERIFIED SAME-ROLE READ RECORDS:\n", 1)[1])[0]
            chunks = sorted(pieces[packet["context_id"]]["runtime-gate-evidence.json"])
            request = json.loads(b"".join(body for _, body in chunks))
            # This is the actual external-model response, not a runtime verdict.
            # The real gate still applies its own guards and emits its receipt.
            marker = "CONTEXT_RECONCILIATION_SHA256=" + packet["reconciliation_digest"]
            raw = json.dumps({"schema_version": 1, "gate_id": node, "input_digest": request["input_digest"],
                "evidence_digest": request["evidence_digest"], "verdict": "reloop",
                "rationale": "User requested documented reusable native discovery; README.md is missing and must be supplied. " + marker})
        else:
            raw = responses.complete(client, prompt, system=system, workdir=workdir)
            if node == "builder" and responses.builder_count == 1:
                report = Path(workdir) / ".factory/reviews/builder-latest.md"
                report.write_text(report.read_text() + "\n" + "Complete retained raw builder observation.\n" * 7000)
        worker = json.loads(client._worker_receipt.read_text())
        task = (system + "\n\n" + prompt if system else prompt).encode()
        worker.update(actual_route={**client.route(), "source": "runtime-request"}, prompt_transport={
            "kind": "stdin_file", "encoding": "utf-8", "bytes": len(task), "sha256": hashlib.sha256(task).hexdigest()})
        client._worker_receipt.write_text(json.dumps(worker))
        return raw

    monkeypatch.setattr(DshModelClient, "_complete", completion)
    outcome, _, _ = approve_and_resume(graph, ctx)
    assert outcome.exit_ok, outcome.error
    root = ctx.workdir
    gate_path = next(path for path in (root / ".refactory/create-fidelity-gates").glob("*/verdict.json")
                     if json.loads(path.read_text()).get("verdict") == "reloop")
    gate = json.loads(gate_path.read_text())
    assert "bounded_context" in gate
    context_path = root / gate["bounded_context"]["manifest"]
    context = json.loads(context_path.read_text())
    assert context["status"] == "reconciled"
    assert any(item["source_kind"] == "derived_gate_request" for item in context["documents"])
    score = lambda: next(row for row in authoring_score([], {"exit_ok": True}, evidence_root=root)
                         if row["requirement_id"] == "F.repair")
    baseline = score()
    assert baseline["status"] == "passed", baseline
    assert any("scope=historical-repair-only" in ref for ref in baseline["evidence"])
    if corruption == "old-input-hash":
        import os
        stamp = gate_path.stat()
        gate["evidence"]["inputs"][".factory/reviews/builder-latest.md"] = "0" * 64
        digest = lambda data: hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        gate["input_digest"] = digest(gate["evidence"]["inputs"])
        gate["evidence_digest"] = digest(gate["evidence"])
        response_path = gate_path.parent / "model-response.txt"
        response = json.loads(response_path.read_text())
        response.update(input_digest=gate["input_digest"], evidence_digest=gate["evidence_digest"])
        response_path.write_text(json.dumps(response))
        gate_path.write_text(json.dumps(gate))
        os.utime(gate_path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    elif corruption == "gap":
        context["pages"][0]["start_byte"] += 1
        context_path.write_text(json.dumps(context))
    elif corruption == "missing-causal-builder":
        builders = [(path, json.loads(path.read_text())) for path in (root / ".refactory/agent-delivery").glob("*/receipt.json")
                    if json.loads(path.read_text()).get("node") == "builder"]
        max(builders, key=lambda pair: pair[1]["started"])[0].unlink()
    if corruption:
        assert score()["status"] != "passed", score()
