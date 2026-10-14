"""Create source idle clocks over real controlled external subprocess output."""
from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from concurrent.futures import CancelledError
from pathlib import Path

import pytest

from refactory_dsh import create_fidelity_directors as directors
from refactory_dsh.budgets import RunBudget
from refactory_dsh.create_fidelity_timing import (
    ActivityClock, POLICY, SOURCE_WALL_S, configure_client, source_idle_timeout,
)
from refactory_dsh.model import DshModelClient
from refactory_dsh.nn.executor import RunContext
from test_create_fidelity_directors import ExternalResponses, run_stage


@pytest.mark.parametrize("node,role,declared,gate,expected", [
    ("research_director", "ceo", None, False, 3600),
    ("tailored-child", "strategist", None, False, 600),
    ("builder", "builder", None, False, 1200),
    ("builder", "builder", 1800, False, 1800),
    ("archivist_plan", "archivist", None, False, 300),
    ("overwatch", "ceo", None, False, 1800),
    ("gate_qa", "ceo", 3600, True, 600),
])
def test_source_idle_defaults_and_explicit_node_precedence(node, role, declared, gate, expected):
    assert source_idle_timeout(node, role, declared, gate) == expected


def test_polling_cannot_reset_clock_or_resurrect_expired_owner():
    clock = ActivityClock(.04, 2)
    while clock.expiry() is None:
        clock.snapshot()
        time.sleep(.005)
    clock.touch()
    assert clock.snapshot()["observation_count"] == 0
    assert clock.expiry() == "idle"
    clock.close()


def _runtime(tmp_path: Path) -> Path:
    path = tmp_path / "controlled_runtime.py"
    path.write_text(f"#!{sys.executable}\n" + """import json,os,signal,sys,time
from pathlib import Path
receipt=Path(os.environ['REFACTORY_WORKER_RECEIPT'])
record=json.loads(receipt.read_text())
record['actual_route']={**record['requested_route'],'source':'controlled-external-process'}
receipt.write_text(json.dumps(record))
mode=record['requested_route']['model']
def stop(*args):
    Path('runtime-settled').write_text('controlled runtime received owned termination')
    raise SystemExit(0)
signal.signal(signal.SIGTERM,stop)
if mode=='silent':
    time.sleep(20)
elif mode=='endless':
    while True:
        print('actual stdout line',flush=True)
        print('actual stderr line',file=sys.stderr,flush=True)
        time.sleep(.04)
else:
    for number in range(24):
        print('actual stdout line '+str(number),flush=True)
        print('actual stderr line '+str(number),file=sys.stderr,flush=True)
        time.sleep(.05)
    print('Final controlled response',flush=True)
""")
    path.chmod(0o700)
    return path


def _owner(tmp_path, monkeypatch, mode, *, wall, idle):
    runtime = _runtime(tmp_path)
    client = DshModelClient(dsh_cli=str(runtime), provider="controlled", model=mode,
                            effort="high", attempts=1)
    # The already configured generic source mapping declares its physical wall,
    # but its source idle must be retained by the director owner.
    client = configure_client(client, idle_timeout_s=idle, wall_timeout_s=wall)
    ctx = RunContext(tmp_path, model=client)
    monkeypatch.setattr(directors, "SOURCE_WALL_S", wall)

    def stage(context, _stage):
        context._director_visit = 1
        selected = context.model.with_identity("controlled-read", "ceo")
        # Isolate the composite's expiry from the independently enforced leaf
        # transport idle timer. Both retain the same owned cancellation event.
        selected = selected.with_inactivity_timeout(5, policy=POLICY)
        selected.complete("Controlled external response only", workdir=context.workdir)

    monkeypatch.setattr(directors, "_direct_stage", stage)
    return ctx, client


def test_actual_output_can_outlast_source_idle_without_extending_wall(tmp_path, monkeypatch):
    ctx, original = _owner(tmp_path, monkeypatch, "active", wall=3, idle=.8)
    directors.research_director(ctx)
    timing = json.loads((tmp_path / ".factory/directors/research_director/timing-v1.json").read_text())
    assert timing["elapsed_s"] > timing["idle_timeout_s"]
    assert timing["end_reason"] == "completed"
    assert timing["observation_count"] >= 6
    assert timing["polling_resets_activity"] is False
    assert ctx.model is original
    assert not any(thread.name == "create-fidelity-idle-wall" for thread in threading.enumerate())


@pytest.mark.parametrize("mode,wall,idle,reason", [
    ("silent", 2, .5, "idle"), ("endless", 1.3, .8, "wall"),
])
def test_original_owner_expiry_cancels_and_settles_real_runtime(tmp_path, monkeypatch, mode, wall, idle, reason):
    ctx, original = _owner(tmp_path, monkeypatch, mode, wall=wall, idle=idle)
    with pytest.raises((CancelledError, subprocess.TimeoutExpired)):
        directors.research_director(ctx)
    timing = json.loads((tmp_path / ".factory/directors/research_director/timing-v1.json").read_text())
    assert timing["end_reason"] == reason
    assert timing["expiry_reason"] == reason
    assert (tmp_path / "runtime-settled").is_file()
    assert ctx.model is original
    assert not any(thread.name == "create-fidelity-idle-wall" for thread in threading.enumerate())
    workers = [json.loads(path.read_text()) for path in (tmp_path / ".refactory/workers").glob("*.json")]
    assert len(workers) == 1 and workers[0]["state"] == "cancelled"


def test_real_director_dispatch_keeps_child_idle_and_one_parent_wall(tmp_path, monkeypatch):
    seen = []
    old = ExternalResponses.complete

    def observe(self, client, *args, **kwargs):
        seen.append((client._node_id, client.effective_deadlines()))
        return old(self, client, *args, **kwargs)

    monkeypatch.setattr(ExternalResponses, "complete", observe)
    result, _manifest, responses = run_stage(tmp_path, monkeypatch)
    assert result.exit_ok, result.error
    assert len(responses.calls) == 5  # planner, three real dispatches, CEO quality
    for node, deadlines in seen:
        assert deadlines["inactivity_timeout_policy"] == POLICY
        assert deadlines["inactivity_timeout_s"] == (600 if ":child-" in node else 3600)
        assert SOURCE_WALL_S - 5 <= deadlines["declared_stage_timeout_s"] <= SOURCE_WALL_S
    timing = json.loads((tmp_path / ".factory/directors/research_director/timing-v1.json").read_text())
    # The controlled _complete response bypasses external transport output;
    # fake results and scheduler progress do not fabricate activity evidence.
    assert timing["observation_count"] == 0
    assert timing["source_wall_timeout_s"] == SOURCE_WALL_S
    assert timing["end_reason"] == "completed"


def test_remaining_actual_run_budget_bounds_director_wall(tmp_path, monkeypatch):
    ctx, _original = _owner(tmp_path, monkeypatch, "endless", wall=4, idle=.8)
    ctx.budget = RunBudget.create(tmp_path, {"max_elapsed_s": 2})
    # Either the transport's same absolute wall or the owning clock can win.
    with pytest.raises((CancelledError, RuntimeError)):
        directors.research_director(ctx)
    timing = json.loads((tmp_path / ".factory/directors/research_director/timing-v1.json").read_text())
    assert 0 < timing["wall_timeout_s"] <= 2
    assert timing["source_wall_timeout_s"] == 4  # accelerated controlled source wall
    assert timing["end_reason"] == "wall"
    assert (tmp_path / "runtime-settled").is_file()
    assert ctx.budget.remaining_s() == 0
