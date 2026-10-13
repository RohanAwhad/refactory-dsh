"""Explicit local mapping of the pinned archivist's report/memory duties.

The archivist authors the markdown. This helper records actual source bytes,
regenerates the performance report and attempts optional integration; it never
authors a missing note, approves a build, invents scores or writes shared home.
"""
from __future__ import annotations

import fcntl
import hashlib
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import time
from datetime import datetime, timezone
from uuid import uuid4

VERSION = "create-fidelity-native-archive/v1"
SOURCE = {
    "commit": "3ab6f8f7ca792c874f2654574d7f7fd6dd18b896",
    "role_sha256": "316dc3a44771751622c8c4d17e48615d25f988a062f928d9098a074821cde9ab",
    "factory/report.py": "4da0eca8dea0c6069545f9149809b6cbda77568a72dfec9914fd4e9833528a49",
    "factory/mempalace/helpers.py": "d6756528bddbd15d539b49b541ce6138e43a39e3f9cd7ef1930453aac0875c8a",
    "factory/mempalace/writer.py": "33632ecf4d11cd11f2c9d64aa3208c86662f10f643d2b59ce9434e1019abdb62",
}
REQUIRED = {
    "plan": (".factory/strategy/current.md", ".factory/strategy/user-intent.md"),
    "build": (".factory/reviews/qa-synthesized.md", ".factory/reviews/precheck.json", ".factory/reviews/overwatch-latest.md"),
}
INDEX=".factory/archive/native-finalization/index.json"


def _generated(project: Path) -> set[str]:
    path=_path(project,INDEX)
    if not path.exists():return {INDEX}
    record=json.loads(_read(project,INDEX))
    if type(record) is not dict or record.get("version")!=VERSION or type(record.get("outputs")) is not list or any(type(name) is not str for name in record["outputs"]):
        raise ValueError("invalid native archive output index")
    names=set(record["outputs"])
    for name in names:
        if not re.fullmatch(r"\.factory/archive/(create-(plan|build)-receipt\.json|native-finalization/(plan|build)-[0-9a-f]{32}/(receipt|performance_report)\.json)",name):
            raise ValueError("archive output index cannot omit arbitrary input files")
    return names|{INDEX}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _path(project: Path, relative: str) -> Path:
    path=project/relative
    for component in (path,*path.parents):
        if component==project.parent:break
        if component.is_symlink():raise ValueError(f"archive path contains a symlink: {relative}")
    if not path.is_relative_to(project):raise ValueError("archive path escapes project")
    return path


def _read(project: Path, relative: str) -> bytes:
    path=_path(project,relative)
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd,"rb") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):raise ValueError(f"not a regular archive input: {relative}")
        return handle.read()


def _atomic(project: Path, relative: str, data: bytes) -> None:
    path=_path(project,relative);path.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent,prefix=".archive-",delete=False) as handle:
        temporary=Path(handle.name)
        try:
            handle.write(data);handle.flush();os.fsync(handle.fileno())
            os.replace(temporary,path)
        finally:
            temporary.unlink(missing_ok=True)


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value,indent=2,ensure_ascii=False)+"\n").encode()


def mempalace_attempt() -> dict:
    """Inspect actual availability without importing unknown installed code.

    Pinned mp_write returns a no-op when its optional module is absent. Its
    installed branch uses global KG/playbook/episodic state. Although reviewed
    3.6.0 KG accepts db_path, full scoped episodic integration is not proven;
    no installed branch may be falsely called successful or touch shared home.
    """
    result={"attempted":True,"at":datetime.now(timezone.utc).isoformat(),
            "operation":"native equivalent of factory mempalace write",
            "method":"importlib.util.find_spec('mempalace'); no optional module execution",
            "shared_home_access":False,"source":SOURCE}
    try:
        spec=importlib.util.find_spec("mempalace")
        if spec is None:
            return {**result,"status":"not_installed","ok":True,"installed":False,
                    "reason":"Verified optional module absent; pinned mp_write has an explicit not-installed no-op."}
        try:version=importlib.metadata.version("mempalace")
        except importlib.metadata.PackageNotFoundError:version=None
        return {**result,"status":"unavailable","ok":False,"installed":True,
                "module_origin":spec.origin,"installed_version":version,
                "reason":"Installed integration is unsupported: complete project-owned KG plus episodic storage API is not verified. Pinned shared-home defaults are prohibited; no backend write was attempted."}
    except Exception as error:
        return {**result,"status":"unavailable","ok":False,"error":f"{type(error).__name__}: {error}"}


