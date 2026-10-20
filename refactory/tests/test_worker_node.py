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


def test_end_to_end_drain_feedback_reaches_real_worker(tmp_path):
    """dm-110 end-to-end: verify that the file-level contract between
    drain() and worker_node.execute() holds across three visits.

    drain() writes ``.refactory/prompts/<id>.feedback.md`` and puts
    ``feedback_file`` in the DSH_WORKFLOW_NODE manifest.  worker_node.execute()
    reads that file and prepends its content under "## Feedback for this visit".

    The complementary vitest test (correction.spec.ts: 'drain dispatches to
    real worker_node.execute and feedback reaches prompt (dm-110 e2e)') crosses
    the real TS→Python boundary via a real shell; this test exercises the
    Python consumer side with the exact file layout that drain() produces.

    - Visit 1: no feedback sidecar → prompt has no "Feedback" heading.
    - Visit 2: feedback sidecar written → prompt carries marker under heading.
    - Visit 3: feedback sidecar removed → prompt has no stale feedback.
    """
    from refactory_dsh.run import NODE_ENV

    workdir = tmp_path / "run"
    workdir.mkdir()
    prompts_dir = workdir / ".refactory" / "prompts"
    prompts_dir.mkdir(parents=True)

    # Write the prompt file exactly as drain() does.
    prompt_file = prompts_dir / "a.md"
    prompt_file.write_text("Build it.")

    spec = {
        "schema_version": 1,
        "node_id": "a",
        "prompt": "fallback",
        "reads": [],
        "writes": [],
        "model_context": {"provider": "test", "model": "test"},
    }

    recorded_prompts: list[str] = []

    class FakeClient:
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

    marker = "MARKER-dm110-end-to-end-feedback"
    feedback_file = prompts_dir / "a.feedback.md"

    def run_worker(*, with_feedback: bool):
        """Simulate one visit: write or remove the sidecar, build the manifest
        exactly as runDeterministic() does, and call worker_node.execute()."""
        if with_feedback:
            feedback_file.write_text(marker)
        elif feedback_file.exists():
            feedback_file.unlink()

        manifest = {
            "id": "a",
            "type": "AgentNode",
            "command": "echo dispatched",
            "reads": [],
            "writes": [],
            "prompt_file": str(prompt_file),
        }
        if with_feedback:
            manifest["feedback_file"] = str(feedback_file)

        recorded_prompts.clear()
        with mock.patch.dict(os.environ, {NODE_ENV: json.dumps(manifest)}):
            with mock.patch("refactory_dsh.model.DshModelClient", FakeClient), \
                 mock.patch("refactory_dsh.agent_delivery.begin", FakeDelivery.begin), \
                 mock.patch("refactory_dsh.agent_delivery.finish", FakeDelivery.finish), \
                 mock.patch("refactory_dsh.agent_delivery._path", FakeDelivery._path), \
                 mock.patch("refactory_dsh.run._knobs", return_value={}), \
                 mock.patch("refactory_dsh.run._read_facts", return_value={}), \
                 mock.patch("refactory_dsh.nn.executor.render", side_effect=lambda p, c: p), \
                 mock.patch("refactory_dsh.nn.executor.RunContext"):
                from refactory_dsh import worker_node
                worker_node.execute(spec, workdir)
        return recorded_prompts[-1] if recorded_prompts else ""

    # Visit 1: no feedback sidecar — prompt is the prompt file content only.
    v1 = run_worker(with_feedback=False)
    assert "Feedback for this visit" not in v1
    assert "Build it." in v1

    # Visit 2: feedback sidecar written (simulating drain() after rerun --text).
    v2 = run_worker(with_feedback=True)
    assert "## Feedback for this visit" in v2
    assert marker in v2
    assert "Build it." in v2
    # Feedback comes before the main prompt.
    assert v2.index("Feedback for this visit") < v2.index("Build it.")

    # Visit 3: feedback cleared (simulating completeDue removing the sidecar).
    v3 = run_worker(with_feedback=False)
    assert "Feedback for this visit" not in v3
    assert marker not in v3


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
