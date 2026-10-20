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


def test_end_to_end_feedback_reaches_worker_with_scripted_model(tmp_path):
    """dm-110 end-to-end: feedback sidecar written by drain() (TypeScript)
    reaches the dispatched agent's prompt via worker_node.execute() (Python),
    using a scripted model that records the prompts it receives.

    This test crosses the real boundary:
    - drain() writes ``.refactory/prompts/<id>.feedback.md`` atomically
    - drain() puts ``feedback_file`` in the DSH_WORKFLOW_NODE manifest
    - worker_node.execute() reads the manifest, reads the sidecar, prepends
      the feedback under ``## Feedback for this visit``, and calls the model
    - The scripted model records the prompt, proving the text arrived

    After a rerun with --text, the marker text appears under the heading.
    A subsequent visit without feedback does not get the old text.
    """
    from refactory_dsh.run import NODE_ENV

    # ── Visit 1: rerun with feedback ──────────────────────────────────────
    prompts_dir = tmp_path / ".refactory" / "prompts"
    prompts_dir.mkdir(parents=True)
    fb_path = prompts_dir / "emit.feedback.md"
    # Atomic write as drain() does: temporary file then rename.
    fb_tmp = prompts_dir / "emit.feedback.tmp"
    fb_tmp.write_text("MARKER-dm110-rerun-correction: fix the output format")
    fb_tmp.rename(fb_path)

    prompt_path = prompts_dir / "emit.md"
    prompt_path.write_text("Build the artifact per the design.")

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

    # Scripted model that records every prompt it receives.
    recorded_prompts: list[str] = []

    class ScriptedModelClient:
        def __init__(self, **kwargs):
            pass
        def for_node(self, node):
            return self
        def complete(self, prompt, workdir=None):
            recorded_prompts.append(prompt)
            return "done"
        def context(self):
            return {"provider": "test", "model": "test"}
        def route(self):
            return {"worker_backend": "dsh"}

    class FakeDelivery:
        @staticmethod
        def begin(workdir, node, prompt, writes, *, reads=None, model_context=None):
            recorded_prompts.append(prompt)
            return {"prompt": prompt, "node": node, "status": "pending"}
        @staticmethod
        def finish(workdir, node, *, final_response=None, capture_final=False):
            return {"node": node, "status": "delivered"}
        @staticmethod
        def _path(workdir, name):
            return Path(workdir) / name

    with mock.patch.dict(os.environ, {NODE_ENV: json.dumps(manifest)}):
        with mock.patch("refactory_dsh.model.DshModelClient", ScriptedModelClient):
            with mock.patch("refactory_dsh.agent_delivery.begin", FakeDelivery.begin):
                with mock.patch("refactory_dsh.agent_delivery.finish", FakeDelivery.finish):
                    with mock.patch("refactory_dsh.agent_delivery._path", FakeDelivery._path):
                        with mock.patch("refactory_dsh.run._knobs", return_value={}):
                            with mock.patch("refactory_dsh.run._read_facts", return_value={}):
                                with mock.patch("refactory_dsh.nn.executor.render", side_effect=lambda p, c: p):
                                    with mock.patch("refactory_dsh.nn.executor.RunContext"):
                                        from refactory_dsh import worker_node
                                        worker_node.execute(spec, tmp_path)

    assert len(recorded_prompts) > 0
    final_prompt = recorded_prompts[-1]
    # The rerun text appears under the feedback heading.
    assert "## Feedback for this visit" in final_prompt
    assert "MARKER-dm110-rerun-correction" in final_prompt
    # The main prompt is also present.
    assert "Build the artifact per the design." in final_prompt
    # Feedback comes before the main prompt.
    fb_pos = final_prompt.index("Feedback for this visit")
    prompt_pos = final_prompt.index("Build the artifact per the design.")
    assert fb_pos < prompt_pos

    # ── Visit 2: next visit without feedback ──────────────────────────────
    # drain() does not write a sidecar when there is no feedback, and the
    # old sidecar should have been removed. Simulate that by removing the
    # sidecar file and dropping feedback_file from the manifest.
    if fb_path.exists():
        fb_path.unlink()
    manifest_no_fb = {
        "id": "emit",
        "type": "AgentNode",
        "command": "refactory-worker",
        "reads": [],
        "writes": ["emit.txt"],
        "prompt_file": str(prompt_path),
        # No feedback_file — drain() omits it when there is no feedback.
    }

    recorded_prompts.clear()
    with mock.patch.dict(os.environ, {NODE_ENV: json.dumps(manifest_no_fb)}):
        with mock.patch("refactory_dsh.model.DshModelClient", ScriptedModelClient):
            with mock.patch("refactory_dsh.agent_delivery.begin", FakeDelivery.begin):
                with mock.patch("refactory_dsh.agent_delivery.finish", FakeDelivery.finish):
                    with mock.patch("refactory_dsh.agent_delivery._path", FakeDelivery._path):
                        with mock.patch("refactory_dsh.run._knobs", return_value={}):
                            with mock.patch("refactory_dsh.run._read_facts", return_value={}):
                                with mock.patch("refactory_dsh.nn.executor.render", side_effect=lambda p, c: p):
                                    with mock.patch("refactory_dsh.nn.executor.RunContext"):
                                        worker_node.execute(spec, tmp_path)

    assert len(recorded_prompts) > 0
    second_prompt = recorded_prompts[-1]
    # The old feedback must NOT appear.
    assert "Feedback for this visit" not in second_prompt
    assert "MARKER-dm110" not in second_prompt
    # The main prompt is present.
    assert "Build the artifact per the design." in second_prompt


