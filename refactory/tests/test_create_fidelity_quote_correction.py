"""One quote-only correction through real Executor/model/delivery ownership."""
from __future__ import annotations

import hashlib
import json
import re
import time

import pytest

from refactory_dsh.create_fidelity_context import ContextPolicy, MARKER, quote_semantics
from refactory_dsh.model import DshModelClient
from refactory_dsh.nn.executor import Executor, RunContext
from refactory_dsh.nn.graph import Graph, Node


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class QuoteResponses:
    """Fake only external response bytes; observers bind actual supplied bytes."""
    def __init__(self, root, problem=None):
        self.root, self.problem = root, problem
        self.calls, self.originals = [], {}
        self.final_called = False

    def complete(self, client, prompt, *, system="", workdir=None):
        task = (system + "\n\n" + prompt if system else prompt).encode()
        # The fake transport boundary observes the real inherited timer and
        # shared budget. No new timer or allowance is installed by telemetry.
        from refactory_dsh.workers import dispatch_deadlines
        dispatch_deadlines(client._worker_receipt, budget=client._budget,
                           stage_stop_at=time.monotonic() + client._node_timeout_s,
                           stage_timeout_s=client._node_timeout_s, attempt=1,
                           enclosing_deadlines=client._deadline_observations)
        worker = json.loads(client._worker_receipt.read_text())
        worker.update(actual_route={**client.route(), "source": "runtime-request"}, prompt_transport={
            "kind": "stdin_file", "encoding": "utf-8", "bytes": len(task),
            "sha256": hashlib.sha256(task).hexdigest()})
        if self.problem == "transport":
            worker["prompt_transport"]["sha256"] = "0" * 64
        if self.problem == "correction-transport" and client._node_id.endswith(":quote-correction-1"):
            worker["prompt_transport"]["sha256"] = "0" * 64
        client._worker_receipt.write_text(json.dumps(worker))
        self.calls.append({"identity": client._node_id, "role": client._role, "route": client.route(),
                           "task": task, "deadlines": client._deadline_observations,
                           "stage_s": client._node_timeout_s, "observed": time.monotonic()})
        if "EVIDENCE READ REQUEST:\n" not in prompt:
            self.final_called = True
            (self.root / "report.md").write_text("Final original role artifact.\n")
            return "Reconciled actual observations. " + re.findall(re.escape(MARKER) + r"[a-f0-9]{64}", prompt)[-1]
        request = json.JSONDecoder().raw_decode(prompt.split("EVIDENCE READ REQUEST:\n", 1)[1])[0]
        data = (self.root / request["document_snapshot"]).read_bytes()
        start = request["start_byte"]
        quote = data[start:start + 8].decode()
        correction = "QUOTE ANCHOR CORRECTION REQUEST:\n" in prompt
        if correction:
            prior = prompt.split("COMPLETE REJECTED MODEL RESPONSE:\n", 1)[1].rsplit(
                "\n=== END COMPLETE REJECTED MODEL RESPONSE ===", 1)[0]
            assert prior == self.originals[request["page_id"]]
            value = json.loads(prior)
            value["claims"][0].update(quote=quote, start_byte=start, end_byte=start + len(quote.encode()))
            if self.problem == "semantic-change":
                value["claims"][-1]["reason"] = "Changed the adverse finding."
            elif self.problem == "drop-finding":
                value["claims"].pop()
            elif self.problem == "second-anchor":
                value["claims"][0]["quote"] = "Bad again"
            elif self.problem == "correction-deadline":
                while True:
                    client._raise_if_cancelled()
                    time.sleep(.01)
            elif self.problem == "rejected-worker-drift":
                first_worker = next(path for path in (self.root / ".refactory/workers").glob("*.json")
                                    if json.loads(path.read_text())["node_id"] == self.calls[0]["identity"])
                first_worker.write_text("{}")
            return canonical(value)
        value = {"schema_version": 1, "scope": "page_only", **{key: request[key] for key in (
            "context_id", "logical_node_id", "role", "page_id", "document_sha256", "start_byte", "end_byte", "page_sha256")},
            "coverage": [{"obligation_id": "original-role-stage-and-user-contract", "status": "unresolved",
                          "reason": "Original obligation remains for final same-role reconciliation."}],
            "claims": [{"claim_id": f"c{i:02d}", "verdict": "adverse" if i % 2 else "unresolved",
                        "reason": f"Concrete preserved finding {i}.", "start_byte": start,
                        "end_byte": start + len(quote.encode()), "quote": quote}
                       for i in range(45 if request["page_id"] == "page-0001" else 1)]}
        if request["page_id"] == "page-0001":
            value["claims"][0]["quote"] = "Six missing zeroes"
            if self.problem == "later-claim":
                value["claims"][-1]["reason"] = ""
            elif self.problem == "coverage":
                value["coverage"].append({"obligation_id": "unknown", "status": "unresolved", "reason": "Not a valid ID."})
            elif self.problem == "identity":
                value["page_id"] = "other-page"
            elif self.problem == "application-drift":
                (self.root / "report.md").write_text("Unauthorized read-pass output.")
            elif self.problem == "source-drift":
                (self.root / "evidence.md").write_text("Changed full source bytes.")
            elif self.problem == "snapshot-drift":
                (self.root / request["document_snapshot"]).write_bytes(b"Changed immutable snapshot.")
            elif self.problem == "no-room":
                value["claims"][0]["reason"] += " Unique complete adverse detail." * 2000
            elif self.problem == "expire-before-correction":
                time.sleep(1.05)
            elif self.problem == "deadline-reduction":
                time.sleep(1.1)
        raw = canonical(value)
        self.originals[request["page_id"]] = raw
        return raw


