"""Independent neutral variant cases; controlled solutions exist only in tests."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from benchmarks.create_fidelity import variant_cases as cases
from benchmarks.create_fidelity import lane
from benchmarks.create_fidelity.evidence import hash_tree
from refactory_dsh import create_fidelity_ops as ops
from refactory_dsh.create_fidelity import CreateFidelityMode
from refactory_dsh.nn import Graph, execute


def init(project, study, spec):
    graph=Graph();graph.add(CreateFidelityMode().graph().nodes["init_user_intent"])
    result=execute(graph,project,knobs={"name":spec["name"],"variant":spec["variant"],"brief":(project/"BRIEF.md").read_text(),"requires_input":spec["requires_input"],"revision_of":spec.get("revision_of",""),"project_path":str(study)})
    assert result.exit_ok,result.error


def controlled_plugin(project):
    # Never used by provisioning/live runners: only tests author this control.
    root=project/".factory/packages/reverse_plugin";root.mkdir(parents=True)
    (root/"__init__.py").write_text("from refactory_dsh import nn\nfrom .mode import Reverse\nPACKAGE=nn.Package(name='reverse_plugin',description='Controlled native reverse plugin',modes=(Reverse,),ops=('refactory_packages.reverse_plugin.ops',))\n")
    (root/"mode.py").write_text("""from refactory_dsh import nn
class Reverse(nn.Mode):
    name='run'
    description='Reverse Unicode code points'
    unscored_reason='Artifact producer'
    @classmethod
    def declare(cls):return {'text_path':nn.Knob('','str')}
    def build(self):
        g=nn.Graph();g.add(nn.Node('reverse','op',call='refactory_packages.reverse_plugin.ops:reverse',knobs=('text_path',),writes=('text.json',)));return g
    def invariants(self):return (nn.artifact_produced('text.json',by='reverse'),)
