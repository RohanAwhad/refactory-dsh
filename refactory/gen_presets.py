"""Generate the 3 meta-mode agent presets from the emitted graph.json.

Rewrites refactory's ``factory ...`` / ``{project_path}`` / ``{generation}`` commands
into ``python -m refactory_dsh.ops.*`` calls, then writes one DSH agent preset per
mode (the science preset's standard-tool skeleton + a workflow-spine row that inlines
the rewritten graph). The presets land in ``~/.dsh/.agent-presets/<mode>/``.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from refactory_dsh.paths import REPO_ROOT, python as _python, refactory_dsh_pkg, venv_bin, workdir_base

REPO = REPO_ROOT
GRAPHS = refactory_dsh_pkg().parent / "graphs"
SCIENCE_PRESET = Path.home() / ".dsh" / ".agent-presets" / "science" / "agent.cordis.yml"
PRESET_ROOT = Path.home() / ".dsh" / ".agent-presets"
VENV = str(venv_bin())
PY = str(_python())
PKG = str(refactory_dsh_pkg().parent)
WORKDIR_BASE = str(workdir_base())


def _op(module: str, op: str) -> str:
    return f"{PY} -m refactory_dsh.ops.{module} {op}"


def rewrite(command: str | None) -> str | None:
    if not command:
        return command
    cmd = command.strip()
    if "outer-loop calibrate" in cmd:
        return _op("outer_loop", "calibrate")
    if "outer-loop run" in cmd:
        return _op("outer_loop", "run")
    if "outer-loop evaluate" in cmd:
        return _op("outer_loop", "evaluate")
    if "outer-loop reflect" in cmd:
        return _op("outer_loop", "reflect")
    if "outer-loop evolve" in cmd:
        return _op("outer_loop", "evolve")
    if "outer-loop status" in cmd and "--check-converge" in cmd:
        return _op("outer_loop", "check_converge")
    if "outer-loop status" in cmd:
        return _op("outer_loop", "status")
    if "factory discover" in cmd:
        return _op("scaffold", "discover")
    if "factory init" in cmd:
        return _op("scaffold", "init")
    if "factory study" in cmd:
        return _op("scaffold", "study")
    if "factory graph update" in cmd:
        return _op("scaffold", "graph_update")
    if "factory precheck" in cmd:
        return _op("scaffold", "precheck")
    if "spec-generate" in cmd:
        return _op("scaffold", "spec_generate")
    if "intent_init" in cmd:
        return _op("scaffold", "intent_init")
    if "qa_synthesis" in cmd:
        return _op("scaffold", "qa_synthesis")
    if ".factory/config.json" in cmd:
        return _op("scaffold", "check_factory_exists")
    if "factory.md" in cmd:
        return _op("scaffold", "check_factory_md")
    if "cat " in cmd:
        return _op("scaffold", "concat_study")
    return cmd


def load_graph(name: str) -> dict:
    return json.loads((GRAPHS / f"{name}.graph.json").read_text())


def rewrite_graph(graph: dict) -> dict:
    for node in graph["nodes"].values():
        if node.get("command"):
            node["command"] = rewrite(node["command"])
        if node.get("evaluator_command"):
            node["evaluator_command"] = rewrite(node["evaluator_command"])
    return graph


def standard_skeleton() -> str:
    """The science preset up to (not including) the workflow-spine row."""
    text = SCIENCE_PRESET.read_text()
    marker = "- id: workflow-spine\n"
    idx = text.index(marker)
    return text[:idx]


def write_preset(name: str, graph: dict) -> None:
    d = PRESET_ROOT / name
    d.mkdir(parents=True, exist_ok=True)
    workdir = f"{WORKDIR_BASE}/{name}"
    Path(workdir).mkdir(parents=True, exist_ok=True)
    (d / "preset.yml").write_text(f"name: {name}\ndescription: refactory-dsh {name} meta-mode (interactive).\n")

    graph_yaml = yaml.safe_dump(graph, sort_keys=False, default_flow_style=False, width=1000)
    graph_yaml = "\n".join("      " + line for line in graph_yaml.splitlines())

    spine = f"""# ── workflow spine (refactory-dsh meta-mode) ──────────────────────────────
- id: workflow-spine
  name: '@deepseek-ai/dsh-workflow-spine'
  config:
    toolPrefix: wf
    maxIterations: 500
    executeCommands: true
    workdir: {workdir}
    commandEnv:
      PATH: /bin:/usr/bin:/usr/local/bin:/usr/sbin:/sbin:{VENV}
      PYTHONPATH: {PKG}
      REFACTORY_WORKDIR: {workdir}
    ceoDeny:
      - bash
      - pwsh
      - write
      - edit
      - str_replace_editor
      - web_search
      - web_fetch
      - skill
      - ralph
      - workflow
      - read_image
    graph:
{graph_yaml}
"""
    (d / "agent.cordis.yml").write_text(standard_skeleton() + spine)
    (GRAPHS / f"{name}.graph.json").write_text(json.dumps(graph, indent=2))
    print(f"wrote preset {name} ({len(graph['nodes'])} nodes) + rewritten graph.json")


PRESET_IDS = {"create_v2": "create-v2", "outer_loop": "outer-loop", "design_v2": "design-v2"}


def main() -> None:
    for name, preset_id in PRESET_IDS.items():
        graph = rewrite_graph(load_graph(name))
        write_preset(preset_id, graph)


if __name__ == "__main__":
    main()
