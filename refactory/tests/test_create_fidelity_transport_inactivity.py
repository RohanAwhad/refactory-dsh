"""Actual local subprocesses exercise source idle/wall clocks without model calls."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from refactory_dsh.model import DshModelClient
from refactory_dsh.create_fidelity_timing import POLICY
from refactory_dsh.workers import begin


def transport(tmp_path, monkeypatch, idle):
    client = DshModelClient(attempts=1).with_inactivity_timeout(idle, policy=POLICY)
    monkeypatch.setattr(client, "_environment", lambda: dict(os.environ))
    client._worker_receipt = begin(tmp_path, client.route(), node="timing-test")
    observations = []
    client = client.with_activity_callback(lambda: observations.append(time.monotonic()))
    return client, observations


def run(client, tmp_path, script, wall):
    return client._run_completion([sys.executable, "-u", "-c", script], tmp_path,
                                  time.monotonic() + wall, wall)


def timing(client):
    return json.loads(client._worker_receipt.read_text())["transport_timing"][-1]


def test_real_active_output_can_outlast_idle_window_without_resetting_wall(tmp_path, monkeypatch):
    client, observations = transport(tmp_path, monkeypatch, .4)
    result = run(client, tmp_path, "import time\nfor i in range(14):\n print(i,flush=True)\n time.sleep(.05)", 3)
    record = timing(client)
    assert result.returncode == 0 and record["end_reason"] == "completed"
    assert record["elapsed_s"] > .4
    assert record["stdout_lines"] == 14 and record["stderr_lines"] == 0
    assert record["activity_observations"] == len(observations) > 1
    assert record["wall_timeout_s"] == 3 and record["inactivity_timeout_s"] == .4
    assert record["policy"] == POLICY


def test_repeated_cumulative_output_does_not_fake_new_activity(tmp_path, monkeypatch):
    client, observations = transport(tmp_path, monkeypatch, .35)
    with pytest.raises(subprocess.TimeoutExpired) as failed:
        run(client, tmp_path, "import time\nprint('once',flush=True)\ntime.sleep(10)", 3)
    record = timing(client)
    assert failed.value.deadline_kind == record["end_reason"] == "idle"
    assert record["stdout_lines"] == 1 and len(observations) == 1
    assert record["idle_elapsed_s"] >= .35 and record["elapsed_s"] < 2


def test_partial_line_trickle_does_not_reset_native_readline_inactivity(tmp_path, monkeypatch):
    client, observations = transport(tmp_path, monkeypatch, .35)
    with pytest.raises(subprocess.TimeoutExpired) as failed:
        run(client, tmp_path, "import sys,time\nfor i in range(200):\n sys.stdout.write('x');sys.stdout.flush();time.sleep(.04)", 3)
    record = timing(client)
    assert failed.value.deadline_kind == record["end_reason"] == "idle"
    assert record["stdout_bytes"] > 0 and record["stdout_lines"] == 0
    assert observations == [] and record["activity_observations"] == 0


def test_continuous_stderr_cannot_extend_absolute_wall(tmp_path, monkeypatch):
    client, observations = transport(tmp_path, monkeypatch, 1)
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired) as failed:
        run(client, tmp_path, "import sys,time\nfor i in range(200):\n print(i,file=sys.stderr,flush=True);time.sleep(.04)", .45)
    record = timing(client)
    assert failed.value.deadline_kind == record["end_reason"] == "wall"
    assert record["stderr_lines"] > 1 and observations
    # The absolute deadline includes launch preparation; the transport receipt
    # starts after spawn and can therefore be a few milliseconds shorter.
    assert time.monotonic() - started >= .45
    assert 0 < record["elapsed_s"] < 2


def test_final_nonempty_eof_tail_is_observed_once(tmp_path, monkeypatch):
    client, observations = transport(tmp_path, monkeypatch, 1)
    result = run(client, tmp_path, "import sys\nsys.stdout.write('done')", 3)
    record = timing(client)
    assert result.stdout == "done" and record["stdout_bytes"] == 4
    assert record["stdout_lines"] == 0 and len(observations) == 1


def test_silent_process_expires_idle_and_owned_process_settles(tmp_path, monkeypatch):
    client, observations = transport(tmp_path, monkeypatch, .25)
    launched = []
    original = subprocess.Popen
    def capture(*args, **kwargs):
        process = original(*args, **kwargs)
        launched.append(process)
        return process
    monkeypatch.setattr(subprocess, "Popen", capture)
    with pytest.raises(subprocess.TimeoutExpired):
        run(client, tmp_path, "import time\ntime.sleep(10)", 3)
    assert observations == [] and timing(client)["end_reason"] == "idle"
    assert launched[0].poll() is not None


def test_source_timing_opt_in_round_trip_does_not_serialize_callbacks():
    original = DshModelClient(attempts=1)
    assert "inactivity_timeout_s" not in original.context()
    selected = original.with_inactivity_timeout(600, policy=POLICY).with_activity_callback(lambda: None)
    restored = DshModelClient(**selected.context())
    assert restored.effective_deadlines()["inactivity_timeout_s"] == 600
    assert restored._activity_callbacks == () and original._activity_callbacks == ()
    assert selected.with_node_timeout(25)._activity_callbacks == selected._activity_callbacks


@pytest.mark.parametrize("value,policy", [(0,POLICY),(-1,POLICY),(True,POLICY),(float('inf'),POLICY),(600,'unknown')])
def test_invalid_or_unknown_inactivity_policy_is_rejected(value, policy):
    with pytest.raises(ValueError):
        DshModelClient(attempts=1).with_inactivity_timeout(value, policy=policy)
