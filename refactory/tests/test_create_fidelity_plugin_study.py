"""Standalone plugin discovery studies its own empty, protected local context."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from benchmarks.create_fidelity import variant_cases
from refactory_dsh import create_fidelity_ops
from refactory_dsh.create_fidelity import CreateFidelityMode
from refactory_dsh.nn import Graph, execute


def _bootstrap(project, study, spec):
    original = CreateFidelityMode().graph()
    graph = Graph()
    graph.add(original.nodes["init_user_intent"])
    graph.add(original.nodes["discover"])
    graph.connect("init_user_intent", "discover")
    return execute(graph, project, knobs={
        "name": spec["name"], "variant": spec["variant"],
        "brief": (project / "BRIEF.md").read_text(),
        "requires_input": spec["requires_input"], "project_path": str(study),
    })


def test_plugin_real_init_and_discover_never_scan_framework(tmp_path, monkeypatch):
    framework = tmp_path / "framework"
    hidden = framework / "refactory/tests/hidden-checker.py"
    hidden.parent.mkdir(parents=True)
    hidden.write_text("must not become authoring study input")
    project = tmp_path / "plugin"
    spec, original, study = variant_cases.provision(
        project, framework, "native-plugin-reverse-v1")
    sources = create_fidelity_ops._sources
    scanned = []

    def observe(root):
        scanned.append(root)
        assert root == original, "plugin attempted to scan framework source"
        yield from sources(root)

    monkeypatch.setattr(create_fidelity_ops, "_sources", observe)
    result = _bootstrap(project, study, spec)
    assert result.exit_ok, result.error
    assert scanned == [original]
    profile = json.loads((project / ".factory/eval_profile.json").read_text())
    assert profile["project_path"] == str(original)
    assert profile["files"] == [] and profile["manifests"] == {}
    assert profile["eval_status"] == "not_exercised"
    assert not list(original.iterdir())
    assert not (project / ".factory/packages/reverse_plugin").exists()
    assert hidden.read_text() == "must not become authoring study input"
    assert "Source project for study: " + str(original) in (project / "BRIEF.md").read_text()
    assert spec["brief"] in (project / "BRIEF.md").read_text()


@pytest.mark.skipif(sys.platform != "darwin", reason="requires actual Seatbelt")
def test_plugin_real_bootstrap_with_framework_tests_kernel_denied(tmp_path):
    # Provision only the public case context outside the authoring sandbox.
    framework = tmp_path / "framework"
    hidden = framework / "refactory/tests"
    hidden.mkdir(parents=True)
    (hidden / "hidden-checker.py").write_text("operator-only")
    project = tmp_path / "plugin"
    spec, original, study = variant_cases.provision(
        project, framework, "native-plugin-reverse-v1")
    script = r'''
import json, sys
from pathlib import Path
from refactory_dsh.create_fidelity import CreateFidelityMode
from refactory_dsh.nn import Graph, execute
project, study, hidden = map(Path, sys.argv[1:4])
try:
    hidden.is_symlink()
except PermissionError:
    pass
else:
    raise AssertionError("hidden framework tests were not actually denied")
source=CreateFidelityMode().graph()
g=Graph();g.add(source.nodes["init_user_intent"]);g.add(source.nodes["discover"])
g.connect("init_user_intent","discover")
r=execute(g,project,knobs={"name":"reverse_plugin","variant":"plugin",
    "brief":(project/"BRIEF.md").read_text(),"requires_input":"text_path",
    "project_path":str(study)})
assert r.exit_ok,r.error
profile=json.loads((project/".factory/eval_profile.json").read_text())
assert profile["project_path"]==str(study)
assert profile["files"]==[] and profile["manifests"]=={}
assert not list(study.iterdir())
print("PLUGIN_LOCAL_DISCOVERY_OK")
'''
    # Preserve the existing protected study directory and hidden-source policy.
    policy = tmp_path / "study.sb"
    policy.write_text("(version 1) (allow default)\n"
                      f"(deny file-read* (subpath {json.dumps(str(hidden))}))\n"
                      f"(deny file-write* (subpath {json.dumps(str(original))}))\n")
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1",
           "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
    cp = subprocess.run(["/usr/bin/sandbox-exec", "-f", str(policy),
                         sys.executable, "-B", "-c", script,
                         str(project), str(study), str(hidden)],
                        env=env, cwd=project, capture_output=True, text=True, timeout=30)
    if "sandbox_apply: Operation not permitted" in cp.stderr:
        pytest.skip("caller kernel sandbox cannot apply nested Seatbelt")
    assert cp.returncode == 0, cp.stderr + cp.stdout
    assert cp.stdout.strip() == "PLUGIN_LOCAL_DISCOVERY_OK"
    assert not list(original.iterdir())
