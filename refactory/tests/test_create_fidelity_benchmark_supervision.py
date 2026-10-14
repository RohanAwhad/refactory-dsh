"""Real controller death/elapsed expiry settles benchmark waits and DSH tools."""
from __future__ import annotations
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest


def _assert_process_stopped(pid, message):
    # Query once: a child can be reaped between kill(pid, 0) and ps.
    result = subprocess.run(["/bin/ps", "-o", "stat=", "-p", str(pid)],
                            text=True, capture_output=True, check=False)
    vanished = result.returncode == 1 and not result.stdout.strip() and not result.stderr.strip()
    zombie = result.returncode == 0 and result.stdout.strip().startswith("Z")
    assert vanished or zombie, f"{message}: {result}"


@pytest.mark.parametrize("returncode,stdout,stderr,stopped", [
    (1, "", "", True), (0, "Z\n", "", True), (0, "S\n", "", False),
    (1, "", "ps: denied", False), (2, "", "", False), (0, "", "", False),
])
def test_process_exit_observation_distinguishes_reaping_from_live_or_failed_probe(
        monkeypatch, returncode, stdout, stderr, stopped):
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs:
                        subprocess.CompletedProcess(args[0], returncode, stdout, stderr))
    if stopped:
        _assert_process_stopped(123, "still live")
    else:
        with pytest.raises(AssertionError, match="still live"):
            _assert_process_stopped(123, "still live")


