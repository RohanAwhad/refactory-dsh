"""Separate native variant trials; never reuse booking artifacts or scorecards.

Prepare from a trusted lane configuration, then use the ordinary full author
entry point. Only the intended original +1 package is seeded for a revision;
no final generated implementation is provisioned. Capability verification is
operator-owned, offline and distinct from full semantic authoring acceptance.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .contract import digest
from .evidence import hash_tree, route_score
from .harness import read, write, source_identity, runtime_identity, static_graph, execute_command
from .isolation import profile, probe, runtime_probe
from .artifact_inventory import strict_artifact_inventory, VERSION as INVENTORY_VERSION

FIXTURES = Path(__file__).resolve().parents[2] / "tests/fixtures/create_fidelity/variants/v1"
LANE = "dsh-candidate"
EVALUATOR_VERSION = 2
EVALUATOR_ID = "create-fidelity-native-variant-capability/v2"


def package_inventory(root: Path) -> dict[str, str]:
    """Freeze every regular package file; no cache-directory exemptions.

    Verification compiles current source with bytecode writes disabled and the
    delivered package is read-only. Thus generated cache files already present
    can also be included; authored data cannot hide behind a cache-like name.
    """
    return strict_artifact_inventory(root)


def matches_output(actual: object, expected: dict, case_id: str) -> bool:
    """Check token types before equality (Python bool/float can equal int)."""
    if type(actual) is not dict or set(actual)!=set(expected): return False
    if case_id=="native-update-offset-v1":
        values=actual.get("values")
        return (type(values) is list and len(values)==len(expected["values"])
                and all(type(value) is int for value in values) and values==expected["values"])
    if case_id=="native-plugin-reverse-v1":
        return type(actual.get("text")) is str and actual["text"]==expected["text"]
    if case_id=="native-portable-document-v1":
        return json.dumps(actual,sort_keys=True,ensure_ascii=False)==json.dumps(expected,sort_keys=True,ensure_ascii=False)
    raise ValueError("unknown versioned variant output contract")


class IntegerToken(str):
    """Operator-only lexical JSON integer; quoted strings retain plain str."""


def unique_object(pairs: list[tuple[str, object]]) -> dict:
    result={}
    for key,value in pairs:
        if key in result: raise ValueError(f"duplicate JSON object key: {key}")
        result[key]=value
    return result


def matches_decimal_output(raw: str, expected: list[str]) -> bool:
    # Do not change Python's process-wide digit guard. Native children still
    # have their ordinary guard; only independent evidence parses long tokens.
    try: actual=json.loads(raw,parse_int=IntegerToken,object_pairs_hook=unique_object)
    except (ValueError,UnicodeError): return False
    return (type(actual) is dict and set(actual)=={"values"}
            and type(actual["values"]) is list and len(actual["values"])==len(expected)
            and all(type(value) is IntegerToken for value in actual["values"])
            and actual["values"]==expected)


def authoring_hidden(operator: Path, source: Path, adapter: Path) -> list[Path]:
    return [operator,FIXTURES,adapter/"benchmarks/create_fidelity/variant_cases.py",
            source/"refactory/tests",source/"refactory/benchmarks"]


def case(case_id: str) -> dict:
    return json.loads((FIXTURES / "cases.json").read_text())["cases"][case_id]


def provision(project: Path, source: Path, case_id: str) -> tuple[dict, Path, Path]:
    """Seed only public original input; never provision a final generated package."""
    supplied=case(case_id);project.mkdir(parents=True)
    original=project/"original-project"
    if supplied["variant"]=="update-existing":
        target=original/".factory/packages"/supplied["name"]
        shutil.copytree(FIXTURES/"update_seed"/supplied["name"],target)
    elif supplied["variant"] in ("plugin", "portable"):
        # A new package has no existing application to study. Keep its
        # empty local context inside this lane; the framework is an API
        # reference, not an authoring project (its tests remain hidden).
        original.mkdir()
    study=original if original.exists() else source
    seed_note=("The study project is empty; no existing package or finished output is supplied."
               if supplied["variant"] in ("plugin", "portable") else
               "The original seed is intended input only, not the requested final implementation.")
    brief=supplied["brief"]+f"\n\nNative case identity: {case_id}; fixture version 1. Author into {project}/.factory/packages/{supplied['name']}. Source project for study: {study}. {seed_note}\n"
    (project/"BRIEF.md").write_text(brief)
    return supplied,original,study


def prepare(root: Path, template: Path, case_id: str) -> dict:
    root=root.resolve(); supplied=case(case_id); old=read(template)
    if root.exists():
        raise ValueError("variant trial root must be new; no frozen config is overwritten")
    source=Path(old["source"]); identity=source_identity(source,old["identity"]["declared_revision"])
    if identity != old["identity"]:
        raise ValueError("template source changed; freeze the intended source before preparing a variant trial")
    project=root/LANE/"project";supplied,original,study=provision(project,source,case_id)
    operator=root/"operator"; operator.mkdir(); (operator/"isolation-canary.txt").write_text("operator-only variant checker\n")
    # Copy only launcher profile metadata and a read-only credential reference.
    home=project/"runtime/dsh"; profile_dir=home/"profiles/headless"; profile_dir.mkdir(parents=True)
    previous_home=Path(old["runtime_state_isolation"]["dsh_home"])
    for filename in ("package.json","cordis.patch.yml","pnpm-workspace.yaml"):
        src=previous_home/"profiles/headless"/filename
        if src.is_file(): shutil.copyfile(src,profile_dir/filename)
    auth=Path(old["runtime_state_isolation"]["shared_auth_reference"])
    if auth.is_file(): (home/".credentials.yaml").symlink_to(auth.resolve())
    (project/"runtime/claude").mkdir(); (project/"tmp").mkdir()
    package=Path(__file__).resolve().parents[2]
    adapter=project/"runtime/benchmark"; destination=adapter/"benchmarks/create_fidelity"; destination.mkdir(parents=True)
    shutil.copyfile(package/"benchmarks/__init__.py",adapter/"benchmarks/__init__.py")
    for module in (package/"benchmarks/create_fidelity").glob("*.py"): shutil.copyfile(module,destination/module.name)
    runtime=Path(old["model_context"]["runtime_root"])
    pin=runtime_identity(runtime)
    python=old["python"]
    prefixes=json.loads(subprocess.check_output([python,"-c","import sys,json;print(json.dumps([sys.prefix,sys.base_prefix]))"],text=True))
    dependencies=[runtime/"node_modules",Path(pin["dependencies"]["root"]),*map(Path,prefixes)]
    if shutil.which("node"): dependencies.append(Path(shutil.which("node")).resolve())
    policy=root/LANE/"isolation.sb"; config_path=root/LANE/"config.json"
    from .native_temp import create as create_native_temp
    native_temp=create_native_temp(root.name,LANE);own_temp=Path(native_temp["path"])
    write(operator/"native-temp-creation.json",{"mappings":{LANE:native_temp},"status":"created"})
    # A template supplies immutable source/runtime settings, never another
    # trial's mutable project/session/temporary roots or remote credentials.
    peer_projects={Path(old["project"])};peer_temps=set()
    for path in template.parent.parent.glob("*/config.json"):
        peer_config=read(path)
        if peer_config.get("project"):peer_projects.add(Path(peer_config["project"]))
        if peer_config.get("native_temp_mapping"):
            peer_temps.add(Path(peer_config["native_temp_mapping"]["path"]))
    if old.get("native_temp_mapping"):peer_temps.add(Path(old["native_temp_mapping"]["path"]))
    peers=sorted(peer_projects|peer_temps)
    # The independent oracle is copied with adapters but must remain unreadable
    # in authoring. Its separate offline checker policy can execute it later.
    hidden_checker=destination/"variant_cases.py"
    hidden=authoring_hidden(operator,source,adapter)
    readonly=[source,package,adapter,*dependencies,auth,config_path]
    if original.exists(): readonly.append(original)
    policy.write_text(profile(project,[operator,*peers],readonly,hidden,
                              allowed_reads=[source,adapter,*dependencies,auth,config_path,policy,own_temp],
                              writable_roots=[own_temp]))
    knobs={"name":supplied["name"],"variant":supplied["variant"],"requires_input":supplied["requires_input"],"project_path":str(study)}
    if supplied.get("revision_of"): knobs["revision_of"]=supplied["revision_of"]
    config={**old,"lane":LANE,"trial_id":root.name,"project":str(project),"mode":"create_fidelity", "identity":identity,
            "variant_case":{"case_id":case_id,**supplied},"authoring_knobs":knobs,"authoring_brief_sha256":digest(project/"BRIEF.md"),
            "policy":str(policy),"adapter_root":str(adapter),"adapter_sha256":hash_tree(adapter),"runtime_identity":pin,
            "inputs":str(project/"inputs"),"model_context":{**old["model_context"],"dsh_home":str(home)},
            "native_temp_mapping":native_temp,
            "runtime_state_isolation":{"status":"pending-probe","dsh_home":str(home),"native_tmp":str(own_temp),"claude_config_dir":str(project/"runtime/claude"),"private_tmp":str(project/"tmp"),"shared_auth_reference":str(auth)},
            "forbidden_controls":[str(operator/"isolation-canary.txt"),str(FIXTURES/"cases.json"),str(hidden_checker),
                                  *[str(path/"isolation-canary.txt") for path in peers]],
            "strategy_decision_policy":"direct-human-or-explicit-delegated-operator; exact reviewed gate bytes only"}
    for remote_only in ("remote_route","native_environment_mapping","native_credential_mapping","native_vertex_mapping","native_cli_identity"):
        config.pop(remote_only,None)
    if supplied.get("development_limits"):
        config["development_limits"] = supplied["development_limits"]
        config["total_timeout_s"] = supplied["development_limits"]["max_elapsed_s"]
    write(config_path,config)
    trial={"schema_version":1,"kind":"native-variant-case","fixture_version":1,"trial_id":root.name,"case_id":case_id,
           "evaluator_id":EVALUATOR_ID,"evaluator_version":EVALUATOR_VERSION,
           "case_fixture_sha256":digest(FIXTURES/"cases.json"),"checker_sha256":digest(destination/"variant_cases.py"),
           "original_seed":package_inventory(original) if original.exists() else None,
           "original_seed_inventory_version":INVENTORY_VERSION,"brief_sha256":digest(project/"BRIEF.md"),
           "model":config["model_context"]["model"],"effort":config["model_context"]["effort"],"source":identity,
           "native_temp_mapping":native_temp,
           "booking56_acceptance":"not applicable; separate case and checker", "authoring_fidelity":"requires independent complete semantic review"}
    write(root/"trial.json",trial); write(operator/"graphs"/f"{LANE}.json",static_graph(source,Path(python),"create_fidelity"))
    return trial


def preflight(root: Path, receipt_root: Path) -> dict:
    config_path=root/LANE/"config.json";config=read(config_path);project=Path(config["project"])
    checked_at=datetime.now(timezone.utc).isoformat()
    binding={"config_sha256":digest(config_path),"policy_sha256":digest(Path(config["policy"]))}
    isolation=probe(project,[root/"operator"],Path(config["policy"]),config["python"])
    runtime=runtime_probe(project,Path(config["policy"]),config["python"],[Path(p) for p in config["forbidden_controls"]])
    from .native_temp import probe as native_temp_probe
    try: native_temp=native_temp_probe(config,{**os.environ,"PYTHONDONTWRITEBYTECODE":"1"})
    except (OSError,ValueError,subprocess.TimeoutExpired) as error:
        native_temp={"status":"unavailable","reason":f"{type(error).__name__}: {error}"}
    receipts=[read(p) for p in receipt_root.rglob(".refactory/workers/*.json")]
    route=route_score(receipts,model=config["model_context"]["model"],effort=config["model_context"]["effort"])
    observed=read(receipt_root/"probe.json",read(receipt_root/"probe-result.json",{}))
    if observed.get("status")!="completed": route.update(status="unavailable",reason="served route probe did not complete")
    report={"kind":"native-variant-case-preflight","isolation":{LANE:isolation},"runtime_state_isolation":{LANE:runtime},
            "checked_at":checked_at,"lane_bindings":{LANE:binding},
            "native_temp_isolation":{LANE:native_temp},"route":route,
            "status":"passed" if all(x["status"]=="passed" for x in (isolation,runtime,native_temp,route)) else "unavailable"}
    write(root/"operator/preflight.json",report);return report


def fixtures(case_id: str) -> list[tuple[str, str, dict | None]]:
    # Independent public-contract expectations; no generated implementation is imported.
    if case_id=="native-update-offset-v1":
        return [("empty","[]",{"values":[]}), ("positive","[0, 4, 4]",{"values":[7,11,11]}),
                ("negative","[-9, -1]",{"values":[-2,6]}), ("large",json.dumps([10**70]),{"values":[10**70+7]}),
                ("bool","[true]",None),("float","[1.0]",None),("root","{}",None),("syntax","[",None)]
    if case_id=="native-plugin-reverse-v1":
        return [("empty",'""',{"text":""}),("ascii",'"abc"',{"text":"cba"}),
                ("unicode",json.dumps("e\u0301雪😀"),{"text":"😀雪\u0301e"}),
                ("whitespace",json.dumps(" a\n b "),{"text":" b \na "}),
                ("number","7",None),("object","{}",None),("array","[]",None),("syntax",'"',None)]
    if case_id=="native-portable-document-v1":
        return [("empty","{}",{}),("first",'{"name":"first"}',{"name":"first"}),
                ("second",'{"n":2,"nested":{"items":[true,null,"雪"]}}',{"n":2,"nested":{"items":[True,None,"雪"]}}),
                ("whitespace",'{"text":" a\\n b "}',{"text":" a\n b "}),
                ("array","[]",None),("number","7",None),("string",'"object"',None),("syntax","{",None)]
    raise ValueError("unknown versioned variant case")


def read_artifact(path: Path) -> tuple[object, str | None]:
    if not path.is_file(): return None,None
    try: return json.loads(path.read_text(),object_pairs_hook=unique_object),None
    except (ValueError,UnicodeError,OSError) as error: return None,f"{type(error).__name__}: {error}"


def check_inside(config_path: Path) -> dict:
    """Run only under the mandatory inherited offline boundary."""
    from .lane import _native_child
    from refactory_dsh.create_fidelity_variants import source_import_code

    config=read(config_path);project=Path(config["project"]);spec=config["variant_case"];name=spec["name"]
    package=project/".factory/packages"/name
    before=package_inventory(package);rows=[];temporary=Path(tempfile.mkdtemp(prefix="variant-check-",dir=project))
    consumer=temporary/"consumer"; consumer.mkdir()
    common=source_import_code(name)+"\nimport json\nfrom pathlib import Path\nfrom refactory_dsh import registry\nfrom refactory_dsh.nn import execute,validate_state\n"
    installation=None
    execution_project=project
    if spec["variant"]=="plugin":
        installation=_native_child(project,common+f"registry.add(Path({str(consumer)!r}),Path({str(package)!r}))\np=registry.load({name!r},Path({str(consumer)!r}))\nprint(json.dumps({{'installed':p.ids(),'discoverable':{name!r} in registry.catalogue(Path({str(consumer)!r}))}}))")
        execution_project=consumer
    for case_name,raw,expected in fixtures(spec["case_id"]):
        source=temporary/(case_name+".json");source.write_text(raw);run=temporary/case_name
        identifier=name+"/"+spec["mode"]
        code=common+f"_,cls=registry.resolve(Path({str(execution_project)!r}),{identifier!r})\nm=cls();s=m.state_dict()\ns['knobs'][{spec['requires_input']!r}]['value']={str(source)!r}\nm.load_state_dict(s)\nissues=validate_state(m.state_dict(),invariants=m.invariants(),resolve_calls=True)\nr=execute(m.graph(),Path({str(run)!r}),knobs=m.values())\nprint(json.dumps({{'exit_ok':r.exit_ok,'error':r.error,'issues':[str(i) for i in issues]}}))"
        observed=_native_child(project,code);output=run/spec["artifact"]
        actual,output_error=read_artifact(output)
        passed=(observed.get("native_boundary",{}).get("status")=="passed" and not observed.get("issues")
                and output_error is None
                and observed.get("exit_ok") is (expected is not None)
                and (matches_output(actual,expected,spec["case_id"]) if expected is not None else not output.exists() and bool(observed.get("error"))))
        rows.append({"case":case_name,"expected":expected,"observed":observed,"output":actual,"output_error":output_error,"output_path":str(output),"passed":passed})
    extra=[];removed=None
    if spec["variant"]=="plugin":
        removed=_native_child(project,common+f"registry.remove(Path({str(consumer)!r}),{name!r})\nprint(json.dumps({{'removed':registry.where({name!r},Path({str(consumer)!r})) is None}}))")
        after_remove=_native_child(project,common+f"found=False\ntry: registry.resolve(Path({str(consumer)!r}),{name+'/run'!r});found=True\nexcept ValueError: pass\nprint(json.dumps({{'still_resolves':found}}))")
        extra.append({"check":"actual install/discovery/remove/fresh resolution refusal","passed":bool(installation and installation.get("discoverable") and installation.get("installed")==[name+"/run"] and removed.get("removed") and after_remove.get("still_resolves") is False),"installation":installation,"removal":removed,"fresh_resolution":after_remove})
    elif spec["variant"]=="update-existing":
        # Run both original and revised unrelated modes; compare actual values.
        for label,owner,mode,artifact,want in [("original",project/"original-project","run","adjusted.json",{"values":[2,-1]}),("unrelated-original",project/"original-project","identity","identity.json",{"values":[1,-2]}),("unrelated-revised",project,"identity","identity.json",{"values":[1,-2]})]:
            source=temporary/(label+".json");source.write_text("[1,-2]");run=temporary/label
            observed=_native_child(project,common+f"_,cls=registry.resolve(Path({str(owner)!r}),{name+'/'+mode!r})\nm=cls();r=execute(m.graph(),Path({str(run)!r}),knobs={{'values_path':{str(source)!r}}})\nprint(json.dumps({{'exit_ok':r.exit_ok,'error':r.error}}))")
            output=run/artifact;actual,output_error=read_artifact(output)
            extra.append({"check":label,"observed":observed,"output":actual,"output_error":output_error,"passed":observed.get("exit_ok") is True and matches_output(actual,want,spec["case_id"]) and output_error is None})
        # V2 adds explicit beyond-default-digit-limit probes separately from
        # the original eight fixtures. Expectations are independently derived
        # decimal tokens, never converted through the authored implementation.
        probes=[("large-positive","1"+"0"*4300,"1"+"0"*4299+"7"),
                ("large-negative","-1"+"0"*4300,"-"+"9"*4299+"3"),
                ("large-carry","9"*4300,"1"+"0"*4299+"6")]
        for label,number,want in probes:
            source=temporary/(label+".json");source.write_text("["+number+"]");run=temporary/label
            observed=_native_child(project,common+f"_,cls=registry.resolve(Path({str(project)!r}),{name+'/run'!r})\nm=cls();r=execute(m.graph(),Path({str(run)!r}),knobs={{'values_path':{str(source)!r}}})\nprint(json.dumps({{'exit_ok':r.exit_ok,'error':r.error}}))")
            output=run/spec["artifact"]
            try: raw=output.read_text();output_error=None
            except (OSError,UnicodeError) as error: raw="";output_error=f"{type(error).__name__}: {error}"
            extra.append({"check":label,"input_decimal":number,"expected_decimal":want,"observed":observed,
                          "output_text":raw,"output_error":output_error,"output_path":str(output),
                          "passed":observed.get("native_boundary",{}).get("status")=="passed" and observed.get("exit_ok") is True
                                   and output_error is None and matches_decimal_output(raw,[want])})
    after=package_inventory(package)
    extra.append({"check":"delivered package unchanged","before":before,"after":after,"passed":bool(before) and before==after})
    native_complete=all(row["observed"].get("native_boundary",{}).get("status")=="passed" for row in rows)
    result={"kind":"independent-native-variant-capability","case_id":spec["case_id"],"total":len(rows),"expected_total":8,"passed":sum(r["passed"] for r in rows),"rows":rows,"extra_checks":extra,
            "evaluator_id":EVALUATOR_ID,"evaluator_version":EVALUATOR_VERSION,"evaluator_source_sha256":digest(Path(__file__)),
            "package_inventory_version":INVENTORY_VERSION,
            "status":"unavailable" if not native_complete else "passed" if len(rows)==8 and all(r["passed"] for r in rows) and all(r["passed"] for r in extra) else "failed",
            "authoring_fidelity":"unavailable here; independently review full Create execution and source role outputs", "case_accepted":False,
            "booking56_acceptance":"not applicable"}
    return result


def check(root: Path) -> dict:
    config_path=root/LANE/"config.json";config=read(config_path);trial=read(root/"trial.json")
    if trial.get("evaluator_version",1)!=EVALUATOR_VERSION:
        raise ValueError("Frozen trial uses an older evaluator; use its pinned source or supplemental-check, never overwrite historical v1 evidence")
    source=Path(config["source"]);adapter=Path(config["adapter_root"])
    checker=adapter/"benchmarks/create_fidelity/variant_cases.py"
    if source_identity(source,config["identity"]["declared_revision"])!=config["identity"]:
        raise ValueError("frozen native source changed before capability verification")
    if hash_tree(adapter)!=config["adapter_sha256"] or digest(checker)!=trial["checker_sha256"]:
        raise ValueError("independent checker or frozen adapter changed")
    project=Path(config["project"])
    protected=[source,adapter,root/"operator",project/"original-project",project/".factory/packages"]
    if config.get("supplemental_operator_root"):
        protected.append(Path(config["supplemental_operator_root"]))
    policy="(version 1)\n(allow default)\n(deny network*)\n"+"\n".join(f"(deny file-write* (subpath {json.dumps(str(p.resolve()))}))" for p in protected)
    env={**os.environ,"PYTHONPATH":os.pathsep.join([str(adapter),str(source/"refactory")]),"PYTHONDONTWRITEBYTECODE":"1"}
    owner=Path(config.get("variant_check_owner",project/".factory"/f"variant-check-supervisor-v{EVALUATOR_VERSION}"));owner.mkdir(parents=True,exist_ok=True)
    output=root/"operator/variant-check-process"
    process=execute_command(["/usr/bin/sandbox-exec","-p",policy,config["python"],"-m","benchmarks.create_fidelity.variant_cases","_check",str(config_path)],cwd=owner,env=env,output=output,timeout=600,grace=15)
    stdout=(output/"stdout.log").read_text();stderr=(output/"stderr.log").read_text()
    try: result=json.loads(stdout.splitlines()[-1])
    except (ValueError,IndexError): result={"status":"unavailable","reason":stderr,"total":0,"case_accepted":False}
    result.update(checker_sha256=digest(checker),process=process,process_stderr=stderr,trial_id=root.name)
    if process.get("status")!="completed" and result.get("status")=="passed":result["status"]="failed"
    if result.get("evaluator_version")!=trial.get("evaluator_version",1):
        result.update(status="failed",evaluator_error="reported evaluator version differs from the frozen trial")
    if digest(project/"BRIEF.md")!=trial["brief_sha256"]:result.update(status="failed",brief_error="original brief changed")
    original_inventory=package_inventory if trial.get("original_seed_inventory_version")==INVENTORY_VERSION else hash_tree
    result["original_seed_inventory_version"]=trial.get("original_seed_inventory_version","legacy-generic-source-inventory")
    if trial["original_seed"] is not None and original_inventory(project/"original-project")!=trial["original_seed"]:result.update(status="failed",source_error="supplied original seed changed")
    if digest(checker)!=trial["checker_sha256"] or source_identity(source,config["identity"]["declared_revision"])!=config["identity"]:
        result.update(status="failed",checker_error="checker or framework source changed during capability verification")
    write(root/"operator/variant-capability.json",result);return result


def supplemental_check(root: Path) -> dict:
    """Freeze and run evaluator v2 separately against an older delivered trial.

    Original config, frozen checker and v1 result remain untouched. Copied
    dependencies and the current evaluator/supervisor are hashed explicitly;
    source/runtime and delivered artifact still come from the original trial.
    """
    original_config=root/LANE/"config.json";config=read(original_config);trial=read(root/"trial.json")
    evaluation=root/"operator/evaluators"/f"native-variant-v{EVALUATOR_VERSION}"
    if evaluation.exists(): raise ValueError("supplemental evaluation already exists; preserve it rather than overwrite")
    runtime=evaluation/"runtime/benchmark"
    shutil.copytree(Path(config["adapter_root"]),runtime)
    updates={}
    for module in ("variant_cases.py","supervision.py","artifact_inventory.py"):
        source=Path(__file__).with_name(module);destination=runtime/"benchmarks/create_fidelity"/module
        updates[module]={"previous_sha256":digest(destination) if destination.is_file() else None,"source":str(source),"source_sha256":digest(source)}
        shutil.copyfile(source,destination)
    config={**config,"adapter_root":str(runtime),"adapter_sha256":hash_tree(runtime),
            "supplemental_operator_root":str(root/"operator"),
            "variant_check_owner":str(Path(config["project"])/".factory"/f"variant-check-supervisor-v{EVALUATOR_VERSION}")}
    write(evaluation/LANE/"config.json",config)
    write(evaluation/"trial.json",{**trial,"evaluator_version":EVALUATOR_VERSION,"evaluator_id":EVALUATOR_ID,
                                  "checker_sha256":digest(runtime/"benchmarks/create_fidelity/variant_cases.py")})
    provenance={"schema_version":1,"kind":"separately-versioned-operator-evaluation","original_trial_id":root.name,
                "evaluator_id":EVALUATOR_ID,"original_config_sha256":digest(original_config),
                "package_inventory_version":INVENTORY_VERSION,
                "original_trial_sha256":digest(root/"trial.json"),"original_evaluator_version":trial.get("evaluator_version",1),
                "original_checker_sha256":trial["checker_sha256"],"updated_modules":updates,
                "frozen_dependency_hashes":hash_tree(runtime),"scope":"Stronger integer-token types, beyond-4300-digit probes and full file inventory; not a retroactive claim that v1 checked these conditions."}
    write(evaluation/"evaluator-provenance.json",provenance)
    result=check(evaluation)
    result.update(trial_id=root.name,supplemental_evaluation_id=EVALUATOR_ID,
                  evaluator_provenance_sha256=digest(evaluation/"evaluator-provenance.json"),
                  original_evaluator_version=trial.get("evaluator_version",1),case_accepted=False)
    write(evaluation/"operator/variant-capability.json",result)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("operation",choices=["prepare","preflight","check","supplemental-check","_check"]);parser.add_argument("root",type=Path);parser.add_argument("--template",type=Path);parser.add_argument("--case");parser.add_argument("--probe",type=Path);args=parser.parse_args()
    if args.operation=="prepare" and (args.template is None or args.case is None):
        parser.error("prepare requires --template and --case")
    if args.operation=="preflight" and args.probe is None:
        parser.error("preflight requires --probe with actual served route receipts")
    if args.operation=="_check":
        from .supervision import LaneSupervisor
        config=read(args.root);project=Path(config["project"])
        owner=Path(config.get("variant_check_owner",project/".factory"/f"variant-check-supervisor-v{EVALUATOR_VERSION}"))
        with LaneSupervisor(owner,600,15):
            result=check_inside(args.root)
    else:
        result=prepare(args.root,args.template,args.case) if args.operation=="prepare" else preflight(args.root,args.probe) if args.operation=="preflight" else supplemental_check(args.root) if args.operation=="supplemental-check" else check(args.root)
    print(json.dumps(result));return 1 if result.get("status") in ("failed","unavailable") else 0


if __name__=="__main__":raise SystemExit(main())
