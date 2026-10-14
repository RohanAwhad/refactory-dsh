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
from benchmarks.create_fidelity import native_temp
from benchmarks.create_fidelity import harness
from benchmarks.create_fidelity.contract import digest


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


def controlled_update(project):
    # Test-only solution: native authoring trials receive only the original +1.
    operation=project/".factory/packages/offset_probe/ops.py"
    original=operation.read_text().replace("value + 7","value + 1")
    operation.write_text(original.replace(
        '    ctx.write_json("adjusted.json", {"values": [value + 1 for value in values(ctx)]})',
        '''    payload=json.loads(Path(ctx.knob("values_path")).read_text(),parse_int=parse_decimal)
    if type(payload) is not list or any(type(value) is not int for value in payload):raise ValueError("exact integers required")
    ctx.write_text("adjusted.json",'{"values":['+','.join(decimal(value + 7) for value in payload)+']}')''')+'''

def parse_decimal(raw):
    negative=raw.startswith('-');raw=raw.lstrip('-');value=0
    for pos in range(0,len(raw),9):
        chunk=raw[pos:pos+9];value=value*10**len(chunk)+int(chunk)
    return -value if negative else value

def decimal(value):
    negative=value<0;value=abs(value);chunks=[]
    while value:
        value,part=divmod(value,10**9);chunks.append(part)
    raw='0' if not chunks else str(chunks[-1])+''.join(str(part).zfill(9) for part in reversed(chunks[:-1]))
    return ('-' if negative else '')+raw
''')
    return operation


@pytest.mark.parametrize("case_id",["native-update-offset-v1","native-plugin-reverse-v1","native-portable-document-v1"])
def test_provision_only_intended_original_input_no_final_artifacts(tmp_path,case_id):
    project=tmp_path/"case";spec,original,study=cases.provision(project,tmp_path,case_id)
    assert case_id in (project/"BRIEF.md").read_text()
    assert not (project/".factory/packages").exists()
    assert len(cases.fixtures(case_id))==8
    if spec["variant"]=="update-existing":
        assert study==original
        assert "+ 1" in (original/".factory/packages/offset_probe/ops.py").read_text()
        assert hash_tree(original)
    else:
        assert study==original and original.is_dir()
        assert not list(original.iterdir())


def test_tiny_document_checker_through_real_native_runtime_and_bad_control(tmp_path, monkeypatch):
    from test_create_fidelity_graph_ops import make_package
    monkeypatch.setattr(lane,"_offline_argv",lambda code:([sys.executable,"-c",code],{"status":"passed","kind":"controlled OS test shim"}))
    project=tmp_path/"project"
    spec,original,study=cases.provision(project,tmp_path,"native-portable-document-v1")
    init(project,study,spec)
    package=make_package(project)  # test control only; provisioning supplies no answer
    config=tmp_path/"config.json"
    config.write_text(json.dumps({"project":str(project),"variant_case":{"case_id":"native-portable-document-v1",**spec}}))
    result=cases.check_inside(config)
    assert result["status"]=="passed" and result["passed"]==8,result
    assert all(row["passed"] for row in result["extra_checks"])
    operation=package/"ops.py"
    operation.write_text("def read(ctx):\n    ctx.write_json('result.json',{})\n")
    failed=cases.check_inside(config)
    assert failed["status"]=="failed" and failed["passed"]<8