@pytest.mark.skipif(os.name != "posix", reason="POSIX controller pipe/process ownership")
@pytest.mark.parametrize("operation", ["waiting", "model", "native-subprocess"])
@pytest.mark.parametrize("stop", ["controller-loss", "deadline"])
def test_controller_sigkill_or_wall_expiry_unwinds_lane_and_detached_runtime_tools(tmp_path, operation, stop):
    root = tmp_path / "project"
    root.mkdir()
    tool = tmp_path / "tool.py"
    tool.write_text('''import os,signal,time
from pathlib import Path
def stopped(*args): Path('tool-stopped').write_text('settled'); raise SystemExit(0)
signal.signal(signal.SIGTERM,stopped)
Path('tool-ready').write_text(str(os.getpid()))
time.sleep(30)
''')
    runtime = tmp_path / "runtime.py"
    runtime.write_text(f'#!{sys.executable}\n' + '''import json,os,signal,subprocess,sys,time
from pathlib import Path
try: os.fstat(int(os.environ['REFACTORY_BENCHMARK_OWNER_FD'])); exposed=True
except OSError: exposed=False
''' + f"child=subprocess.Popen([sys.executable,{str(tool)!r}],start_new_session=True)\n" + '''
def stop(*args):
    child.terminate(); child.wait(timeout=3)
    Path('runtime-stopped').write_text('settled'); raise SystemExit(0)
signal.signal(signal.SIGTERM,stop)
while not Path('tool-ready').exists(): time.sleep(.01)
Path('runtime-ready').write_text(json.dumps({'pid':os.getpid(),'owner_fd_exposed':exposed}))
deadline=time.monotonic()+20
while time.monotonic()<deadline and not Path('fixture-release').exists(): time.sleep(.02)
stop()
''')
    runtime.chmod(0o700)
    owner = tmp_path / "owner.py"
    owner.write_text('''import json,sys
from pathlib import Path
from benchmarks.create_fidelity.supervision import LaneSupervisor
from benchmarks.create_fidelity import approval
from refactory_dsh.model import DshModelClient
root=Path(sys.argv[1]); operation=sys.argv[2]
try:
    with LaneSupervisor(root,30,4):
        (root/'owner-ready').write_text(str(__import__('os').getpid()))
        if operation=='waiting':
            (root/'strategy.md').write_text('Actual current strategy')
            pending=approval.request(root,'Actual question',inputs=('strategy.md',))
            approval.wait_for_answer(root,pending,timeout_s=30)
        elif operation=='model':
            DshModelClient(dsh_cli=sys.argv[3],provider='controlled',model='controlled',attempts=1).for_node('worker').complete('Controlled external process',workdir=root)
        else:
            import asyncio,os
            async def native_runner():
                child=await asyncio.create_subprocess_exec(sys.executable,'-c',"import os,signal,time; from pathlib import Path; signal.signal(signal.SIGTERM,signal.SIG_IGN); Path('native-child-ready').write_text(str(os.getpid())); time.sleep(30)")
                await child.wait()  # pinned native runner has no cancellation finally
            asyncio.run(native_runner())
except SystemExit as error:
    (root/'owner-unwound.json').write_text(json.dumps({'exit_code':error.code}))
    raise
''')
    controller = tmp_path / "controller.py"
    controller.write_text('''import os,sys
from pathlib import Path
from benchmarks.create_fidelity.harness import execute_command
execute_command([sys.executable,sys.argv[2],sys.argv[1],sys.argv[3],sys.argv[4]],cwd=Path(sys.argv[1]),env=dict(os.environ),
                output=Path(sys.argv[1])/'raw/controller',timeout=float(sys.argv[5]),grace=4)
''')
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1]), "PYTHONDONTWRITEBYTECODE": "1"}
    outer = subprocess.Popen([sys.executable, str(controller), str(root), str(owner), operation, str(runtime),
                              "1.5" if stop == "deadline" else "30"], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        expected = root / ({"model": "runtime-ready", "waiting": "approval-required.json", "native-subprocess": "native-child-ready"}[operation])
        until = time.monotonic() + 5
        while not expected.exists():
            if outer.poll() is not None or time.monotonic() > until:
                pytest.fail("real lane did not reach the waiting/model boundary: " + str(outer.communicate(timeout=2)))
            time.sleep(.01)
        if operation == "model":
            assert json.loads(expected.read_text())["owner_fd_exposed"] is False
        if stop == "controller-loss":
            outer.kill()  # no finally block; closing its sole pipe is the signal
        outer.communicate(timeout=8)
        until = time.monotonic() + 5
        while not (root / "owner-unwound.json").exists() and time.monotonic() < until:
            time.sleep(.02)
        assert json.loads((root / "owner-unwound.json").read_text())["exit_code"] == 143
        lifecycle = json.loads((root / "raw/lifecycle.json").read_text())
        assert lifecycle["reason"] == ("controller_lost" if stop == "controller-loss" else "wall_deadline_expired")
        assert lifecycle["status"] == "stopped"
        if operation == "model":
            assert (root / "runtime-stopped").exists() and (root / "tool-stopped").exists()
            workers = [json.loads(p.read_text()) for p in (root / ".refactory/workers").glob("*.json")]
            assert len(workers) == 1 and workers[0]["state"] == "cancelled"
        elif operation == "native-subprocess":
            pid = int((root / "native-child-ready").read_text())
            _assert_process_stopped(pid, "frozen native runner child remained live after owner unwound")
        else:
            assert not (root / "human-answer.json").exists(), "no decision should be invented to escape waiting"
    finally:
        (root / "fixture-release").write_text("release only this fixture's external I/O")
        if outer.poll() is None:
            outer.kill(); outer.communicate(timeout=3)


@pytest.mark.parametrize("value", [float("inf"), float("nan"), 0, -1])
def test_supervision_rejects_nonfinite_allowances(tmp_path, value):
    from benchmarks.create_fidelity.supervision import LaneSupervisor
    with pytest.raises(ValueError, match="finite"):
        LaneSupervisor(tmp_path, value)
    with pytest.raises(ValueError, match="finite"):
        LaneSupervisor(tmp_path, 30, grace_s=value)


@pytest.mark.parametrize("outcome", ["internal-timeout", "exception", "normal-return"])
def test_every_owned_lane_exit_settles_native_runner_residual_child(tmp_path, outcome):
    from benchmarks.create_fidelity.harness import execute_command
    owner = tmp_path / "internal-owner.py"
    owner.write_text('''import asyncio,sys
from pathlib import Path
from benchmarks.create_fidelity.supervision import LaneSupervisor
async def invoke():
    child=await asyncio.create_subprocess_exec(sys.executable,'-c',"import os,signal,time; from pathlib import Path; signal.signal(signal.SIGTERM,signal.SIG_IGN); Path('child-ready').write_text(str(os.getpid())); time.sleep(30)")
    while not Path('child-ready').exists(): await asyncio.sleep(.01)
    if sys.argv[1]=='normal-return': return
    if sys.argv[1]=='exception': raise RuntimeError('controlled native failure')
    await asyncio.wait_for(child.wait(),timeout=.1)
with LaneSupervisor(Path.cwd(),10,4): asyncio.run(invoke())
''')
    result = execute_command([sys.executable, str(owner), outcome], cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1]), "PYTHONDONTWRITEBYTECODE": "1"},
        output=tmp_path / "raw/controller", timeout=10, grace=4)
    assert result["status"] == ("completed" if outcome == "normal-return" else "failed")
    lifecycle = json.loads((tmp_path / "raw/lifecycle.json").read_text())
    assert lifecycle["status"] == ("settled" if outcome == "normal-return" else "stopped")
    assert lifecycle["reason"] == ({"normal-return": "residual_native_children_at_lane_exit",
        "internal-timeout": "lane_exception: TimeoutError", "exception": "lane_exception: RuntimeError"}[outcome])
    pid = int((tmp_path / "child-ready").read_text())
    cleanup = lifecycle["owned_group_cleanup"]
    assert cleanup["initial_members"] == [pid]
    assert cleanup["trigger"] == ("lane_exit:normal-return" if outcome == "normal-return" else "lane_exit:exception")
    assert cleanup["status"] == "settled" and cleanup["confirmed_empty"] is True
    assert cleanup["remaining_members"] == []
    assert cleanup["completion"] == "residual_children_signalled"
    assert [action["signal"] for action in cleanup["actions"]] == ["SIGTERM", "SIGKILL"]
    assert all(action["outcome"] == "sent after owned-group recheck" for action in cleanup["actions"])
    _assert_process_stopped(pid, "native runner residual child is still live")


