"""Real native revision and plugin lifetime checks; no model is invoked."""
from __future__ import annotations

import json
import os
import py_compile
import subprocess
import sys
from pathlib import Path

import pytest

from refactory_dsh import create_fidelity_ops as ops, registry
from refactory_dsh.create_fidelity import CreateFidelityMode
from refactory_dsh.create_fidelity_variants import BASELINE, inventory, plugin_check, update_check
from refactory_dsh.nn import Graph, execute, validate_state


def package(project: Path, name="variant_probe"):
    root = registry.workspace_root(project) / name
    root.mkdir(parents=True)
    (root / "__init__.py").write_text(f"from refactory_dsh import nn\nfrom .mode import Probe, Other\nPACKAGE=nn.Package(name={name!r},description='Native variant probe',modes=(Probe,Other),ops=('refactory_packages.{name}.ops',))\n")
    (root / "mode.py").write_text(f"""from refactory_dsh import nn
class Probe(nn.Mode):
    name='run'
    description='Write the runtime input'
    unscored_reason='Artifact producer'
    @classmethod
    def declare(cls): return {{'value':nn.Knob('original','str')}}
    def build(self):
        g=nn.Graph()
        g.add(nn.Node('write','op',call='refactory_packages.{name}.ops:write',knobs=('value',),writes=('value.json',)))
        return g
    def invariants(self): return (nn.artifact_produced('value.json',by='write'),)
class Other(nn.Mode):
    name='other'
    description='Unrelated mode remains registered'
    unscored_reason='Artifact producer'
    def build(self):
        g=nn.Graph()
        g.add(nn.Node('other','op',call='refactory_packages.{name}.ops:other',writes=('other.json',)))
        return g
    def invariants(self): return (nn.artifact_produced('other.json',by='other'),)
""")
    (root / "ops.py").write_text("def write(ctx):\n    ctx.write_json('value.json', {'value':ctx.knob('value'),'revision':1})\ndef other(ctx):\n    ctx.write_json('other.json', {'unrelated':True})\n")
    (root / "README.md").write_text("# Native variant probe\n\nUse registry add/import to copy this native Package into an isolated consumer; list, show, run and remove with the actual native CLI.\n")
    (root / "SKILL.md").write_text("Original generated skill retained in exact revision snapshot.\n")
    (root / ".origin.json").write_text('{"trust":"generated","origin":"controlled test"}\n')
    (root / "data.bin").write_bytes(b"\x00\xff original bytes\n")
    return root


def init_graph():
    g = Graph()
    g.add(CreateFidelityMode().graph().nodes["init_user_intent"])
    return g


def init_revision(source, run, **extra):
    return execute(init_graph(), run, knobs={"brief":"Revise this existing native mode while preserving unrelated behavior", "variant":"update-existing", "revision_of":"variant_probe/run", "project_path":str(source), **extra})


def test_real_revision_snapshot_execution_check_and_repeat_init(tmp_path):
    source, run = tmp_path / "source", tmp_path / "authoring"
    root = package(source); original = inventory(root)
    result = init_revision(source, run)
    assert result.exit_ok, result.error
    assert result.facts["name"] == "variant_probe" and result.facts["revision_of"] == "variant_probe/run"
    baseline = json.loads((run / BASELINE).read_text())
    assert baseline["files"] == original
    assert set(baseline["modes"]) == {"variant_probe/run", "variant_probe/other"}
    assert "original bytes" in result.facts["native_api"]
    assert "COMPLETE baseline" in result.facts["native_api"]
    target = registry.workspace_root(run) / "variant_probe"
    assert inventory(target) == original
    operation=target/"ops.py"
    py_compile.compile(str(operation),doraise=True)
    timestamp=operation.stat()
    operation.write_text(operation.read_text().replace("'revision':1", "'revision':2"))
    os.utime(operation,ns=(timestamp.st_atime_ns,timestamp.st_mtime_ns))
    assert operation.stat().st_size==timestamp.st_size  # deterministic stale-pyc window
    repeated = init_revision(source, run)
    assert repeated.exit_ok, repeated.error
    assert "'revision':2" in (target / "ops.py").read_text()  # resume never recopies over a revision
    assert inventory(root) == original
    check = update_check(run, "variant_probe")
    assert check["ok"] and check["source_unchanged"], check
    scoped=ops.validate_native_package(run,"variant_probe",requires_input=("value",))
    assert scoped["ok"] and scoped["requires_input_mode"]=="variant_probe/run",scoped
    before = ops.run_native_mode(source, "variant_probe/run", tmp_path / "before", {"value":"first"})
    after = ops.run_native_mode(run, "variant_probe/run", tmp_path / "after", {"value":"second"})
    other = ops.run_native_mode(run, "variant_probe/other", tmp_path / "other", {})
    assert before["exit_ok"] and after["exit_ok"] and other["exit_ok"]
    assert json.loads((tmp_path / "before/value.json").read_text()) == {"value":"first","revision":1}
    assert json.loads((tmp_path / "after/value.json").read_text()) == {"value":"second","revision":2}
    assert json.loads((tmp_path / "other/other.json").read_text()) == {"unrelated":True}
    assert inventory(root) == original


def test_revision_identity_non_target_source_and_baseline_fail_closed(tmp_path):
    source, run = tmp_path / "source", tmp_path / "run"
    root=package(source)
    assert init_revision(source, run).exit_ok
    target=registry.workspace_root(run)/"variant_probe"
    path=target/"mode.py"; old=path.read_text(); path.write_text(old.replace("name='other'", "name='lost'"))
    assert any("unrelated mode" in p for p in update_check(run,"variant_probe")["problems"])
    path.write_text(old)
    (root/"data.bin").write_bytes(b"source drift")
    assert not update_check(run,"variant_probe")["ok"]
    assert not init_revision(source, run).exit_ok
    data=(run/BASELINE).read_text(); (run/BASELINE).write_text(data+'\n')
    assert any("baseline does not match" in p for p in update_check(run,"variant_probe")["problems"])