def test_gate_reloop_reason_reaches_reloop_target_prompt(tmp_path):
    """dm-110: A gate reloop's reason reaches the reloop target's prompt
    the same way as rerun text: under '## Feedback for this visit'.

    The spine writes the gate reason as the feedback sidecar; the worker
    reads it identically to a rerun text sidecar.
    """
    from refactory_dsh.run import NODE_ENV

    prompts_dir = tmp_path / ".refactory" / "prompts"
    prompts_dir.mkdir(parents=True)
    fb_path = prompts_dir / "builder.feedback.md"
    # The gate's reason, written by the spine's drain() for the reloop target.
    fb_path.write_text("GATE-REASON: the output did not pass review criteria")

    prompt_path = prompts_dir / "builder.md"
    prompt_path.write_text("Implement the feature.")

    manifest = {
        "id": "builder",
        "type": "AgentNode",
        "command": "refactory-worker",
        "reads": [],
        "writes": ["output.txt"],
        "prompt_file": str(prompt_path),
        "feedback_file": str(fb_path),
    }

    spec = {
        "schema_version": 1,
        "node_id": "builder",
        "prompt": "fallback",
        "reads": [],
        "writes": ["output.txt"],
        "model_context": {"provider": "test", "model": "test"},
    }

    recorded_prompts: list[str] = []

    class ScriptedModelClient:
        def __init__(self, **kwargs):
            pass
        def for_node(self, node):
            return self
        def complete(self, prompt, workdir=None):
            recorded_prompts.append(prompt)
            return "done"
        def context(self):
            return {"provider": "test", "model": "test"}
        def route(self):
            return {"worker_backend": "dsh"}

    class FakeDelivery:
        @staticmethod
        def begin(workdir, node, prompt, writes, *, reads=None, model_context=None):
            recorded_prompts.append(prompt)
            return {"prompt": prompt, "node": node, "status": "pending"}
        @staticmethod
        def finish(workdir, node, *, final_response=None, capture_final=False):
            return {"node": node, "status": "delivered"}
        @staticmethod
        def _path(workdir, name):
            return Path(workdir) / name

    with mock.patch.dict(os.environ, {NODE_ENV: json.dumps(manifest)}):
        with mock.patch("refactory_dsh.model.DshModelClient", ScriptedModelClient):
            with mock.patch("refactory_dsh.agent_delivery.begin", FakeDelivery.begin):
                with mock.patch("refactory_dsh.agent_delivery.finish", FakeDelivery.finish):
                    with mock.patch("refactory_dsh.agent_delivery._path", FakeDelivery._path):
                        with mock.patch("refactory_dsh.run._knobs", return_value={}):
                            with mock.patch("refactory_dsh.run._read_facts", return_value={}):
                                with mock.patch("refactory_dsh.nn.executor.render", side_effect=lambda p, c: p):
                                    with mock.patch("refactory_dsh.nn.executor.RunContext"):
                                        from refactory_dsh import worker_node
                                        worker_node.execute(spec, tmp_path)

    assert len(recorded_prompts) > 0
    final_prompt = recorded_prompts[-1]
    assert "## Feedback for this visit" in final_prompt
    assert "GATE-REASON: the output did not pass review criteria" in final_prompt
    assert "Implement the feature." in final_prompt
    fb_pos = final_prompt.index("Feedback for this visit")
    prompt_pos = final_prompt.index("Implement the feature.")
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