def run_quote_stage(tmp_path, monkeypatch, *, problem=None, ceiling=65536, timeout=20, fixed_bytes=0,
                    prompt_template="Original stage: review complete actual evidence."):
    body = "01234567 café🙂 exact source zeroes 10000000000000000000\n" * 1500
    (tmp_path / "evidence.md").write_bytes(body.encode())
    if fixed_bytes:
        path = tmp_path / ".factory/strategy/current.md"
        path.parent.mkdir(parents=True)
        path.write_text("a" * fixed_bytes)
    responses = QuoteResponses(tmp_path, problem)
    monkeypatch.setattr(DshModelClient, "_complete", lambda client, prompt, **kw: responses.complete(client, prompt, **kw))
    client = DshModelClient(provider="fixture", model="gpt-6-sol", effort="high", attempts=1,
                            node_routes={"review": {"model": "specific-model", "effort": "high"}})
    from refactory_dsh.create_fidelity_timing import configure_client
    client = configure_client(client, timeout, wall_timeout_s=timeout)
    ctx = RunContext(tmp_path, facts={"create_fidelity": True}, model=client)
    ctx._create_fidelity_context_policy = ContextPolicy(max_prompt_bytes=ceiling)
    graph = Graph()
    graph.add(Node("review", "agent", role="health_checker", reads=("evidence.md",), writes=("report.md",),
                   prompt_template=prompt_template, timeout_s=timeout))
    result = Executor(graph, ctx).run()
    manifest_path = next(tmp_path.glob(".refactory/create-fidelity-context/*/*/manifest.json"))
    return result, json.loads(manifest_path.read_text()), responses, body, ctx


