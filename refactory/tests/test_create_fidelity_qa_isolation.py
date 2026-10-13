"""Real mutation/lifetime tests stay on copies; frozen source bytes never move."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

from benchmarks.create_fidelity.artifact_inventory import strict_artifact_inventory
from refactory_dsh import create_fidelity_ops as ops, registry
from refactory_dsh.create_fidelity_prompts import NATIVE_INSTRUCTIONS
from test_create_fidelity_variants import package


def test_repeated_admission_export_and_registration_mutate_only_disposable_copies(tmp_path):
    delivered=tmp_path/"delivered-project";source=package(delivered)
    assert ops.admit_native_package(delivered,"variant_probe")["ok"]
    # Actual authored data in a cache-named directory is also frozen.
    cache=source/"__pycache__/authored.bin";cache.parent.mkdir(exist_ok=True);cache.write_bytes(b"authored cache-named data\x00")
    source_before=strict_artifact_inventory(source)
    def unchanged():assert strict_artifact_inventory(source)==source_before
    for index in range(2):
        copy_project=tmp_path/f"disposable-copy-{index}"
        copied=registry.workspace_root(copy_project)/"variant_probe"
        shutil.copytree(source,copied)
        assert strict_artifact_inventory(copied)==source_before
        unchanged()
        for repetition in range(3):
            old_origin=(copied/".origin.json").read_bytes()
            admitted=ops.admit_native_package(copy_project,"variant_probe")
            assert admitted["ok"],admitted
            # Real repeat admission changes provenance; no idempotence shim.
            assert (copied/".origin.json").read_bytes()!=old_origin
            unchanged()
            skill=ops.export_skill(copy_project,"variant_probe")
            assert skill.is_file() and str(copy_project) in skill.read_text()
            unchanged()
        consumer=tmp_path/f"consumer-{index}"
        registry.add(consumer,copied)
        unchanged()
        installed=registry.workspace_root(consumer)/"variant_probe"
        assert installed!=source and installed!=copied and not installed.is_symlink()
        for repetition in range(2):
            old=(installed/".origin.json").read_bytes()
            assert ops.admit_native_package(consumer,"variant_probe")["ok"]
            assert (installed/".origin.json").read_bytes()!=old
            unchanged()
            result=ops.run_native_mode(consumer,"variant_probe/run",tmp_path/f"invocation-{index}-{repetition}",{"value":f"native-{index}-{repetition}"})
            assert result["exit_ok"],result
            assert json.loads((tmp_path/f"invocation-{index}-{repetition}/value.json").read_text())["value"]==f"native-{index}-{repetition}"
            unchanged()
        registry.remove(consumer,"variant_probe")
        unchanged()
        assert not installed.exists()
        completed=subprocess.run([sys.executable,"-B","-c",
            "from pathlib import Path;from refactory_dsh import registry;import sys\ntry:registry.resolve(Path(sys.argv[1]),'variant_probe/run')\nexcept ValueError:print('removed')\nelse:raise AssertionError('removed package still resolves')",str(consumer)],capture_output=True,text=True,timeout=15)
        assert completed.returncode==0 and completed.stdout.strip()=="removed",completed.stderr
        unchanged()


def test_review_prompt_keeps_shared_package_freeze_and_drift_evidence_explicit():
    for phrase in ("shared delivered package", "read-only", "including SKILL.md, .origin.json and .native-admission.json",
                   "ONLY to", "isolated disposable full copy", "Never repair, restore, re-admit",
                   "transient drift observed by any peer remains a real failure"):
        assert phrase in NATIVE_INSTRUCTIONS


def test_parallel_copy_admissions_leave_frozen_source_stable_for_observer(tmp_path):
    source=package(tmp_path/"builder-project")
    assert ops.admit_native_package(tmp_path/"builder-project","variant_probe")["ok"]
    before=strict_artifact_inventory(source)
    projects=[tmp_path/f"peer-copy-{index}" for index in range(3)]
    for project in projects:shutil.copytree(source,registry.workspace_root(project)/"variant_probe")
    def mutate(project):
        for _ in range(3):assert ops.admit_native_package(project,"variant_probe")["ok"]
    comparisons=0
    with ThreadPoolExecutor(max_workers=3) as executor:
        jobs=[executor.submit(mutate,project) for project in projects]
        while any(not job.done() for job in jobs):
            assert strict_artifact_inventory(source)==before
            comparisons+=1;time.sleep(.002)
        for job in jobs:job.result()
    assert comparisons>0 and strict_artifact_inventory(source)==before
