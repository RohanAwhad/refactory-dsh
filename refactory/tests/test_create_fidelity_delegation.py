"""Controlled delegated reviews exercise real pipe ownership and Mode.resume.

These fixtures prove the protocol; they never authorize or certify a live trial.
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import threading
import time

import pytest

from benchmarks.create_fidelity import approval, harness
from benchmarks.create_fidelity.contract import digest
from benchmarks.create_fidelity.operator_evidence import OperatorEvidenceContext, review_template, supplemental_row
from refactory_dsh import nn


def controlled_inputs(ctx):
    ctx.write_text(approval.DEFAULT_INPUTS[0], "Controlled strategy: preserve the requested booking contract.\n")
    ctx.write_text(approval.DEFAULT_INPUTS[1], "Controlled original intent: report overlapping bookings.\n")
    return {"create_fidelity": True}


def controlled_done(ctx):
    ctx.write_text("done.txt", "actual native resume completed")


class ControlledCandidate(nn.Mode):
    name = "create_fidelity"
    unscored_reason = "Controlled delegated approval fixture"

    @classmethod
    def declare(cls):
        return {name: nn.Knob(value, "str") for name, value in (
            ("name", ""), ("brief", ""), ("requires_input", ""))}

    def build(self):
        graph = nn.Graph()
        graph.add(nn.Node("init", "op", call=f"{__name__}:controlled_inputs", writes=approval.DEFAULT_INPUTS,
                          produces=("create_fidelity",)))
        graph.add(nn.Node("gate_strategy", "gate", gate_kind="user", reads=approval.DEFAULT_INPUTS,
                          prompt_template="Review the actual strategy and original intent; proceed, revise, or halt."))
        graph.add(nn.Node("done", "op", call=f"{__name__}:controlled_done", writes=("done.txt",)))
        graph.connect("init", "gate_strategy")
        graph.connect("gate_strategy", "done", condition="proceed")
        graph.connect("gate_strategy", "init", condition="reloop")
        return graph


def trusted(root):
    return {"authorization": "operator-strategy-review", "trial_id": root.name,
            "lane_ids": ["dsh-candidate"], "gate": "gate_strategy", "operator_id": "controlled operator",
            "user_message_reference": "controlled actual delegation message",
            "user_turn_reference": "controlled actual delegation turn",
            "user_thread_reference": "controlled actual thread",
            "authorization_text": "Review each actual strategy and continue only after your independent decision."}


def review(project, root, pending):
    return {"schema_version": 1, "decision_source": "delegated-operator", "human_inspected_strategy": False,
            "trial_id": root.name, "lane_id": "dsh-candidate", "gate": "gate_strategy",
            "reviewer_id": "controlled operator", "strategy_digest": pending["strategy_digest"],
            "input_paths": pending["input_paths"], "request_created_unix": pending["created_unix"],
            "request_sha256": digest(project / "approval-required.json"), "verdict": "proceed", "feedback": "",
            "rationale": "Controlled reviewer compared the contract in both actual inputs; this is protocol test evidence only.",
            "evidence": [{"path": name, "sha256": digest(project / name), "quote": (project / name).read_text().strip()}
                         for name in pending["input_paths"]]}


@pytest.fixture
def pending_review(tmp_path):
    project = tmp_path / "project"
    (project / ".factory/strategy").mkdir(parents=True)
    for name in approval.DEFAULT_INPUTS:
        (project / name).write_text("actual input " + name)
    pending = approval.request(project, "actual question", trial_id=tmp_path.name, lane_id="dsh-candidate")
    return project, tmp_path, pending


@pytest.mark.parametrize("defect", ["wrong-trial", "wrong-lane", "standing-verdict", "stale-input", "stale-request", "missing-citation", "blank-rationale", "human-inspection-claim"])
def test_delegated_decision_rejects_scope_staleness_and_unreviewed_claims(pending_review, defect):
    project, root, pending = pending_review
    authority, record = trusted(root), review(project, root, pending)
    if defect == "wrong-trial": authority["trial_id"] = "other"
    elif defect == "wrong-lane": authority["lane_ids"] = ["remote-reference"]
    elif defect == "standing-verdict": authority["verdict"] = "proceed"
    elif defect == "stale-input": (project / approval.DEFAULT_INPUTS[0]).write_text("changed")
    elif defect == "stale-request": record["request_created_unix"] -= 1
    elif defect == "missing-citation": record["evidence"].pop()
    elif defect == "blank-rationale": record["rationale"] = ""
    else: record["human_inspected_strategy"] = True
    with pytest.raises(ValueError):
        approval.operator_answer(project, review=record, delegation_reference="actual-delegation",
            trusted_delegation=authority, trial_id=root.name, lane_id="dsh-candidate",
            operator_id="controlled operator", review_sha256="a" * 64)


def test_lane_file_cannot_deliver_delegated_authority(pending_review):
    project, root, pending = pending_review
    answer = approval.operator_answer(project, review=review(project, root, pending), delegation_reference="actual-delegation",
        trusted_delegation=trusted(root), trial_id=root.name, lane_id="dsh-candidate",
        operator_id="controlled operator", review_sha256="a" * 64)
    (project / "human-answer.json").write_text(json.dumps(answer))
    with pytest.raises(ValueError, match="Lane-authored"):
        approval.take(project, pending)
    assert json.loads((project / "approval-required.json").read_text())["status"] == "awaiting-human"


@pytest.mark.parametrize("single_lane", [False, True])
def test_operator_pipe_consumes_exact_review_through_actual_mode_resume(tmp_path, single_lane):
    root = tmp_path / "controlled-trial"
    project = root / "dsh-candidate/project"
    project.mkdir(parents=True)
    (project / "BRIEF.md").write_text("Controlled requested booking workflow")
    for lane in (["dsh-candidate"] if single_lane else harness.LANES):
        harness.write(root / lane / "config.json", {"project": str(root / lane / "project")})
    harness.write(root / "operator/review-context.json", {"reviewer_id": "controlled operator",
        "trusted_operator_delegations": {"actual-delegation": trusted(root)}})
    driver = root / "driver.py"
    driver.write_text('''import json,sys
from pathlib import Path
from types import SimpleNamespace
from benchmarks.create_fidelity.lane import dsh_author
from benchmarks.create_fidelity.supervision import LaneSupervisor
from refactory_dsh import registry,model
from test_create_fidelity_delegation import ControlledCandidate
registry.load=lambda *args: SimpleNamespace(mode=lambda _: ControlledCandidate)
model.DshModelClient=lambda **kwargs: SimpleNamespace(route=lambda: {})
project=Path(sys.argv[1])
with LaneSupervisor(project,20,4):
    result=dsh_author({'mode':'create_fidelity','model_context':{},'total_timeout_s':20,
                       'trial_id':sys.argv[2],'lane':'dsh-candidate'},project)
    (project/'result.json').write_text(json.dumps(result))
assert result['exit_ok'],result
''')
    outcomes, errors = [], []
    def controller():
        try:
            outcomes.append(harness.execute_command([sys.executable, str(driver), str(project), root.name], cwd=project,
                env={**os.environ, "PYTHONPATH": os.pathsep.join([str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parent)]),
                     "PYTHONDONTWRITEBYTECODE": "1"}, output=project / "raw/controller", timeout=20, grace=4,
                control_messages=harness.decision_messages(root, "dsh-candidate")))
        except BaseException as error:
            errors.append(error)
    thread = threading.Thread(target=controller)
    thread.start()
    pending_path = project / "approval-required.json"
    until = time.monotonic() + 8
    while not pending_path.exists() and thread.is_alive() and time.monotonic() < until:
        time.sleep(.02)
    assert pending_path.exists(), (errors, (project / "raw/controller/stderr.log").read_text())
    pending = json.loads(pending_path.read_text())
    assert not (project / "done.txt").exists()
    operator_review = root / "operator/strategy-review.json"
    harness.write(operator_review, review(project, root, pending))
    queued = harness.operator_decision(root, "dsh-candidate", operator_review, "actual-delegation")
    assert queued["human_inspected_strategy"] is False
    thread.join(15)
    assert not thread.is_alive() and not errors
    assert outcomes[0]["status"] == "completed", (outcomes, (project / "raw/controller/stderr.log").read_text())
    cleanup = outcomes[0]["lane_lifecycle"]["owned_group_cleanup"]
    assert cleanup["initial_members"] == [] and cleanup["actions"] == []
    assert cleanup["completion"] == "no_residual_members_observed" and cleanup["confirmed_empty"] is True
    assert (project / "done.txt").read_text() == "actual native resume completed"
    request = json.loads(pending_path.read_text())
    answer = json.loads((project / request["answer_path"]).read_text())
    consumption = json.loads(next((project / "raw/approvals").glob("native-consumption-*.json")).read_text())
    assert answer["source"] == consumption["decision_actor"] == "delegated-operator"
    assert consumption["human_inspected_strategy"] is False
    assert len(consumption["ledger_uses"]) == 1
    assert consumption["logical_gate_owner"] == "user"
    with pytest.raises(ValueError, match="unconsumed|already"):
        harness.operator_decision(root, "dsh-candidate", operator_review, "actual-delegation")

    # Supplemental identity certifies this distinct authorization profile only.
    context = OperatorEvidenceContext(root=root / "operator", trial_id=root.name, lane_id="dsh-candidate",
        reviewer_id="controlled operator", lane_roots=tuple(root / lane / "project" for lane in harness.LANES),
        trusted_operator_delegations={"actual-delegation": trusted(root)})
    identity = review_template(project, context, "identity")
    identity.update(reviewed_at=datetime.now(timezone.utc).isoformat(), verdict="passed",
        rationale="Controlled protocol test: actual delegated review and one-use native receipt, no direct-human inspection claim.",
        decision_source="delegated-operator", delegation_reference="actual-delegation", human_inspected_strategy=False,
        decision="proceed", strategy_digest=pending["strategy_digest"], input_paths=pending["input_paths"],
        answer_evidence_path=request["answer_path"], request_evidence_path="approval-required.json",
        native_receipt_path=consumption["native_gate_receipts"][0], operator_review_path="strategy-review.json")
    harness.write(context.root / "dsh-candidate/human-approval.json", identity)
    row = supplemental_row("F.approval.identity", "identity", project, context)
    assert row["status"] == "passed", row
    assert row["authorization_profile"] == "benchmark-delegated-strategy-review/v1"
    identity["human_inspected_strategy"] = True
    harness.write(context.root / "dsh-candidate/human-approval.json", identity)
    assert supplemental_row("F.approval.identity", "identity", project, context)["status"] == "failed"