@pytest.mark.skipif(sys.platform != "darwin", reason="Exact macOS authoring Seatbelt boundary")
@pytest.mark.parametrize("outcome", ["internal-timeout", "normal-return"])
def test_group_cleanup_under_actual_authoring_read_separation_policy(tmp_path, outcome):
    import shutil
    from benchmarks.create_fidelity import supervision
    from benchmarks.create_fidelity.harness import execute_command
    from benchmarks.create_fidelity.isolation import profile
    lane, peer, operator = (tmp_path / name for name in ("lane", "peer", "operator"))
    for directory in (lane, peer, operator):
        directory.mkdir()
        (directory / "canary.txt").write_text("controlled isolation canary")
    adapter = lane / "runtime/benchmark"
    module = adapter / "benchmarks/create_fidelity"
    module.mkdir(parents=True)
    (adapter / "benchmarks/__init__.py").write_text("")
    (module / "__init__.py").write_text("")
    shutil.copyfile(supervision.__file__, module / "supervision.py")
    owner = lane / "owner.py"
    owner.write_text('''import asyncio,json,subprocess,sys
from pathlib import Path
from benchmarks.create_fidelity.supervision import LaneSupervisor
for name in sys.argv[2:]:
    try: Path(name).read_bytes()
    except PermissionError: pass
    else: raise AssertionError('independent peer/operator evidence was visible')
try: subprocess.run(['/bin/ps','-axo','pid=,pgid=,stat='],check=True,capture_output=True)
except PermissionError: ps_denied=True
else: ps_denied=False
Path('sandbox-control.json').write_text(json.dumps({'peer_operator_denied':True,'setuid_ps_denied':ps_denied}))
async def invoke():
    child=await asyncio.create_subprocess_exec(sys.executable,'-c',"import os,signal,time; from pathlib import Path; signal.signal(signal.SIGTERM,signal.SIG_IGN); Path('child-ready').write_text(str(os.getpid())); time.sleep(30)")
    while not Path('child-ready').exists(): await asyncio.sleep(.01)
    if sys.argv[1]=='normal-return': return
    await asyncio.wait_for(child.wait(),timeout=.1)
with LaneSupervisor(Path.cwd(),10,4): asyncio.run(invoke())
''')
    readonly = [adapter, Path(sys.prefix), Path(sys.base_prefix), Path(sys.executable).resolve().parent.parent]
    policy = tmp_path / "isolation.sb"
    policy.write_text(profile(lane, [peer], readonly, [operator], allowed_reads=readonly))
    result = execute_command(["/usr/bin/sandbox-exec", "-f", str(policy), sys.executable, str(owner), outcome,
                              str(peer / "canary.txt"), str(operator / "canary.txt")], cwd=lane,
        env={**os.environ, "PYTHONPATH": str(adapter), "PYTHONDONTWRITEBYTECODE": "1", "TMPDIR": str(lane)},
        output=lane / "raw/controller", timeout=10, grace=4)
    assert result["status"] == ("completed" if outcome == "normal-return" else "failed"), (result, (lane / "raw/controller/stderr.log").read_text())
    assert json.loads((lane / "sandbox-control.json").read_text()) == {"peer_operator_denied": True, "setuid_ps_denied": True}
    cleanup = json.loads((lane / "raw/lifecycle.json").read_text())["owned_group_cleanup"]
    assert cleanup["membership_probe"].startswith("libproc PROC_PGRP_ONLY")
    assert cleanup["confirmed_empty"] is True and cleanup["status"] == "settled"
    assert cleanup["initial_members"] == [int((lane / "child-ready").read_text())]
    assert [action["signal"] for action in cleanup["actions"]] == ["SIGTERM", "SIGKILL"]