def test_quote_only_correction_preserves_complete_attempts_semantics_and_sole_leaf(tmp_path, monkeypatch):
    result, manifest, responses, body, _ = run_quote_stage(tmp_path, monkeypatch)
    assert result.exit_ok, result.error
    first = manifest["reviews"][0]
    correction = first["quote_correction"]
    rejected = correction["rejected_attempt"]
    assert rejected["status"] == "quote_rejected" and first["status"] == "reviewed"
    assert len(first["review"]["claims"]) == 45
    assert quote_semantics(first["review"]) == quote_semantics(rejected["review"])
    assert first["execution_node_id"] == rejected["execution_node_id"] + ":quote-correction-1"
    assert correction["max_extra_requests"] == 1
    assert correction["validation_failures"] == [{"claim_id": "c00", "reason": "literal quote does not equal its declared original UTF-8 byte slice"}]
    assert len({review["page_id"] for review in manifest["reviews"]}) == len(manifest["pages"])
    assert b"".join((tmp_path / page["snapshot"]).read_bytes() for page in manifest["pages"]) == body.encode()
    for attempt in (rejected, first):
        for key in ("supplied_prompt", "raw_response", "application_before", "application_after"):
            snapshot = attempt[key]
            raw = (tmp_path / snapshot["snapshot"]).read_bytes()
            assert hashlib.sha256(raw).hexdigest() == snapshot["sha256"]
        assert attempt["application_before"] == attempt["application_after"]
        worker_path = attempt["worker_receipts"][0]
        worker = json.loads((tmp_path / worker_path).read_text())
        delivery = json.loads((tmp_path / attempt["delivery_receipt"]).read_text())
        assert worker["state"] == "completed" and worker["role"] == "health_checker"
        assert worker["prompt_transport"]["sha256"] == hashlib.sha256((attempt["system"] + "\n\n").encode() +
            (tmp_path / attempt["supplied_prompt"]["snapshot"]).read_bytes()).hexdigest()
        assert delivery["status"] == "delivered"
    reserve = manifest["policy"]["applied_quote_correction_reserve_bytes"]
    room = manifest["policy"]["quote_correction_page_room_bytes"]
    assert reserve == min(32768, 65536 // 6, room // 3)
    assert all(len(call["task"]) <= 65536 for call in responses.calls)
    assert all(len(call["task"]) <= 65536 - reserve for call in responses.calls if ":page-" in call["identity"] and
               not call["identity"].endswith(":quote-correction-1"))
    assert responses.calls[0]["deadlines"] == responses.calls[1]["deadlines"]
    assert all(call["role"] == "health_checker" and call["route"]["model"] == "specific-model" for call in responses.calls)
    assert sum(call["identity"].endswith(":quote-correction-1") for call in responses.calls) == 1


@pytest.mark.parametrize("problem", ["later-claim", "coverage", "identity", "application-drift", "source-drift", "snapshot-drift", "transport", "no-room", "expire-before-correction"])
def test_other_invalidity_drift_no_room_or_expired_deadline_never_dispatch_correction(tmp_path, monkeypatch, problem):
    result, manifest, responses, _, _ = run_quote_stage(tmp_path, monkeypatch, problem=problem,
                                                       timeout=1 if problem == "expire-before-correction" else 20)
    assert not result.exit_ok and not responses.final_called
    assert manifest["status"] == "failed"
    assert len(responses.calls) == 1
    assert not any(call["identity"].endswith(":quote-correction-1") for call in responses.calls)
    assert manifest["reviews"][0]["status"] == "failed"


@pytest.mark.parametrize("problem", ["semantic-change", "drop-finding", "second-anchor", "correction-deadline", "correction-transport", "rejected-worker-drift"])
def test_failed_second_attempt_stays_preserved_without_semantic_repair_or_more_retries(tmp_path, monkeypatch, problem):
    result, manifest, responses, _, _ = run_quote_stage(tmp_path, monkeypatch, problem=problem,
                                                       timeout=1 if problem == "correction-deadline" else 20)
    assert not result.exit_ok and not responses.final_called and len(responses.calls) == 2
    entry = manifest["reviews"][0]
    assert entry["status"] == "failed" and entry["quote_correction"]["rejected_attempt"]["status"] == "quote_rejected"
    assert entry["quote_correction"]["rejected_attempt"]["raw_response"]
    assert entry["worker_receipts"] and entry["delivery_receipt"]
    if problem != "correction-deadline":
        assert entry["raw_response"] and entry["application_before"] == entry["application_after"]
    assert responses.calls[0]["deadlines"] == responses.calls[1]["deadlines"]
    assert responses.calls[1]["observed"] < dict(responses.calls[1]["deadlines"])["bounded_context"]


def test_small_custom_ceiling_reserve_does_not_starve_original_page_room(tmp_path, monkeypatch):
    # A fixed authority leaves little page space; the reserve is capped by the
    # measured available room instead of subtracting a blanket 32KB.
    result, manifest, responses, _, _ = run_quote_stage(tmp_path, monkeypatch, ceiling=32768, fixed_bytes=14000)
    room = manifest["policy"]["quote_correction_page_room_bytes"]
    reserve = manifest["policy"]["applied_quote_correction_reserve_bytes"]
    assert 0 < reserve <= room // 3 and room - reserve >= 2 * room // 3
    assert manifest["pages"][0]["bytes"] <= room - reserve
    assert all(len(call["task"]) <= 32768 for call in responses.calls)
    assert manifest["reviews"][0]["status"] == "reviewed"
    assert manifest["reviews"][0]["quote_correction"]["rejected_attempt"]["status"] == "quote_rejected"
    # Small pages can yield too many mandatory retained findings for final
    # reconciliation; that later honest failure does not justify starving reads.
    assert not result.exit_ok and "ceiling" in result.error


def test_correction_gets_remaining_original_deadline_not_a_new_allowance(tmp_path, monkeypatch):
    result, _, responses, _, _ = run_quote_stage(tmp_path, monkeypatch, problem="deadline-reduction", timeout=3)
    assert result.exit_ok, result.error
    assert responses.calls[0]["stage_s"] == 3 and responses.calls[1]["stage_s"] == 2
    assert responses.calls[0]["deadlines"] == responses.calls[1]["deadlines"]


@pytest.mark.parametrize("attempt", ["first", "correction"])
def test_actual_begin_prefix_is_checked_before_model_dispatch(tmp_path, monkeypatch, attempt):
    from refactory_dsh import agent_delivery
    real_begin = agent_delivery.begin

    def expanded_begin(workdir, identity, prompt, *args, **kwargs):
        if (attempt == "first" and not identity.endswith(":quote-correction-1")) or identity.endswith(":quote-correction-1"):
            prompt = "Real generated delivery prefix." * 3000 + prompt
        return real_begin(workdir, identity, prompt, *args, **kwargs)

    monkeypatch.setattr(agent_delivery, "begin", expanded_begin)
    result, manifest, responses, _, _ = run_quote_stage(tmp_path, monkeypatch)
    assert not result.exit_ok and "ceiling" in result.error
    assert len(responses.calls) == (1 if attempt == "correction" else 0) and not responses.final_called
    if attempt == "correction":
        assert manifest["reviews"][0]["raw_response"]


def test_first_actual_delivery_preserves_reserve_even_when_below_original_ceiling(tmp_path, monkeypatch):
    from refactory_dsh import agent_delivery
    original_contract = agent_delivery._execution_contract
    observed = []

    def expanded_contract(workdir, identity, prompt, *args, **kwargs):
        annotated, execution = original_contract(workdir, identity, prompt, *args, **kwargs)
        if ":context:" in identity:
            manifest = json.loads(next(workdir.glob(".refactory/create-fidelity-context/*/*/manifest.json")).read_text())
            reserve = manifest["policy"]["applied_quote_correction_reserve_bytes"]
            # The real begin path archives this entire added contract prefix.
            target = 65536 - reserve + 1
            size = len(("Evidence-only read by the original logical role.\n\n" + annotated).encode())
            annotated += "x" * (target - size)
            observed.append(target)
        return annotated, execution

    monkeypatch.setattr(agent_delivery, "_execution_contract", expanded_contract)
    result, manifest, responses, _, _ = run_quote_stage(tmp_path, monkeypatch)
    assert not result.exit_ok and "quote correction byte reserve" in result.error
    assert observed and observed[0] < 65536 and not responses.calls
    assert manifest["status"] == "failed"
