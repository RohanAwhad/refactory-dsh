"""Independent regressions for concurrent archive ownership and final settlement."""
import json
import threading
import time

from refactory_dsh.model import DshModelClient
from refactory_dsh.nn.executor import Executor, RunContext
from refactory_dsh.nn.graph import Graph, Node
from test_create_fidelity_context import ExternalModel, run_stage


read_started = threading.Event()
builder_finished = threading.Event()


def concurrent_builder(ctx):
    assert read_started.wait(3), "Archive evidence read was never dispatched"
    ctx.write_text(".factory/packages/generated/ops.py", "# Real concurrent builder output\n")
    ctx.write_json(".factory/performance_report.json", {"owner": "another archive visit"})
    ctx.write_text(".factory/archive/peer.md", "An independently owned archive note.\n")
    builder_finished.set()


def test_actual_nonblocking_archive_read_overlaps_real_builder_without_global_output_freeze(tmp_path, monkeypatch):
    read_started.clear(); builder_finished.clear()
    (tmp_path / "evidence.md").write_text("Original immutable role evidence.\n" * 12000)
    external = ExternalModel(tmp_path, role="archivist")
    def complete(client, prompt, **kwargs):
        if "EVIDENCE READ REQUEST:\n" in prompt:
            read_started.set()
            assert builder_finished.wait(3), "Nonblocking archive prevented builder from running"
        return external.complete(client, prompt, **kwargs)
    monkeypatch.setattr(DshModelClient, "_complete", complete)
    graph = Graph()
    graph.add(Node("archive", "agent", role="archivist", blocking=False,
                   prompt_template="Preserve full source evidence, then perform actual archive task.", reads=("evidence.md",),
                   writes=("report.md", ".factory/archive/own-note.md", ".factory/archive/", ".factory/performance_report.json"), timeout_s=10))
    graph.add(Node("builder", "op", call="test_create_fidelity_context_review:concurrent_builder",
                   writes=(".factory/packages/", ".factory/archive/peer.md", ".factory/performance_report.json")))
    graph.connect("archive", "builder")
    context = RunContext(tmp_path, facts={"create_fidelity": True}, model=DshModelClient(provider="fixture", model="sol-6.1", effort="high", attempts=1))
    result = Executor(graph, context).run()
    assert result.exit_ok, result.error
    assert read_started.is_set() and builder_finished.is_set()
    assert (tmp_path / ".factory/packages/generated/ops.py").read_text() == "# Real concurrent builder output\n"
    manifest_path = next(tmp_path.glob(".refactory/create-fidelity-context/*/*/manifest.json"))
    manifest = json.loads(manifest_path.read_text())
    assert manifest["status"] == "reconciled"
    inventory = json.loads((tmp_path / manifest["reviews"][0]["application_before"]["snapshot"]).read_text())
    assert set(inventory) == {"report.md", ".factory/archive/own-note.md"}
    assert external.final_called


def test_original_stage_deadline_also_bounds_final_delivery_settlement(tmp_path, monkeypatch):
    import refactory_dsh.agent_delivery as delivery
    original = delivery.finish
    def finish(workdir, node, **kwargs):
        if node == "review":
            time.sleep(1.05)
        return original(workdir, node, **kwargs)
    monkeypatch.setattr(delivery, "finish", finish)
    result, manifest, external, _ = run_stage(tmp_path, monkeypatch, timeout=1)
    assert external.final_called
    assert not result.exit_ok and "deadline expired" in result.error
    assert manifest["status"] == "failed"
    assert "uttered_reconciliation_marker" not in manifest["final"]
