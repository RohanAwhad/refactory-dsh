"""Real archivist finalization/CLI and concurrent source snapshots; no models."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from refactory_dsh import create_fidelity_archive as archive
from refactory_dsh.create_fidelity import CreateFidelityMode
from refactory_dsh.create_fidelity_prompts import role_prompt


def write(project,name,text):
    path=project/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text);return path


def seed(project):
    write(project,".factory/strategy/current.md","# Exact approved plan\n\nEntire literal plan with declared native input/output and full acceptance matrix.\n")
    write(project,".factory/strategy/user-intent.md","# Original human request\n\nPreserve every ask and original scope; no invented score or experiment.\n")
    write(project,".factory/strategy/observations.md","# Actual source observation\n\nSource graph contains a single concrete native operation.\n")
    write(project,".factory/reviews/qa-synthesized.md","# Actual QA report\n\nNative negative cases failed as expected; acceptance remains independently reviewed.\n")
    write(project,".factory/reviews/overwatch-latest.md","# Actual Overwatch report\n\nThe supplied build has an unresolved precheck failure, not an acceptance PASS.\n")
    write(project,".factory/reviews/precheck.json",json.dumps({"ok":False,"outcome":"halt","problems":["actual missing admission"]}))
    write(project,".refactory/create-fidelity-gates/actual/verdict.json",json.dumps({"gate_id":"gate_qa","owner":"agent","status":"answered","verdict":"reloop","rationale":"actual blocking issue"}))
    for phase in ("plan","build"):
        write(project,f".factory/archive/create-{phase}.md",f"# Full {phase} archive\n\nActual complete note preserves source requirements and unresolved findings.\n\nCommand: python -m refactory_dsh.create_fidelity_ops archive-update {phase} --project PROJECT\nReceipt: .factory/archive/create-{phase}-receipt.json\n")


def cli(project,phase="plan",extra_pythonpath=None):
    paths=[str(Path(archive.__file__).parents[1])]
    if extra_pythonpath:paths.insert(0,str(extra_pythonpath))
    command=[sys.executable,"-B","-m","refactory_dsh.create_fidelity_ops","archive-update",phase,"--project",str(project)]
    return subprocess.run(command,cwd=project,env={**os.environ,"PYTHONPATH":os.pathsep.join(paths),"PYTHONDONTWRITEBYTECODE":"1"},capture_output=True,text=True,timeout=15)


def test_graph_retains_original_nonblocking_archives_and_declares_actual_helper_outputs():
    graph=CreateFidelityMode().graph()
    for phase in ("plan","build"):
        node=graph.nodes["archivist_"+phase]
        assert node.kind=="agent" and node.role=="archivist" and node.blocking is False
        assert role_prompt("archivist") in node.prompt_template
        assert f"archive-update {phase} --project" in node.prompt_template
        assert '"{{artifact_project}}"' in node.prompt_template
        assert "Do not edit the note after finalization" in node.prompt_template
        assert {f".factory/archive/create-{phase}.md",f".factory/archive/create-{phase}-receipt.json",".factory/archive/",".factory/performance_report.json"}<=set(node.writes)
    assert archive.SOURCE["role_sha256"]==hashlib.sha256(role_prompt("archivist").encode()).hexdigest()


def test_actual_cli_regenerates_sourcebound_report_preserves_memory_and_failed_precheck(tmp_path):
    seed(tmp_path)
    memory=b'[ {"type":"anti_pattern", "text":"Actual prior insight", "evidence":[1,2]} ]\n'
    write(tmp_path,".factory/archive/memory.json",memory.decode())
    note=tmp_path/".factory/archive/create-build.md";before=note.read_bytes()
    proc=cli(tmp_path,"build")
    assert proc.returncode==0,(proc.stdout,proc.stderr)
    result=json.loads(proc.stdout)
    assert result["ok"] and result["mempalace"]["attempted"]
    assert result["mempalace"]["status"]=="not_installed"
    assert (tmp_path/".factory/archive/memory.json").read_bytes()==memory
    assert note.read_bytes()==before
    report_path=tmp_path/result["report_regeneration"]["immutable_snapshot"]
    assert hashlib.sha256(report_path.read_bytes()).hexdigest()==result["report_regeneration"]["sha256"]
    report=json.loads(report_path.read_text())
    assert report["observed_precheck"]=={"ok":False,"outcome":"halt","problems":["actual missing admission"]}
    assert report["latest_score"] is None and report["experiment_statistics"]["total_experiments"] is None
    assert report["agent_verdicts"][0]["verdict"]=="reloop"
    assert report["verdict_patterns"]=={"gate_qa:reloop":1}
    assert all(item["body"]==(tmp_path/name).read_text() and item["sha256"]==hashlib.sha256((tmp_path/name).read_bytes()).hexdigest() for name,item in report["inputs"].items())
    assert report["inputs"][".factory/archive/create-build.md"]["sha256"]==hashlib.sha256(before).hexdigest()
    assert result["process"]["executable"]==sys.executable
    assert "archive-update" in result["process"]["argv"]
    assert json.loads((tmp_path/result["immutable_receipt"]).read_text())==result


def test_installed_unknown_optional_api_is_unavailable_without_executing_module(tmp_path):
    seed(tmp_path);optional=tmp_path/"unknown-api";optional.mkdir()
    write(optional,"mempalace/__init__.py","raise AssertionError('optional module must not be executed or use shared home')\n")
    proc=cli(tmp_path,extra_pythonpath=optional)
    assert proc.returncode==1,proc.stderr
    result=json.loads(proc.stdout)
    assert not result["ok"] and result["status"]=="unavailable"
    assert result["mempalace"]["installed"] and not result["mempalace"]["ok"]
    assert result["mempalace"]["shared_home_access"] is False
    assert result["report_regeneration"]["status"]=="completed"
    assert not (tmp_path/".mempalace").exists()


def test_optional_availability_error_is_not_an_uninstalled_success(monkeypatch):
    def fail(name):raise PermissionError("actual availability inspection denied")
    monkeypatch.setattr(archive.importlib.util,"find_spec",fail)
    result=archive.mempalace_attempt()
    assert result["status"]=="unavailable" and not result["ok"]
    assert "PermissionError" in result["error"]


@pytest.mark.parametrize("content",['{}','invalid JSON'])
def test_invalid_memory_preserved_and_failure_attempt_recorded(tmp_path,content):
    seed(tmp_path);memory=write(tmp_path,".factory/archive/memory.json",content)
    result=archive.finalize(tmp_path,"plan")
    assert not result["ok"] and result["status"]=="failed"
    assert memory.read_text()==content
    assert result["mempalace"]["attempted"] is False
    assert not (tmp_path/".factory/performance_report.json").exists()
    assert json.loads((tmp_path/result["immutable_receipt"]).read_text())==result


def test_fifo_input_fails_promptly_in_real_cli_without_blocking(tmp_path):
    seed(tmp_path);note=tmp_path/".factory/archive/create-plan.md";note.unlink();os.mkfifo(note)
    proc=cli(tmp_path)
    assert proc.returncode==1
    result=json.loads(proc.stdout)
    assert "not a regular archive input" in result["error"]
    assert result["mempalace"]["attempted"] is False


def test_symlink_note_never_reads_or_overwrites_external_content(tmp_path):
    project=tmp_path/"project";project.mkdir();seed(project)
    outside=write(tmp_path,"external.md","outside original bytes")
    note=project/".factory/archive/create-plan.md";note.unlink();note.symlink_to(outside)
    proc=cli(project)
    assert proc.returncode==1 and "symlink" in json.loads(proc.stdout)["error"]
    assert outside.read_text()=="outside original bytes"


def test_concurrent_cli_rescans_notes_and_excludes_only_named_generated_outputs(tmp_path):
    seed(tmp_path)
    write(tmp_path,".factory/archive/native-finalization/authored.json",'{"actual":"authored data in helper directory"}')
    command=[sys.executable,"-B","-m","refactory_dsh.create_fidelity_ops","archive-update"]
    env={**os.environ,"PYTHONPATH":str(Path(archive.__file__).parents[1]),"PYTHONDONTWRITEBYTECODE":"1"}
    processes=[subprocess.Popen([*command,phase,"--project",str(tmp_path)],cwd=tmp_path,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True) for phase in ("build","plan")]
    records=[]
    for proc in processes:
        stdout,stderr=proc.communicate(timeout=15);assert proc.returncode==0,(stdout,stderr);records.append(json.loads(stdout))
    for record in records:
        assert record["ok"]
        snapshot=json.loads((tmp_path/record["report_regeneration"]["immutable_snapshot"]).read_text())
        assert {".factory/archive/create-plan.md",".factory/archive/create-build.md",".factory/archive/native-finalization/authored.json"}<=set(snapshot["inputs"])
        assert not any(name.endswith("-receipt.json") or name==archive.INDEX or name.endswith("/performance_report.json") for name in snapshot["inputs"])
    # A late plan update must include already available build observations too.
    completed=cli(tmp_path,"plan");assert completed.returncode==0,completed.stderr
    latest=json.loads((tmp_path/".factory/performance_report.json").read_text())
    assert latest["phase"]=="plan" and latest["observed_precheck"]["ok"] is False
    assert any(row["source"]==".factory/archive/create-build.md" for row in latest["observations"])
    assert json.loads((tmp_path/".factory/archive/memory.json").read_text())==[]
    assert len(list((tmp_path/".factory/archive/native-finalization").glob("*/receipt.json")))==3


def test_output_index_cannot_hide_arbitrary_authored_records(tmp_path):
    seed(tmp_path)
    write(tmp_path,archive.INDEX,json.dumps({"version":archive.VERSION,"outputs":[".factory/archive/authored.json"]}))
    result=cli(tmp_path)
    assert result.returncode==1 and "cannot omit arbitrary" in result.stdout


def test_lock_wait_is_bounded_and_existing_report_survives(tmp_path):
    seed(tmp_path);lock=write(tmp_path,".factory/archive/.native-archive.lock","")
    old=write(tmp_path,".factory/performance_report.json",'{"previous":"actual preserved report"}')
    with lock.open("r+") as handle:
        fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with pytest.raises(TimeoutError,match="serialization deadline"):
            archive.finalize(tmp_path,"plan",lock_timeout_s=.02)
    assert old.read_text()=='{"previous":"actual preserved report"}'
