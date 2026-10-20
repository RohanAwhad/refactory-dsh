"""Tests for the worker node: feedback sidecar in dispatched prompt (dm-110)."""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest import mock

import pytest


def test_feedback_file_prepends_heading_to_prompt(tmp_path):
    """dm-110: A manifest with feedback_file results in feedback under its heading."""
    from refactory_dsh.run import NODE_ENV

    # Set up the feedback file.
    fb_path = tmp_path / "feedback.md"
    fb_path.write_text("Please fix the widget alignment.")

    # Set up the prompt file.
    prompt_path = tmp_path / "prompt.md"
    prompt_path.write_text("Build the widget.")

    manifest = {
        "id": "emit",
        "type": "AgentNode",
        "command": "echo",
        "reads": [],
        "writes": [],
        "prompt_file": str(prompt_path),
        "feedback_file": str(fb_path),
    }

    spec = {
        "schema_version": 1,
        "node_id": "emit",
        "prompt": "fallback prompt",
        "reads": [],
        "writes": [],
        "model_context": {"provider": "test", "model": "test"},
    }

    captured_prompt = []

    class FakeClient:
        def __init__(self, **kwargs):
            pass
        def for_node(self, node):
            return self
        def complete(self, prompt, workdir=None):
            captured_prompt.append(prompt)
            return "done"
        def context(self):
            return {"provider": "test", "model": "test"}
        def route(self):
            return {"worker_backend": "dsh"}

    class FakeDelivery:
        @staticmethod
        def begin(workdir, node, prompt, writes, *, reads=None, model_context=None):
            captured_prompt.clear()
            captured_prompt.append(prompt)
            return {"prompt": prompt, "node": node, "status": "pending"}
        @staticmethod
        def finish(workdir, node, *, final_response=None, capture_final=False):
            return {"node": node, "status": "delivered"}
        @staticmethod
        def _path(workdir, name):
            return Path(workdir) / name

    # Mock at the source modules (deferred imports inside execute() import from
    # these, not from worker_node module-level attributes).
    with mock.patch.dict(os.environ, {NODE_ENV: json.dumps(manifest)}):
        with mock.patch("refactory_dsh.model.DshModelClient", FakeClient):
            with mock.patch("refactory_dsh.agent_delivery.begin", FakeDelivery.begin):
                with mock.patch("refactory_dsh.agent_delivery.finish", FakeDelivery.finish):
                    with mock.patch("refactory_dsh.agent_delivery._path", FakeDelivery._path):
                        with mock.patch("refactory_dsh.run._knobs", return_value={}):
                            with mock.patch("refactory_dsh.run._read_facts", return_value={}):
                                with mock.patch("refactory_dsh.nn.executor.render", side_effect=lambda p, c: p):
                                    with mock.patch("refactory_dsh.nn.executor.RunContext"):
                                        from refactory_dsh import worker_node
                                        worker_node.execute(spec, tmp_path)

    assert len(captured_prompt) > 0
    final_prompt = captured_prompt[0]
    assert "## Feedback for this visit" in final_prompt
    assert "Please fix the widget alignment." in final_prompt
    assert "Build the widget." in final_prompt
    # Feedback comes before the main prompt.
    fb_pos = final_prompt.index("Feedback for this visit")
    prompt_pos = final_prompt.index("Build the widget.")
    assert fb_pos < prompt_pos


