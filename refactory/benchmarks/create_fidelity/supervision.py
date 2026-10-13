"""Benchmark lane ownership: controller EOF and wall expiry unwind owned work.

The read-only descriptor is non-inheritable after lane startup. Model/tool
processes never receive either authority descriptor. Messages originate in the
operator controller, outside the authoring lane's filesystem authority.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import select
import signal
import subprocess
import threading
import time

OWNER_FD = "REFACTORY_BENCHMARK_OWNER_FD"
DEADLINE = "REFACTORY_BENCHMARK_DEADLINE_UNIX"
GRACE = "REFACTORY_BENCHMARK_SHUTDOWN_GRACE_S"
_ACTIVE = None


def current():
    return _ACTIVE


class LaneSupervisor:
    def __init__(self, project: Path, timeout_s: float, grace_s: float = 15):
        self.project = Path(project)
        self.deadline = float(os.environ.get(DEADLINE, time.time() + timeout_s))
        self.grace_s = float(os.environ.get(GRACE, grace_s))
        if any(not math.isfinite(value) or value <= 0 for value in (timeout_s, self.grace_s, self.deadline)):
            raise ValueError("Benchmark supervision requires positive finite allowances")
        self.fd = int(os.environ[OWNER_FD]) if OWNER_FD in os.environ else None
        # Harness-created lanes lead their own session. Never signal a caller's
        # process group when used in-process or as a standalone adapter.
        self.group = os.getpid() if self.fd is not None and os.getsid(0) == os.getpid() else None
        self.reason = None
        self.group_cleanup = {"scope": "harness-owned native process group", "group": self.group,
                              "status": "pending", "trigger": None, "initial_members": None,
                              "actions": [], "confirmed_empty": None}
        self._finished = threading.Event()
        self._answers = []
        self._lock = threading.Lock()

    def _record(self, status):
        path = self.project / "raw/lifecycle.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        pending = path.with_suffix(".tmp")
        pending.write_text(json.dumps({"schema_version": 1, "owner_pid": os.getpid(), "controller_pid": os.getppid(),
                                      "deadline_unix": self.deadline, "shutdown_grace_s": self.grace_s,
                                      "status": status, "reason": self.reason, "observed_unix": time.time(),
                                      "owned_native_process_group": self.group,
                                      "owned_group_cleanup": self.group_cleanup,
                                      "authority": "controller-owned pipe" if self.fd is not None else "standalone bounded lane"}) + "\n")
        pending.replace(path)

    def _cancel(self, reason):
        if self.reason is None:
            self.reason = reason
            self._record("cancelling")
            if self.group is not None:
                os.killpg(self.group, signal.SIGTERM)
            else:
                os.kill(os.getpid(), signal.SIGTERM)

    def _group_members(self):
        result = subprocess.run(["/bin/ps", "-axo", "pid=,pgid=,stat="], capture_output=True, text=True,
                                timeout=2, check=True, start_new_session=True)
        return [int(fields[0]) for line in result.stdout.splitlines()
                if len(fields := line.split()) >= 3 and int(fields[1]) == self.group
                and int(fields[0]) != os.getpid() and not fields[2].startswith("Z")]

    def _settle_group(self, trigger):
        """Settle native subprocesses whose frozen runner lacks a finally guard."""
        receipt = self.group_cleanup
        receipt.update(trigger=trigger, started_unix=time.time())
        if self.group is None:
            receipt.update(status="not-owned", confirmation="No dedicated harness group; caller group was not inspected or signalled",
                           finished_unix=time.time())
            return True
        try:
            members = self._group_members()
            receipt["initial_members"] = members
            if not members:
                receipt.update(status="settled", actions=[], confirmed_empty=True, remaining_members=[],
                               completion="no_residual_members_observed", finished_unix=time.time())
                return True
            for sig in (signal.SIGTERM, signal.SIGKILL):
                if members and self.reason is None:
                    self.reason = "residual_native_children_at_lane_exit"
                for pid in members:
                    action = {"pid": pid, "signal": signal.Signals(sig).name, "observed_unix": time.time()}
                    try:
                        if os.getpgid(pid) == self.group:
                            os.kill(pid, sig)
                            action["outcome"] = "sent after owned-group recheck"
                        else:
                            action["outcome"] = "not sent: PID no longer belongs to owned group"
                    except ProcessLookupError:
                        action["outcome"] = "not sent: process already gone"
                    receipt["actions"].append(action)
                deadline = time.monotonic() + min(1, self.grace_s / 4)
                while time.monotonic() < deadline:
                    members = self._group_members()
                    if not members:
                        receipt.update(status="settled", confirmed_empty=True, remaining_members=[],
                                       completion="residual_children_signalled", finished_unix=time.time())
                        return True
                    time.sleep(.025)
            members = self._group_members()
            receipt.update(status="settled" if not members else "unknown", confirmed_empty=not members,
                           remaining_members=members, completion="residual_children_signalled", finished_unix=time.time())
            return not members
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            receipt.update(status="unknown", confirmed_empty=None, error=f"{type(error).__name__}: {error}", finished_unix=time.time())
            return False

    def _watch(self):
        buffered = b""
        cancelled_at = None
        while not self._finished.wait(0.05):
            if self.reason is not None:
                cancelled_at = cancelled_at or time.monotonic()
                if time.monotonic() - cancelled_at > self.grace_s:
                    self._record("unknown: cleanup did not settle within grace")
                    if self.group is not None:
                        os.killpg(self.group, signal.SIGKILL)
                    else:
                        os.kill(os.getpid(), signal.SIGKILL)
                continue
            if time.time() >= self.deadline:
                self._cancel("wall_deadline_expired")
                continue
            if self.fd is None:
                continue
            try:
                readable, _, _ = select.select([self.fd], [], [], 0)
                if not readable:
                    continue
                data = os.read(self.fd, 8192)
                if not data:
                    self._cancel("controller_lost")
                    continue
                buffered += data
                if len(buffered) > 65536:
                    raise ValueError("oversized owner message")
                while b"\n" in buffered:
                    frame, buffered = buffered.split(b"\n", 1)
                    message = json.loads(frame)
                    if message.get("type") == "cancel":
                        self._cancel(message.get("reason") or "controller_cancelled")
                    elif message.get("type") == "operator-decision" and isinstance(message.get("answer"), dict):
                        with self._lock:
                            self._answers.append(message["answer"])
                    else:
                        raise ValueError("invalid owner message")
            except (OSError, ValueError, TypeError) as error:
                self._cancel(f"invalid_owner_channel: {error}")

    def take_operator_answer(self):
        with self._lock:
            return self._answers.pop(0) if self._answers else None

    def __enter__(self):
        global _ACTIVE
        if threading.current_thread() is not threading.main_thread():
            raise ValueError("Lane ownership must be established on its main thread")
        if self.fd is not None:
            os.set_inheritable(self.fd, False)
        self._previous = signal.getsignal(signal.SIGTERM)
        def terminate(signum, _frame):
            self.reason = self.reason or "external_sigterm"
            raise SystemExit(128 + signum)
        signal.signal(signal.SIGTERM, terminate)
        self._prior_active, _ACTIVE = _ACTIVE, self
        self._record("running")
        self._thread = threading.Thread(target=self._watch, name="benchmark-owner-monitor", daemon=True)
        self._thread.start()
        return self

    def __exit__(self, kind, error, traceback):
        global _ACTIVE
        self._finished.set()
        self._thread.join(timeout=1)
        if error is not None and self.reason is None:
            self.reason = "lane_exception: " + type(error).__name__
        settled = self._settle_group("lane_exit:exception" if error is not None else "lane_exit:normal-return")
        if self.fd is not None:
            os.close(self.fd)
        signal.signal(signal.SIGTERM, self._previous)
        _ACTIVE = self._prior_active
        self._record(("stopped" if error else "settled") if settled else "unknown: native process-group cleanup unconfirmed")


def encode_message(message: dict) -> bytes:
    encoded = json.dumps(message, separators=(",", ":")).encode() + b"\n"
    if len(encoded) > 65536:
        raise ValueError("Operator decision exceeds bounded owner-message size")
    return encoded