@pytest.mark.parametrize("case_id",["native-update-offset-v1","native-plugin-reverse-v1"])
def test_real_native_checker_correct_and_bad_controls(tmp_path,monkeypatch,case_id):
    # Fake only the OS boundary for portable controlled tests. Child Python,
    # native registry, validation, Executor, imports, files and guards are real.
    monkeypatch.setattr(lane,"_offline_argv",lambda code:([sys.executable,"-c",code],{"status":"passed","kind":"controlled OS test shim"}))
    project=tmp_path/"project";spec,original,study=cases.provision(project,tmp_path,case_id)
    init(project,study,spec)
    if spec["variant"]=="update-existing":
        operation=controlled_update(project)
    else: controlled_plugin(project)
    admission=ops.admit_native_package(project,spec["name"])
    assert admission["ok"],admission
    config=tmp_path/"config.json";config.write_text(json.dumps({"project":str(project),"variant_case":{"case_id":case_id,**spec}}))
    result=cases.check_inside(config)
    assert result["status"]=="passed" and result["total"]==result["expected_total"]==result["passed"]==8,result
    assert result["case_accepted"] is False and "unavailable" in result["authoring_fidelity"]
    assert all(r["passed"] for r in result["extra_checks"])
    assert result["evaluator_version"]==2
    assert result["package_inventory_version"]=="create-fidelity-artifact-inventory/v1"
    if spec["variant"]=="update-existing":operation.write_text(operation.read_text().replace("value + 7","value + 1"))
    else:
        operation=project/".factory/packages/reverse_plugin/ops.py"
        operation.write_text(operation.read_text().replace("value[::-1]","value"))
    failed=cases.check_inside(config)
    assert failed["status"]=="failed" and failed["passed"]<8


@pytest.mark.parametrize("values",[[True,11,11],[7.0,11,11],[7,11.0,11],[7,11,11,"extra"]])
def test_integer_output_tokens_cannot_pass_by_numeric_equality(values):
    assert not cases.matches_output({"values":values},{"values":[7,11,11]},"native-update-offset-v1")


def test_decimal_probe_rejects_quoted_float_bool_and_extra_keys():
    huge="1"+"0"*4300
    assert cases.matches_decimal_output('{"values":['+huge+']}',[huge])
    for raw in [json.dumps({"values":[huge]}),'{"values":['+huge+'.0]}','{"values":[true]}','{"values":['+huge+'],"extra":1}',
                '{"values":[],"values":['+huge+']}']:
        assert not cases.matches_decimal_output(raw,[huge])


def test_duplicate_output_keys_fail_without_silently_discarding_wrong_value(tmp_path):
    output=tmp_path/"output.json";output.write_text('{"values":[0],"values":[7]}')
    actual,error=cases.read_artifact(output)
    assert actual is None and "duplicate JSON object key" in error


@pytest.mark.parametrize("defect",["float-target","bool-identity"])
def test_real_native_integer_type_false_pass_controls(tmp_path,monkeypatch,defect):
    monkeypatch.setattr(lane,"_offline_argv",lambda code:([sys.executable,"-c",code],{"status":"passed","kind":"controlled OS test shim"}))
    project=tmp_path/"project";spec,original,study=cases.provision(project,tmp_path,"native-update-offset-v1")
    init(project,study,spec);operation=controlled_update(project)
    text=operation.read_text()
    if defect=="float-target":
        text=text.replace("decimal(value + 7)","str(float(value + 7)) if abs(value)<10**100 else decimal(value + 7)")
    else:
        text=text.replace('ctx.write_json("identity.json", {"values": values(ctx)})','ctx.write_json("identity.json", {"values": [True,-2]})')
    operation.write_text(text)
    config=tmp_path/"config.json";config.write_text(json.dumps({"project":str(project),"variant_case":{"case_id":"native-update-offset-v1",**spec}}))
    result=cases.check_inside(config)
    assert result["status"]=="failed"
    if defect=="float-target":assert result["passed"]<8
    else:
        assert result["passed"]==8
        assert not next(row for row in result["extra_checks"] if row["check"]=="unrelated-revised")["passed"]


def test_variant_inventory_includes_cache_named_authored_files(tmp_path):
    for name in ("__pycache__/authored.bin",".pytest_cache/request.json",".git/config","ordinary.py"):
        path=tmp_path/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text("before")
    before=cases.package_inventory(tmp_path)
    assert set(before)=={"__pycache__/authored.bin",".pytest_cache/request.json",".git/config","ordinary.py"}
    (tmp_path/"__pycache__/authored.bin").write_text("after")
    assert cases.package_inventory(tmp_path)!=before


