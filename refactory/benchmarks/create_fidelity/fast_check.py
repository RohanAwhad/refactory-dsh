"""Run the fixed no-model Create development gate before live authoring."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET

TESTS = (
    "test_create_fidelity_contract.py",
    "test_create_fidelity_directors.py",
    "test_create_fidelity_orchestration.py",
    "test_create_fidelity_runtime.py",
    "test_create_fidelity_source_timing.py",
    "test_create_fidelity_director_timing.py",
    "test_create_fidelity_transport_inactivity.py",
)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[3]
    destination = args.output.resolve()
    if destination.exists():
        parser.error("output already exists; preserve earlier evidence and choose a new path")
    destination.parent.mkdir(parents=True, exist_ok=True)
    evidence = Path(tempfile.mkdtemp(prefix="create-fast-", dir=destination.parent))
    junit = evidence / "pytest.xml"
    command = [sys.executable, "-m", "pytest", "-q", *[
        "refactory/tests/" + name for name in TESTS], "--junitxml=" + str(junit),
        "--basetemp=" + str(evidence / "runs")]
    started = time.monotonic()
    timeout = False
    with (evidence / "pytest.log").open("w") as output:
        try:
            completed = subprocess.run(command, cwd=root, stdout=output, stderr=subprocess.STDOUT,
                env={**os.environ, "PYTHONPATH": str(root / "refactory"), "PYTHONDONTWRITEBYTECODE": "1"},
                timeout=120)
            code = completed.returncode
        except subprocess.TimeoutExpired:
            code, timeout = 124, True
    cases = list(ET.parse(junit).getroot().iter("testcase")) if junit.is_file() else []
    failed = [case.attrib["name"] for case in cases if case.find("failure") is not None or case.find("error") is not None]
    skipped = [case.attrib["name"] for case in cases if case.find("skipped") is not None]
    coverage_tests = [case.attrib["name"] for case in cases if case.attrib["name"].startswith(
        "test_full_candidate_human_resume_real_directors_qa_join_gates_and_archives")]
    reference = json.loads((root / "refactory/tests/fixtures/create_fidelity/v1/reference.json").read_text())
    passed = code == 0 and bool(cases) and not failed and not skipped and len(coverage_tests) == 2
    result = {"kind": "create-fidelity-fast-controlled-gate/v1", "status": "passed" if passed else "failed",
              "external_model_calls": 0, "elapsed_s": time.monotonic() - started,
              "hard_timeout_s": 120, "timed_out": timeout, "returncode": code,
              "tests": len(cases), "failed": failed, "skipped": skipped,
              "source_graph_sha256": reference["graph_sha256"],
              "all_stage_coverage_tests": coverage_tests,
              "logical_stages_asserted_by_each_coverage_test": reference["node_count"],
              "evidence": str(evidence), "command": command,
              "scope": "Real orchestration and native package execution with controlled external responses; not live model-quality or release acceptance."}
    destination.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