def _snapshot(project: Path, phase: str) -> dict:
    required=(*REQUIRED[phase],f".factory/archive/create-{phase}.md")
    names=set(required)
    for pattern in (".factory/strategy/observations.md",".factory/strategy/current.md",
                    ".factory/strategy/user-intent.md",".factory/reviews/*.md",
                    ".factory/reviews/precheck.json",".factory/qa_director-findings.json",
                    ".factory/archive/**/*.md",".factory/archive/**/*.json",
                    ".refactory/create-fidelity-gates/*/verdict.json"):
        names.update(str(path.relative_to(project)) for path in project.glob(pattern))
    names-=_generated(project)
    inputs={};problems=[]
    for name in sorted(names):
        data=_read(project,name);text=data.decode()
        item={"sha256":_sha(data),"bytes":len(data),"body":text}
        if name.endswith(".json"):
            try:item["record"]=json.loads(text)
            except ValueError as error:
                item["record_status"]="unavailable";item["error"]=str(error)
                problems.append(f"invalid structured source: {name}")
        inputs[name]=item
    note=inputs[f".factory/archive/create-{phase}.md"]
    if len(note["body"].strip())<50:raise ValueError("archivist must write its substantive markdown before archive-update")
    if phase=="build" and inputs[".factory/reviews/precheck.json"].get("record_status")=="unavailable":
        raise ValueError("build archive requires valid actual precheck JSON")
    if phase=="build":
        precheck=inputs[".factory/reviews/precheck.json"].get("record")
        if type(precheck) is not dict or type(precheck.get("ok")) is not bool or precheck.get("outcome")!=("proceed" if precheck["ok"] else "halt"):
            raise ValueError("build archive precheck must preserve actual native ok/outcome fields")
    return {"inputs":inputs,"snapshot_problems":problems}


def finalize(project: Path, phase: str, *, lock_timeout_s: float = 30) -> dict:
    if phase not in REQUIRED:raise ValueError("archive phase must be plan or build")
    project=project.resolve();archive=_path(project,".factory/archive");archive.mkdir(parents=True,exist_ok=True)
    lock=_path(project,".factory/archive/.native-archive.lock")
    fd=os.open(lock,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,"r+") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):raise ValueError("archive lock is not a regular file")
        stop=time.monotonic()+lock_timeout_s
        while True:
            try:fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB);break
            except BlockingIOError:
                if time.monotonic()>=stop:raise TimeoutError("native archive serialization deadline expired")
                time.sleep(min(0.02,max(0,stop-time.monotonic())))
        try:return _finalize_locked(project,phase)
        finally:fcntl.flock(handle,fcntl.LOCK_UN)