""")
    (root/"ops.py").write_text("import json\nfrom pathlib import Path\ndef reverse(ctx):\n    value=json.loads(Path(ctx.knob('text_path')).read_text(encoding='utf-8'))\n    if not isinstance(value,str):raise ValueError('text_path must be a JSON string')\n    ctx.write_json('text.json',{'text':value[::-1]})\n")
    (root/"README.md").write_text("# Controlled test plugin\n\nNative registry import/discovery/run/remove uses a separate consumer project. This test control is never copied into a live authoring project.\n")


@pytest.mark.parametrize("case_id",["native-update-offset-v1","native-plugin-reverse-v1"])
def test_provision_only_intended_original_input_no_final_artifacts(tmp_path,case_id):
    project=tmp_path/"case";spec,original,study=cases.provision(project,tmp_path,case_id)
    assert case_id in (project/"BRIEF.md").read_text()
    assert not (project/".factory/packages").exists()
    assert len(cases.fixtures(case_id))==8
    if spec["variant"]=="update-existing":
        assert study==original
        assert "+ 1" in (original/".factory/packages/offset_probe/ops.py").read_text()
        assert hash_tree(original)
    else: assert not original.exists()


@pytest.mark.parametrize("case_id",["native-update-offset-v1","native-plugin-reverse-v1"])
def test_real_native_checker_correct_and_bad_controls(tmp_path,monkeypatch,case_id):
    # Fake only the OS boundary for portable controlled tests. Child Python,
    # native registry, validation, Executor, imports, files and guards are real.
    monkeypatch.setattr(lane,"_offline_argv",lambda code:([sys.executable,"-c",code],{"status":"passed","kind":"controlled OS test shim"}))
    project=tmp_path/"project";spec,original,study=cases.provision(project,tmp_path,case_id)
    init(project,study,spec)
    if spec["variant"]=="update-existing":
        operation=project/".factory/packages/offset_probe/ops.py"
        operation.write_text(operation.read_text().replace("value + 1","value + 7"))
    else: controlled_plugin(project)
    admission=ops.admit_native_package(project,spec["name"])
    assert admission["ok"],admission
    config=tmp_path/"config.json";config.write_text(json.dumps({"project":str(project),"variant_case":{"case_id":case_id,**spec}}))
    result=cases.check_inside(config)
    assert result["status"]=="passed" and result["total"]==result["expected_total"]==result["passed"]==8,result
    assert result["case_accepted"] is False and "unavailable" in result["authoring_fidelity"]
    assert all(r["passed"] for r in result["extra_checks"])
    if spec["variant"]=="update-existing":operation.write_text(operation.read_text().replace("value + 7","value + 1"))
    else:
        operation=project/".factory/packages/reverse_plugin/ops.py"
        operation.write_text(operation.read_text().replace("value[::-1]","value"))
    failed=cases.check_inside(config)
    assert failed["status"]=="failed" and failed["passed"]<8


def test_missing_offline_boundary_cannot_pass_native_case(tmp_path,monkeypatch):
    monkeypatch.setattr(lane,"_offline_argv",lambda code:(None,{"status":"unavailable","reason":"controlled OS boundary absent"}))
    project=tmp_path/"project";spec,original,study=cases.provision(project,tmp_path,"native-plugin-reverse-v1")
    controlled_plugin(project)
    config=tmp_path/"config.json";config.write_text(json.dumps({"project":str(project),"variant_case":{"case_id":"native-plugin-reverse-v1",**spec}}))
    result=cases.check_inside(config)
    assert result["status"]=="unavailable" and result["passed"]==0 and result["case_accepted"] is False


def test_malformed_native_artifact_records_failed_rows_and_continues(tmp_path,monkeypatch):
    monkeypatch.setattr(lane,"_offline_argv",lambda code:([sys.executable,"-c",code],{"status":"passed","kind":"controlled OS test shim"}))
    project=tmp_path/"project";spec,original,study=cases.provision(project,tmp_path,"native-plugin-reverse-v1")
    init(project,study,spec);controlled_plugin(project)
    operation=project/".factory/packages/reverse_plugin/ops.py"
    operation.write_text(operation.read_text().replace("ctx.write_json('text.json',{'text':value[::-1]})","ctx.write_text('text.json','invalid JSON')"))
    config=tmp_path/"config.json";config.write_text(json.dumps({"project":str(project),"variant_case":{"case_id":"native-plugin-reverse-v1",**spec}}))
    result=cases.check_inside(config)
    assert result["status"]=="failed" and result["total"]==8
    assert sum(bool(row["output_error"]) for row in result["rows"])==4
    assert result["extra_checks"][0]["removal"]["removed"]


def test_copied_oracle_is_forbidden_even_inside_allowed_adapter(tmp_path):
    from benchmarks.create_fidelity.isolation import profile
    project=tmp_path/"project";project.mkdir()
    adapter=project/"runtime/benchmark";adapter.mkdir(parents=True)
    oracle=adapter/"benchmarks/create_fidelity/variant_cases.py";oracle.parent.mkdir(parents=True)
    shutil.copyfile(Path(cases.__file__),oracle)
    visible=adapter/"visible.py";visible.write_text("allowed adapter control\n")
    policy=profile(project,[],[adapter],cases.authoring_hidden(tmp_path/"operator",tmp_path/"source",adapter),allowed_reads=[adapter,Path(sys.prefix),Path(sys.base_prefix)])
    assert f'(deny file-read* (subpath {json.dumps(str(oracle.resolve()))}))' in policy
    assert '"'+str(oracle)+'"' in policy
    if sys.platform!="darwin" or not Path('/usr/bin/sandbox-exec').exists(): pytest.skip("actual Seatbelt unavailable")
    code="""import subprocess,sys
from pathlib import Path
visible,oracle=sys.argv[1:]
assert Path(visible).read_text()=='allowed adapter control\\n'
try:Path(oracle).read_text();raise AssertionError('oracle readable')
except PermissionError:pass
child=subprocess.run([sys.executable,'-c','from pathlib import Path; import sys; Path(sys.argv[1]).read_text()',oracle],capture_output=True)
assert child.returncode!=0 and b'PermissionError' in child.stderr
"""
    result=subprocess.run(['/usr/bin/sandbox-exec','-p',policy,sys.executable,'-c',code,str(visible),str(oracle)],cwd=project,capture_output=True,text=True,timeout=20)
    if 'sandbox_apply: Operation not permitted' in result.stderr: pytest.skip("nested Seatbelt unavailable; real per-trial preflight remains mandatory")
    assert result.returncode==0,result.stderr