def test_real_native_decimal_limit_and_cache_mutation_are_failed_checks(tmp_path,monkeypatch):
    monkeypatch.setattr(lane,"_offline_argv",lambda code:([sys.executable,"-c",code],{"status":"passed","kind":"controlled OS test shim"}))
    project=tmp_path/"project";spec,original,study=cases.provision(project,tmp_path,"native-update-offset-v1")
    init(project,study,spec)
    operation=project/".factory/packages/offset_probe/ops.py"
    operation.write_text(operation.read_text().replace("value + 1","value + 7"))
    config=tmp_path/"config.json";config.write_text(json.dumps({"project":str(project),"variant_case":{"case_id":"native-update-offset-v1",**spec}}))
    limited=cases.check_inside(config)
    assert limited["passed"]==8 and limited["status"]=="failed"
    assert all(not row["passed"] for row in limited["extra_checks"] if row["check"].startswith("large-"))
    operation=controlled_update(project)
    cache=operation.parent/"__pycache__/authored.bin";cache.parent.mkdir();cache.write_text("before")
    operation.write_text(operation.read_text().replace("def adjust(ctx):","def adjust(ctx):\n    Path(__file__).with_name('__pycache__').joinpath('authored.bin').write_text('mutated')"))
    changed=cases.check_inside(config)
    assert changed["passed"]==8 and changed["status"]=="failed"
    freeze=next(row for row in changed["extra_checks"] if row["check"]=="delivered package unchanged")
    assert not freeze["passed"]
    assert freeze["before"]["__pycache__/authored.bin"]!=freeze["after"]["__pycache__/authored.bin"]


def test_frozen_v1_checker_refuses_upgrade_in_place(tmp_path):
    (tmp_path/cases.LANE).mkdir()
    (tmp_path/cases.LANE/"config.json").write_text('{}')
    (tmp_path/"trial.json").write_text(json.dumps({"evaluator_version":1}))
    with pytest.raises(ValueError,match="never overwrite historical v1"):
        cases.check(tmp_path)
    assert not (tmp_path/"operator").exists()


def test_supplemental_evaluator_preserves_old_trial_and_pins_dependencies(tmp_path,monkeypatch):
    root=tmp_path/"old-trial";adapter=root/"adapter/benchmarks/create_fidelity";adapter.mkdir(parents=True)
    (adapter/"variant_cases.py").write_text("# immutable v1 checker\n")
    (adapter/"preserved_dependency.py").write_text("# pinned dependency\n")
    old_config=root/cases.LANE/"config.json";old_config.parent.mkdir()
    old_config.write_text(json.dumps({"adapter_root":str(root/"adapter"),"project":str(root/"project")}))
    (root/"trial.json").write_text(json.dumps({"checker_sha256":"frozen-v1"}))
    original=root/"operator/variant-capability.json";original.parent.mkdir();original.write_text('{"status":"failed","evaluator_version":1}')
    before={path.relative_to(root).as_posix():path.read_bytes() for path in (old_config,root/"trial.json",original,adapter/"variant_cases.py")}
    captured=[]
    def controlled_check(evaluation):
        captured.append(evaluation)
        config=json.loads((evaluation/cases.LANE/"config.json").read_text())
        assert config["variant_check_owner"].endswith("variant-check-supervisor-v2")
        assert config["supplemental_operator_root"]==str(root/"operator")
        provenance=json.loads((evaluation/"evaluator-provenance.json").read_text())
        assert provenance["original_evaluator_version"]==1
        assert set(provenance["updated_modules"])=={"variant_cases.py","supervision.py","artifact_inventory.py"}
        assert provenance["package_inventory_version"]=="create-fidelity-artifact-inventory/v1"
        copied=Path(config["adapter_root"])/"benchmarks/create_fidelity/preserved_dependency.py"
        assert copied.read_bytes()==(adapter/"preserved_dependency.py").read_bytes()
        return {"status":"failed","evaluator_version":2}
    # This test checks freezing/provenance only; real native semantics are above.
    monkeypatch.setattr(cases,"check",controlled_check)
    result=cases.supplemental_check(root)
    assert result["original_evaluator_version"]==1 and result["case_accepted"] is False
    assert result["supplemental_evaluation_id"]==cases.EVALUATOR_ID
    assert all((root/name).read_bytes()==body for name,body in before.items())
    with pytest.raises(ValueError,match="preserve it rather than overwrite"):
        cases.supplemental_check(root)


