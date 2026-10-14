"""Existing native builder delivery keeps its own backend/context policy."""
import json

import pytest

from refactory_dsh.create_fidelity_context import ContextPolicy, Document, prepare_context
from refactory_dsh.model import DshModelClient
from refactory_dsh.nn.executor import Executor, RunContext
from refactory_dsh.nn.graph import Graph, Node


@pytest.mark.parametrize("backend", ["codex", "claude_code"])
def test_oversized_native_builder_preserves_complete_direct_delivery_and_backend(tmp_path, monkeypatch, backend):
    data = ("Complete original input α🙂 {{literal}}\r\n" * 7000).encode()
    assert len(data) > ContextPolicy().max_prompt_bytes
    (tmp_path / "input.md").write_bytes(data)
    calls = []
    def complete(client, prompt, *, system="", workdir=None):
        calls.append((client.route(), prompt.encode(), system))
        record = json.loads(client._worker_receipt.read_text())
        record["actual_route"] = {**client.route(), "source": "controlled-native-response"}
        client._worker_receipt.write_text(json.dumps(record))
        (tmp_path / "builder.md").write_text("Native builder's actual file publication.\n")
        return "Native completion summary."
    monkeypatch.setattr(DshModelClient, "_complete", complete)
    graph = Graph()
    graph.add(Node("builder", "agent", role="builder", prompt_template="Original native builder instructions.", reads=("input.md",), writes=("builder.md",), timeout_s=30))
    client = DshModelClient(worker_backend="dsh", attempts=1,
                            role_routes={"builder": {"worker_backend": backend, "model": "native-fixture"}})
    result = Executor(graph, RunContext(tmp_path, facts={"create_fidelity": True}, model=client)).run()
    assert result.exit_ok, result.error
    assert len(calls) == 1 and calls[0][0]["worker_backend"] == backend
    expected = b"Original native builder instructions.\n\n=== COMPLETE DECLARED INPUT input.md ===\n" + data
    assert calls[0][1].endswith(expected) and calls[0][1].count(data) == 1
    assert len(calls[0][1]) > ContextPolicy().max_prompt_bytes
    assert not list(tmp_path.glob(".refactory/create-fidelity-context/*/*/manifest.json"))
    delivery = json.loads(next(tmp_path.glob(".refactory/agent-delivery/*/receipt.json")).read_text())
    supplied = (tmp_path / delivery["contexts"][-1]["prompt"]).read_bytes()
    assert supplied == calls[0][1] and "bounded_context" not in delivery
    assert (tmp_path / "builder.md").read_text() == "Native builder's actual file publication.\n"


@pytest.mark.parametrize("backend", ["codex", "claude_code"])
def test_oversized_native_nonbuilder_cannot_use_native_builder_exception(tmp_path, backend):
    direct = "Complete original source.\n" * 10000
    node = Node("review", "agent", role="health_checker", prompt_template="Original review duties.")
    ctx = RunContext(tmp_path, model=DshModelClient(worker_backend=backend, attempts=1))
    with pytest.raises(ValueError, match="original DSH ownership"):
        prepare_context(ctx, node=node, instructions=node.prompt_template,
                        documents=[Document("original.md", direct)], direct_prompt=direct)
    assert not list(tmp_path.glob(".refactory/create-fidelity-context/*/*/manifest.json"))