def test_end_to_end_feedback_sidecar_crosses_ts_python_boundary(tmp_path):
    """dm-110 end-to-end: feedback sidecar written by drain() (TypeScript)
    reaches the dispatched agent's prompt via worker_node.execute() (Python).

    This test mirrors the exact contract between the two sides:
    - drain() writes ``.refactory/prompts/<id>.feedback.md`` atomically
    - drain() puts ``feedback_file`` in the DSH_WORKFLOW_NODE manifest
    - worker_node reads the manifest and prepends the feedback under
      ``## Feedback for this visit``

    By using the real path structure and a realistic manifest, it tests the
    handoff that was silently broken before dm-110: each side was correct in
    isolation, but the whole path never ran.
    """
    from refactory_dsh.run import NODE_ENV

    # Simulate what drain() writes: the feedback sidecar at the real path.
    prompts_dir = tmp_path / ".refactory" / "prompts"
    prompts_dir.mkdir(parents=True)
    fb_path = prompts_dir / "emit.feedback.md"
    # Atomic write as drain() does: temporary file then rename.
    fb_tmp = prompts_dir / "emit.feedback.tmp"
    fb_tmp.write_text("MARKER-dm110-rerun-correction: fix the output format")
    fb_tmp.rename(fb_path)

    # The prompt file drain() writes via runDeterministic().
    prompt_path = prompts_dir / "emit.md"
    prompt_path.write_text("Build the artifact per the design.")

    # The manifest that drain() puts in DSH_WORKFLOW_NODE, as requestFor() builds it.
    manifest = {
        "id": "emit",
        "type": "AgentNode",
        "command": "refactory-worker",
        "reads": [],
        "writes": ["emit.txt"],
        "prompt_file": str(prompt_path),
        "feedback_file": str(fb_path),
    }

    spec = {
        "schema_version": 1,
        "node_id": "emit",
        "prompt": "fallback",
        "reads": [],
        "writes": ["emit.txt"],
        "model_context": {"provider": "test", "model": "test"},
    }

    captured_prompt = []

    class FakeClient:
        def __init__(self, **kwargs):
            pass
        def for_node(self, node):
            return self
        def complete(self, prompt, workdir=None):
            captured_prompt.append(prompt)
            return "done"
        def context(self):
            return {"provider": "test", "model": "test"}
        def route(self):
            return {"worker_backend": "dsh"}

    class FakeDelivery:
        @staticmethod
        def begin(workdir, node, prompt, writes, *, reads=None, model_context=None):
            captured_prompt.clear()
            captured_prompt.append(prompt)
            return {"prompt": prompt, "node": node, "status": "pending"}
        @staticmethod
        def finish(workdir, node, *, final_response=None, capture_final=False):
            return {"node": node, "status": "delivered"}
        @staticmethod
        def _path(workdir, name):
            return Path(workdir) / name

    with mock.patch.dict(os.environ, {NODE_ENV: json.dumps(manifest)}):
        with mock.patch("refactory_dsh.model.DshModelClient", FakeClient):
            with mock.patch("refactory_dsh.agent_delivery.begin", FakeDelivery.begin):
                with mock.patch("refactory_dsh.agent_delivery.finish", FakeDelivery.finish):
                    with mock.patch("refactory_dsh.agent_delivery._path", FakeDelivery._path):
                        with mock.patch("refactory_dsh.run._knobs", return_value={}):
                            with mock.patch("refactory_dsh.run._read_facts", return_value={}):
                                with mock.patch("refactory_dsh.nn.executor.render", side_effect=lambda p, c: p):
                                    with mock.patch("refactory_dsh.nn.executor.RunContext"):
                                        from refactory_dsh import worker_node
                                        worker_node.execute(spec, tmp_path)

    assert len(captured_prompt) > 0
    final_prompt = captured_prompt[0]
    # The rerun text appears under the feedback heading.
    assert "## Feedback for this visit" in final_prompt
    assert "MARKER-dm110-rerun-correction" in final_prompt
    # The main prompt is also present.
    assert "Build the artifact per the design." in final_prompt
    # Feedback comes before the main prompt.
    fb_pos = final_prompt.index("Feedback for this visit")
    prompt_pos = final_prompt.index("Build the artifact per the design.")
    assert fb_pos < prompt_pos


def test_no_feedback_file_leaves_prompt_unchanged(tmp_path):
    """dm-110: Without a feedback_file, the prompt is the prompt file's content."""
    from refactory_dsh.run import NODE_ENV

    prompt_path = tmp_path / "prompt.md"
    prompt_path.write_text("Build the widget.")

    manifest = {
        "id": "emit",
        "type": "AgentNode",
        "command": "echo",
        "reads": [],
        "writes": [],
        "prompt_file": str(prompt_path),
        # No feedback_file
    }

    spec = {
        "schema_version": 1,
        "node_id": "emit",
        "prompt": "fallback prompt",
        "reads": [],
        "writes": [],
        "model_context": {"provider": "test", "model": "test"},
    }

    captured_prompt = []

    class FakeClient:
        def __init__(self, **kwargs):
            pass
        def for_node(self, node):
            return self
        def complete(self, prompt, workdir=None):
            captured_prompt.append(prompt)
            return "done"
        def context(self):
            return {"provider": "test", "model": "test"}
        def route(self):
            return {"worker_backend": "dsh"}

    class FakeDelivery:
        @staticmethod
        def begin(workdir, node, prompt, writes, *, reads=None, model_context=None):
            captured_prompt.clear()
            captured_prompt.append(prompt)
            return {"prompt": prompt, "node": node, "status": "pending"}
        @staticmethod
        def finish(workdir, node, *, final_response=None, capture_final=False):
            return {"node": node, "status": "delivered"}
        @staticmethod
        def _path(workdir, name):
            return Path(workdir) / name

    # Mock at the source modules (deferred imports inside execute() import from
    # these, not from worker_node module-level attributes).
    with mock.patch.dict(os.environ, {NODE_ENV: json.dumps(manifest)}):
        with mock.patch("refactory_dsh.model.DshModelClient", FakeClient):
            with mock.patch("refactory_dsh.agent_delivery.begin", FakeDelivery.begin):
                with mock.patch("refactory_dsh.agent_delivery.finish", FakeDelivery.finish):
                    with mock.patch("refactory_dsh.agent_delivery._path", FakeDelivery._path):
                        with mock.patch("refactory_dsh.run._knobs", return_value={}):
                            with mock.patch("refactory_dsh.run._read_facts", return_value={}):
                                with mock.patch("refactory_dsh.nn.executor.render", side_effect=lambda p, c: p):
                                    with mock.patch("refactory_dsh.nn.executor.RunContext"):
                                        from refactory_dsh import worker_node
                                        worker_node.execute(spec, tmp_path)

    assert len(captured_prompt) > 0
    final_prompt = captured_prompt[0]
    assert "Feedback for this visit" not in final_prompt
    assert final_prompt == "Build the widget."