def prepared_temp_variant(tmp_path):
    """Actual source inspection/provisioning/policy; no model or auth fixture."""
    import refactory_dsh
    source=tmp_path/"source";source.mkdir()
    shutil.copytree(Path(refactory_dsh.__file__).parent,source/"refactory/refactory_dsh",
                    ignore=shutil.ignore_patterns("__pycache__"))
    runtime=tmp_path/"runtime";(runtime/"node_modules").mkdir(parents=True)
    template_root=tmp_path/"main-trial";old_project=template_root/cases.LANE/"project";old_project.mkdir(parents=True)
    (old_project/"isolation-canary.txt").write_text("template project")
    old_home=old_project/"runtime/dsh";old_home.mkdir(parents=True)
    auth=tmp_path/"controlled-auth-reference";auth.write_text("no credential values; controlled config test")
    old_temp=native_temp.create("main-trial",cases.LANE)
    peer_temp=native_temp.create("main-trial","remote-reference")
    template=template_root/cases.LANE/"config.json"
    identity=cases.source_identity(source,"controlled")
    old={"source":str(source),"identity":identity,"project":str(old_project),"python":sys.executable,
         "model_context":{"runtime_root":str(runtime),"model":"gpt-6-sol","effort":"high","dsh_home":str(old_home)},
         "runtime_state_isolation":{"dsh_home":str(old_home),"shared_auth_reference":str(auth)},
         "native_temp_mapping":old_temp,"native_environment_mapping":{"native_home":"foreign"},
         "native_credential_mapping":{"path":"foreign-adc"},"native_vertex_mapping":{"version":"foreign-vertex"},
         "native_cli_identity":{"path":"foreign-cli"},"remote_route":"foreign"}
    template.write_text(json.dumps(old))
    peer_config=template_root/"remote-reference/config.json";peer_config.parent.mkdir()
    peer_config.write_text(json.dumps({"native_temp_mapping":peer_temp}))
    root=tmp_path/"new-variant"
    try:
        cases.prepare(root,template,"native-plugin-reverse-v1")
    except BaseException:
        native_temp.cleanup(old_temp);native_temp.cleanup(peer_temp)
        creation=root/"operator/native-temp-creation.json"
        if creation.is_file():native_temp.cleanup(json.loads(creation.read_text())["mappings"][cases.LANE])
        raise
    return root,old,[old_temp,peer_temp]


def test_variant_allocates_own_identity_bound_temp_and_denies_template_peers(tmp_path):
    root,old,mappings=prepared_temp_variant(tmp_path)
    config=json.loads((root/cases.LANE/"config.json").read_text());own=config["native_temp_mapping"]
    try:
        native_temp.validate(own)
        assert own["path"] not in {mapping["path"] for mapping in mappings}
        assert own["trial_id"]==root.name and own["lane_id"]==cases.LANE
        assert config["runtime_state_isolation"]["native_tmp"]==own["path"]
        assert all(key not in config for key in ("remote_route","native_environment_mapping","native_credential_mapping","native_vertex_mapping","native_cli_identity"))
        creation=json.loads((root/"operator/native-temp-creation.json").read_text())
        assert creation["mappings"]=={cases.LANE:own}
        policy=Path(config["policy"]).read_text()
        assert f'(allow file-write* (subpath {json.dumps(own["path"])}))' in policy
        for path in [old["project"],*[mapping["path"] for mapping in mappings]]:
            assert f'(deny file-read* (subpath {json.dumps(path)}))' in policy
            assert f'(deny file-write* (subpath {json.dumps(path)}))' in policy
            assert str(Path(path)/"isolation-canary.txt") in config["forbidden_controls"]
        graph=json.loads((root/"operator/graphs"/(cases.LANE+".json")).read_text())
        assert graph["status"]=="passed"
    finally:
        for mapping in [own,*mappings]:native_temp.cleanup(mapping)