def _finalize_locked(project: Path, phase: str) -> dict:
    ident=uuid4().hex;directory=f".factory/archive/native-finalization/{phase}-{ident}"
    record={"schema_version":1,"version":VERSION,"source":SOURCE,"invocation_id":ident,
            "project":str(project),"stage_id":f"archivist_{phase}","phase":phase,
            "started":datetime.now(timezone.utc).isoformat(),
            "process":{"executable":sys.executable,"argv":sys.argv,"pid":os.getpid()},
            "mapped_operation":"python -m refactory_dsh.create_fidelity_ops archive-update "+phase+" --project <artifact_project>",
            "note":f".factory/archive/create-{phase}.md","immutable_receipt":directory+"/receipt.json",
            "report_regeneration":{"status":"not_attempted"},
            "mempalace":{"status":"not_attempted","attempted":False},"ok":False}
    try:
        snapshot=_snapshot(project,phase)
        memory_path=".factory/archive/memory.json";memory=_path(project,memory_path)
        before=_read(project,memory_path) if memory.exists() else None
        if before is not None and type(json.loads(before)) is not list:
            raise ValueError("existing CEO memory must be a valid JSON array; it was not overwritten")
        if before is None:_atomic(project,memory_path,b"[]\n")
        record["memory"]={"path":memory_path,"created":before is None,"sha256":_sha(_read(project,memory_path)),
                          "preserved_existing_bytes":before is not None,"entries_added":0,
                          "reason":"This one-plan Create helper has no independently established multi-experiment evidence; existing valid memory is preserved, no insights or experiment IDs invented."}
        report={"schema_version":1,"version":VERSION,"source":SOURCE,"generated_at":datetime.now(timezone.utc).isoformat(),
                "project_name":project.name,"phase":phase,"invocation_id":ident,
                "scope":"Native report-update equivalent: observed archive, QA, gate and strategy source bytes; archival establishes no acceptance verdict.",
                "latest_score":None,"experiment_statistics":{"status":"not_established_by_native_Create","total_experiments":None},
                **snapshot,"memory":record["memory"]}
        verdicts=[];observations=[];patterns={}
        for name,item in snapshot["inputs"].items():
            if name.startswith(".refactory/create-fidelity-gates/"):
                raw=item.get("record")
                if type(raw) is dict:
                    verdict={key:raw.get(key) for key in ("gate_id","owner","status","verdict","rationale","input_digest","evidence_digest")}
                    verdict.update(source=name,source_sha256=item["sha256"]);verdicts.append(verdict)
                    if raw.get("verdict"):
                        key=str(raw.get("gate_id"))+":"+str(raw["verdict"]);patterns[key]=patterns.get(key,0)+1
            elif name.endswith(".md"):
                observations.append({"source":name,"source_sha256":item["sha256"],"text":item["body"]})
        report.update(agent_verdicts=verdicts,verdict_patterns=patterns,observations=observations,
                      observation_scope="Recorded source observations; no freshness certification or archival approval is inferred.")
        precheck=snapshot["inputs"].get(".factory/reviews/precheck.json",{}).get("record")
        report["observed_precheck"]=precheck if type(precheck) is dict else None
        data=_json_bytes(report);snapshot_path=directory+"/performance_report.json"
        _atomic(project,snapshot_path,data);_atomic(project,".factory/performance_report.json",data)
        record["report_regeneration"]={"status":"completed","path":".factory/performance_report.json",
                                       "sha256":_sha(data),"immutable_snapshot":snapshot_path,
                                       "inputs":{name:item["sha256"] for name,item in snapshot["inputs"].items()}}
        record["note_sha256"]=snapshot["inputs"][record["note"]]["sha256"]
        # Report first, optional archive second: this ordering is the source duty.
        record["mempalace"]=mempalace_attempt()
        record["ok"]=record["mempalace"]["ok"] and not snapshot["snapshot_problems"]
        record["status"]="completed" if record["ok"] else "unavailable"
    except (ValueError,OSError,UnicodeError) as error:
        record.update(status="failed",error=f"{type(error).__name__}: {error}")
    record["finished"]=datetime.now(timezone.utc).isoformat()
    data=_json_bytes(record)
    _atomic(project,record["immutable_receipt"],data)
    _atomic(project,f".factory/archive/create-{phase}-receipt.json",data)
    generated=_generated(project)-{INDEX}
    generated.update(name for name in (record["immutable_receipt"],f".factory/archive/create-{phase}-receipt.json",directory+"/performance_report.json") if _path(project,name).is_file())
    _atomic(project,INDEX,_json_bytes({"version":VERSION,"outputs":sorted(generated)}))
    return record
