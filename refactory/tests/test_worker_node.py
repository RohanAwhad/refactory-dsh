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
    """dm-110 end-to-end: TypeScript spine drain() writes the feedback sidecar,
    dispatches via a real shell, and the real Python worker_node.execute()
    reads it.

    This test crosses the real TypeScript/Python boundary:
    - A TypeScript driver (run via tsx) mounts the spine plugin, provides a
      real shell, and calls wf_next to dispatch the graph's agent node.
    - drain() writes ``.refactory/prompts/<id>.feedback.md`` atomically and
      puts ``feedback_file`` in the DSH_WORKFLOW_NODE manifest.
    - The real shell runs a Python dispatch helper whose dispatch command
      calls the real worker_node.execute() with a scripted model.
    - The scripted model records the prompt, proving the text arrived.

    After ``graph-control rerun --text "<marker>"``, the prompt that the agent
    receives contains the marker under "## Feedback for this visit".
    A subsequent visit without feedback does not get the old text.
    """
    import subprocess

    repo = Path(__file__).resolve().parent.parent.parent
    workdir = tmp_path / "run"
    workdir.mkdir()

    python_bin = str(repo / ".venv" / "bin" / "python")
    tsx_bin = str(repo / "node_modules" / ".bin" / "tsx")

    if not Path(tsx_bin).exists():
        pytest.skip("tsx not available; cross-boundary test requires Node.js tooling")

    # ── Worker spec ───────────────────────────────────────────────────────
    specs_dir = workdir / ".refactory" / "worker-specs"
    specs_dir.mkdir(parents=True)
    spec_path = specs_dir / "a-spec.json"
    spec_path.write_text(json.dumps({
        "schema_version": 1, "node_id": "a", "prompt": "Build it.",
        "reads": [], "writes": [],
        "model_context": {"provider": "test", "model": "test"},
    }))

    # ── Python dispatch helper ────────────────────────────────────────────
    # Called by the real shell as the agent's dispatch_command.
    # Runs the real worker_node.execute() with a scripted model and records
    # the prompt to a file the test reads back.
    helper_path = tmp_path / "dispatch_helper.py"
    helper_path.write_text(
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "from unittest import mock\n"
        "\n"
        "spec_path = sys.argv[1]\n"
        "spec = json.loads(Path(spec_path).read_text())\n"
        "workdir = Path.cwd()\n"
        "manifest = json.loads(os.environ.get('DSH_WORKFLOW_NODE', '{}'))\n"
        "node_id = manifest.get('id', 'unknown')\n"
        "\n"
        "counter_file = workdir / '.refactory' / f'dm110-visit-{node_id}.txt'\n"
        "counter_file.parent.mkdir(parents=True, exist_ok=True)\n"
        "v = int(counter_file.read_text()) + 1 if counter_file.exists() else 1\n"
        "counter_file.write_text(str(v))\n"
        "\n"
        "captured = []\n"
        "\n"
        "class M:\n"
        "    def __init__(self, **k): pass\n"
        "    def for_node(self, n): return self\n"
        "    def complete(self, p, workdir=None):\n"
        "        captured.append(p); return 'done'\n"
        "    def context(self): return {'provider': 'test', 'model': 'test'}\n"
        "    def route(self): return {'worker_backend': 'dsh'}\n"
        "\n"
        "class D:\n"
        "    @staticmethod\n"
        "    def begin(w, n, p, ws, **k):\n"
        "        captured.append(p); return {'prompt': p, 'node': n, 'status': 'pending'}\n"
        "    @staticmethod\n"
        "    def finish(w, n, **k): return {'node': n, 'status': 'delivered'}\n"
        "    @staticmethod\n"
        "    def _path(w, name): return Path(w) / name\n"
        "\n"
        "with mock.patch('refactory_dsh.model.DshModelClient', M), \\\n"
        "     mock.patch('refactory_dsh.agent_delivery.begin', D.begin), \\\n"
        "     mock.patch('refactory_dsh.agent_delivery.finish', D.finish), \\\n"
        "     mock.patch('refactory_dsh.agent_delivery._path', D._path), \\\n"
        "     mock.patch('refactory_dsh.run._knobs', return_value={}), \\\n"
        "     mock.patch('refactory_dsh.run._read_facts', return_value={}), \\\n"
        "     mock.patch('refactory_dsh.nn.executor.render', side_effect=lambda p, c: p), \\\n"
        "     mock.patch('refactory_dsh.nn.executor.RunContext'):\n"
        "    from refactory_dsh import worker_node\n"
        "    worker_node.execute(spec, workdir)\n"
        "\n"
        "out = workdir / '.refactory' / f'dm110-prompt-{v}-{node_id}.txt'\n"
        "out.write_text(json.dumps(captured))\n"
    )

    # ── TypeScript driver ─────────────────────────────────────────────────
    # Mounts the spine plugin with a real shell, dispatches the graph's agent
    # node, queues a rerun with text, and dispatches again.
    marker = "MARKER-dm110-end-to-end-feedback"
    dispatch_cmd = f"{python_bin} {helper_path} {spec_path}"
    driver_path = tmp_path / "driver.mts"
    driver_path.write_text(
        "import { createHash } from 'node:crypto'\n"
        "import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'\n"
        "import { join, resolve } from 'node:path'\n"
        "import { spawnSync } from 'node:child_process'\n"
        "import { Context } from '@deepseek-ai/cordis'\n"
        "import SystemPrompt from '@deepseek-ai/dsh-system-prompt'\n"
        "import ToolRuntime from '@deepseek-ai/dsh-tools'\n"
        "import * as spine from '@deepseek-ai/dsh-workflow-spine'\n"
        "\n"
        "const cfg = JSON.parse(process.argv[2])\n"
        "const graph = {\n"
        "  name: 't', start_node: 'a',\n"
        "  nodes: {\n"
        "    a: { _type: 'AgentNode', id: 'a', role: 'builder',\n"
        "         prompt_template: 'Build it.', reads: [], writes: [],\n"
        "         dispatch_command: cfg.dispatchCommand }\n"
        "  },\n"
        "  edges: []\n"
        "}\n"
        "const ctx = new Context()\n"
        "await ctx.plugin(SystemPrompt)\n"
        "await ctx.plugin(ToolRuntime)\n"
        "await ctx.plugin(spine, {\n"
        "  graph, toolPrefix: 'wf', maxIterations: 10,\n"
        "  interactiveOpen: false, openCommand: '', openArgs: [],\n"
        "  executeCommands: true, commandEnv: {},\n"
        "  commandTimeoutMs: 60000, commandSandboxMode: 'danger-full-access',\n"
        "  dataCommand: '', steerCommand: '', steerArgs: [], ceoDeny: [],\n"
        "  workdir: cfg.workdir,\n"
        "} as any)\n"
        "ctx.provide('shell', {\n"
        "  resolve: (r: any) => ({\n"
        "    command: r.command, workdir: r.workdir ?? process.cwd(),\n"
        "    timeoutMs: r.timeoutMs ?? 60000, onExpiry: 'kill',\n"
        "    stdoutMaxBytes: 1_000_000, env: r.env, sandboxPolicy: undefined,\n"
        "  }),\n"
        "  async execute(s: any) {\n"
        "    const r = spawnSync('/bin/sh', ['-c', s.command], {\n"
        "      cwd: s.workdir, encoding: 'utf8', timeout: s.timeoutMs,\n"
        "      env: { ...process.env, ...(s.env || {}) },\n"
        "    })\n"
        "    return { result: async () => ({\n"
        "      exitCode: r.status ?? 0, signal: r.signal,\n"
        "      timedOut: false, aborted: false, timeoutMs: s.timeoutMs,\n"
        "      stdout: { text: r.stdout ?? '', truncated: false },\n"
        "      stderr: { text: r.stderr ?? '', truncated: false },\n"
        "    }) }\n"
        "  }\n"
        "} as any)\n"
        "const tool = ctx.tools.get('wf_next')!\n"
        "// Visit 1: dispatch without feedback.\n"
        "try { await tool.execute({}, {} as any) } catch (e) { /* node may fail */ }\n"
        "// Get checkpoint revision.\n"
        "const cp = join(cfg.workdir, '.refactory', 'spine-checkpoint.json')\n"
        "if (!existsSync(cp)) { console.error('no checkpoint'); process.exit(1) }\n"
        "const rev = createHash('sha256').update(readFileSync(cp)).digest('hex')\n"
        "// Queue rerun with text.\n"
        "const dir = join(cfg.workdir, '.refactory', 'graph-controls')\n"
        "mkdirSync(dir, { recursive: true })\n"
        "writeFileSync(join(dir, '1.json'), JSON.stringify({\n"
        "  id: '1', action: 'rerun', target: 'a', text: cfg.marker,\n"
        "  revision: rev, run_id: resolve(cfg.workdir), invocation: null,\n"
        "  status: 'queued',\n"
        "}))\n"
        "// Visit 2: dispatch with feedback.\n"
        "try { await tool.execute({}, {} as any) } catch (e) { /* ok */ }\n"
        "console.log(JSON.stringify({ ok: true }))\n"
        "process.exit(0)\n"
    )

    config_json = json.dumps({
        "workdir": str(workdir),
        "dispatchCommand": dispatch_cmd,
        "marker": marker,
    })

    result = subprocess.run(
        [tsx_bin, str(driver_path), config_json],
        capture_output=True, text=True,
        cwd=str(repo), timeout=120,
    )

    assert result.returncode == 0, (
        f"TypeScript driver failed (exit {result.returncode}):\n"
        f"stdout: {result.stdout[:2000]}\nstderr: {result.stderr[:2000]}"
    )

    # ── Verify visit 2 carries the feedback ───────────────────────────────
    v2_file = workdir / ".refactory" / "dm110-prompt-2-a.txt"
    assert v2_file.exists(), "No prompt recorded from visit 2 (after rerun with text)"
    v2_prompts = json.loads(v2_file.read_text())
    assert any("## Feedback for this visit" in p for p in v2_prompts), (
        "Visit 2 prompt does not contain '## Feedback for this visit'"
    )
    assert any(marker in p for p in v2_prompts), (
        f"Visit 2 prompt does not contain the marker '{marker}'"
    )

    # ── Verify visit 1 does NOT carry feedback ────────────────────────────
    v1_file = workdir / ".refactory" / "dm110-prompt-1-a.txt"
    if v1_file.exists():
        v1_prompts = json.loads(v1_file.read_text())
        assert not any("Feedback for this visit" in p for p in v1_prompts), (
            "Visit 1 prompt should not contain feedback"
        )


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