def test_variant_temp_preflight_actual_own_write_peer_denial(tmp_path):
    if sys.platform!="darwin" or not Path('/usr/bin/sandbox-exec').exists():pytest.skip("actual Seatbelt unavailable")
    readiness=subprocess.run(['/usr/bin/sandbox-exec','-p','(version 1)\n(allow default)',sys.executable,'-B','-c','print("boundary ready")'],capture_output=True,text=True,timeout=15)
    if 'sandbox_apply: Operation not permitted' in readiness.stderr:pytest.skip("nested Seatbelt unavailable; rerun this exact test outside caller sandbox")
    assert readiness.returncode==0,readiness.stderr
    root,old,mappings=prepared_temp_variant(tmp_path)
    config=json.loads((root/cases.LANE/"config.json").read_text());own=config["native_temp_mapping"]
    try:
        result=native_temp.probe(config,{"PATH":str(Path(sys.executable).parent),"PYTHONDONTWRITEBYTECODE":"1"})
        assert result["status"]=="passed",result
        for mapping in mappings:
            checks=[row for row in result["observed"]["checks"] if row.get("path")==str(Path(mapping["path"])/"isolation-canary.txt")]
            assert {row["operation"] for row in checks}=={"read","write"} and all(row["denied"] for row in checks)
    finally:
        for mapping in [own,*mappings]:native_temp.cleanup(mapping)


def test_variant_prepare_real_policy_preflight_and_bound_selected_launch(tmp_path,monkeypatch):
    """Real preparation/isolation, controlled external route, no model dispatch."""
    if sys.platform!="darwin" or not Path('/usr/bin/sandbox-exec').exists():pytest.skip("actual Seatbelt unavailable")
    readiness=subprocess.run(['/usr/bin/sandbox-exec','-p','(version 1)\n(allow default)',sys.executable,'-B','-c','print("boundary ready")'],capture_output=True,text=True,timeout=15)
    if 'sandbox_apply: Operation not permitted' in readiness.stderr:pytest.skip("nested Seatbelt unavailable; actual variant preflight remains required")
    assert readiness.returncode==0,readiness.stderr
    root,old,mappings=prepared_temp_variant(tmp_path)
    config_path=root/cases.LANE/"config.json";config=json.loads(config_path.read_text())
    own=config["native_temp_mapping"];policy=Path(config["policy"])
    try:
        route=tmp_path/"controlled-external-route"
        # These test-only external receipts never enter a live evidence root.
        harness.write(route/"probe.json",{"status":"completed","kind":"controlled route fixture"})
        harness.write(route/".refactory/workers/controlled.json",{"actual_route":{
            "worker_backend":"dsh","model":config["model_context"]["model"],
            "effort":config["model_context"]["effort"],"source":"runtime-request"}})
        observed=cases.preflight(root,route)
        assert observed["status"]=="passed",observed
        assert observed["checked_at"]
        expected={"config_sha256":digest(config_path),"policy_sha256":digest(policy)}
        assert observed["lane_bindings"]=={cases.LANE:expected}
        calls=[]
        monkeypatch.setattr(harness,"run_lane",lambda root,lane,operation,**kwargs:
                            calls.append((lane,operation)) or {"status":"controlled-dispatch"})
        assert harness.run_pair(root,[cases.LANE])==[{"status":"controlled-dispatch"}]
        assert calls==[(cases.LANE,"author")]
        snapshot=harness.read(root/"operator/selected-launch.json")
        assert snapshot["source_preflight"]["sha256"]==digest(root/"operator/preflight.json")
        assert snapshot["source_preflight"]["checked_at"]==observed["checked_at"]
        assert snapshot["lanes"][cases.LANE]["observed_binding"]==expected
        for path in (config_path,policy):
            body=path.read_bytes();path.write_bytes(body+b"\n")
            assert harness.run_pair(root,[cases.LANE])[0]["status"]=="unavailable"
            assert calls==[(cases.LANE,"author")]
            path.write_bytes(body)
        # A passed fixture receipt still cannot bypass a missing real guard.
        observed["native_temp_isolation"].pop(cases.LANE)
        harness.write(root/"operator/preflight.json",observed)
        assert harness.run_pair(root,[cases.LANE])[0]["status"]=="unavailable"
        assert calls==[(cases.LANE,"author")]
    finally:
        for mapping in [own,*mappings]:native_temp.cleanup(mapping)


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
