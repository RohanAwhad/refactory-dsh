"""V13 strategy-read regression: document labels are not active coverage IDs."""
import json
import time

import pytest

from refactory_dsh import create_fidelity_directors as directors
from refactory_dsh.create_fidelity_context import ContextPolicy
from refactory_dsh.model import DshModelClient
from refactory_dsh.nn.executor import RunContext
from test_create_fidelity_context import ExternalModel


@pytest.mark.parametrize("unknown_coverage", [False, True])
def test_strategy_read_separates_coverage_ids_from_source_requirement_labels(tmp_path, monkeypatch, unknown_coverage):
    # The failed live read used these two active obligations but also returned
    # B/I/C document labels as coverage IDs. Keep those real labels as evidence;
    # never filter them out of model output or relax the strict validator.
    active = ["architecture", "testing-verification"]
    labels = [*[f"B{i:02d}" for i in range(1, 11)], "I14", "I18", "I19", "C01", "C02", "C03", "C04", "C05", "C07"]
    body = ("B08: candidate validation failed; " + ", ".join(labels) + " are source requirement labels.\n") * 600
    (tmp_path / "research.md").write_text(body)

    class Responses(ExternalModel):
        def complete(self, client, prompt, **kwargs):
            raw = super().complete(client, prompt, **kwargs)
            if "EVIDENCE READ REQUEST:\n" not in prompt:
                return raw
            allowed = json.JSONDecoder().raw_decode(prompt.split("CLOSED COVERAGE ID VOCABULARY:\n", 1)[1])[0]
            assert allowed == active
            assert "Other IDs appearing in instructions or evidence are document labels" in prompt
            value = json.loads(raw)
            assert [item["obligation_id"] for item in value["coverage"]] == active
            value["claims"][0]["verdict"] = "adverse"
            value["claims"][0]["reason"] = "B08 records failed candidate validation; preserve this finding despite B08 not being an active coverage ID."
            if unknown_coverage:
                value["coverage"].append({"obligation_id": "B08", "status": "adverse", "reason": "Actual source label, but invalid in the closed coverage vocabulary."})
            return json.dumps(value)

    responses = Responses(tmp_path, role="ceo")
    monkeypatch.setattr(DshModelClient, "_complete", lambda client, prompt, **kwargs: responses.complete(client, prompt, **kwargs))
    ctx = RunContext(tmp_path, model=DshModelClient(provider="fixture", model="gpt-6-sol", effort="high", attempts=1))
    ctx._create_fidelity_context_policy = ContextPolicy(max_prompt_bytes=32768)
    ctx._director_deadline = time.monotonic() + 20
    report = directors._run_agent(ctx, "strategy_director:v1:plan:a1", "ceo", body, "report.md", ("research.md",),
        {"stage_id": "strategy_director", "task_id": "plan"}, instructions="Original strategy planning duty; preserve all source findings.",
        documents={"research.md": body}, obligations=[{"id": item, "text": "Complete " + item + " duty."} for item in active])
    manifest = json.loads((tmp_path / report["bounded_context"]["manifest"]).read_text())
    if unknown_coverage:
        assert report["status"] == "failed" and "invalid obligation coverage" in report["error"]
        assert not responses.final_called and manifest["status"] == "failed"
        raw = json.loads((tmp_path / manifest["reviews"][0]["raw_response"]["snapshot"]).read_text())
        assert raw["coverage"][-1]["obligation_id"] == "B08"
    else:
        assert report["status"] == "completed", report["error"]
        assert manifest["status"] == "reconciled" and responses.final_called
        assert b"".join(data for _, _, data in responses.ranges) == body.encode()
        packet = json.loads((tmp_path / manifest["final_packet"]["snapshot"]).read_text())
        for item in packet["reviews"]:
            value = json.loads(item["raw_response"])
            assert {x["obligation_id"] for x in value["coverage"]} <= set(active)
            assert value["claims"][0]["verdict"] == "adverse"
            assert "B08 records failed candidate validation" in value["claims"][0]["reason"]
        assert all(role == "ceo" and route["model"] == "gpt-6-sol" for _, role, route, _ in responses.calls)