@pytest.mark.parametrize("knobs,message", [
    ({"revision_of":""},"requires a qualified"),
    ({"revision_of":"variant_probe/missing"},"target mode missing"),
    ({"name":"different_name"},"identity is preserved"),
    ({"revision_of":"counting_pack/count"},"shipped package"),
])
def test_invalid_revisions_stop_before_model_and_preserve_source(tmp_path, knobs, message):
    source=tmp_path/"source";root=package(source); original=inventory(root)
    result=init_revision(source,tmp_path/"run",**knobs)
    assert not result.exit_ok and message in result.error
    assert inventory(root)==original
    assert not (tmp_path/"run/.factory/create-request.json").exists()


def test_plugin_real_install_discovery_validation_run_remove_and_source_stability(tmp_path):
    project=tmp_path/"plugin-author";project.mkdir()
    init=execute(init_graph(),project,knobs={"name":"variant_probe","brief":"A standalone native plugin","variant":"plugin"})
    assert init.exit_ok, init.error
    root=package(project); original=inventory(root)
    check=plugin_check(project,"variant_probe")
    assert check["ok"] and check["removed"] and len(check["checks"])==3, check
    assert check["modes"]==["variant_probe/run","variant_probe/other"]
    assert check["raw_evidence"]["returncode"]==0 and "native registry.remove" in check["raw_evidence"]["stdout"]
    assert "not exercised" in check["behavioral_execution"]
    assert inventory(root)==original
    # Behavioral proof is a separate, actual native subprocess lifetime.
    consumer=tmp_path/"consumer";consumer.mkdir()
    add=subprocess.run([sys.executable,"-m","refactory_dsh.registry","add",str(root),"--project",str(consumer)],capture_output=True,text=True)
    assert add.returncode==0,add.stderr
    result=ops.run_native_mode(consumer,"variant_probe/run",consumer/"actual-run",{"value":"installed"})
    assert result["exit_ok"],result
    assert json.loads((consumer/"actual-run/value.json").read_text())["value"]=="installed"
    remove=subprocess.run([sys.executable,"-m","refactory_dsh.registry","remove","variant_probe","--project",str(consumer)],capture_output=True,text=True)
    assert remove.returncode==0 and not (registry.workspace_root(consumer)/"variant_probe").exists()
    assert inventory(root)==original


def test_plugin_invalid_graph_cleanup_is_failure_not_structural_pass(tmp_path):
    root=package(tmp_path)
    f=root/"mode.py";f.write_text(f.read_text().replace(".ops:write", ".ops:nonexistent"))
    report=plugin_check(tmp_path,"variant_probe")
    assert not report["ok"] and report["removed"]
    assert report["raw_evidence"]["returncode"]==0  # child returns diagnostics; parent rejects them
    assert not list(tmp_path.glob("plugin-consumer-*"))


def test_variant_cli_reports_actual_exit_status(tmp_path):
    package(tmp_path)
    result=subprocess.run([sys.executable,"-m","refactory_dsh.create_fidelity_ops","plugin-check","variant_probe","--project",str(tmp_path)],capture_output=True,text=True)
    assert result.returncode==0 and json.loads(result.stdout)["ok"]


def test_native_prompt_contracts_match_graph_and_complete_package_fingerprint(tmp_path):
    from refactory_dsh.create_fidelity_prompts import NATIVE_INSTRUCTIONS
    graph=CreateFidelityMode().graph()
    for relative in graph.nodes["gate_strategy"].reads:
        assert f'`{relative}`' in NATIVE_INSTRUCTIONS
    assert graph.nodes["builder"].writes[0]==".factory/reviews/builder-latest.md"
    assert '`'+graph.nodes["builder"].writes[0]+'`' in NATIVE_INSTRUCTIONS
    root=package(tmp_path); before=inventory(root); authored=ops.package_digest(root)
    (root/"SKILL.md").write_text("changed generated skill")
    assert ops.package_digest(root)==authored and inventory(root)!=before
    assert "freeze all relevant package files" in NATIVE_INSTRUCTIONS
    assert not validate_state(CreateFidelityMode().state_dict(), invariants=CreateFidelityMode().invariants())


def test_legacy_public_modes_saved_states_remain_loadable():
    from refactory_dsh.create import CreateMode
    from refactory_dsh.design import DesignMode
    from refactory_dsh.fast import CreateFastMode, DesignFastMode
    for cls in (CreateMode, DesignMode, CreateFastMode, DesignFastMode):
        state=cls().state_dict()
        assert cls(state=state).state_dict()==state
        assert "revision_of" in state["knobs"] or cls in (DesignMode, DesignFastMode)


def test_legacy_create_checkpoint_resumes_its_existing_graph(tmp_path):
    from refactory_dsh.create import CreateMode
    # The absent old Task/single directive fails before model I/O, recording the
    # real legacy graph. A faithful graph must never reinterpret this checkpoint.
    mode=CreateMode()
    result=mode.run(tmp_path)
    assert not result.exit_ok and result.model_calls==0
    checkpoint=json.loads((tmp_path/".refactory/progress.json").read_text())
    assert checkpoint["graph"]==mode.graph().to_state()
    resumed=CreateMode.resume(tmp_path)
    assert not resumed.exit_ok and resumed.model_calls==0
    assert "graph or instructions changed" not in resumed.error
    with pytest.raises(ValueError,match="graph or instructions changed"):
        CreateFidelityMode.resume(tmp_path)
