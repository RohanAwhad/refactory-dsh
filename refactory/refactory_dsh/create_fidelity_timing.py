"""Pinned Create invocation inactivity and hard-wall timing semantics.

These are the ordinary source agent runner's limits, not new model capacity
estimates. Activity is supplied only by actual transport output observations.
"""
from __future__ import annotations

import math
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable

POLICY = "create-fidelity-native-idle-wall/v1"
SOURCE_WALL_S = 14400
DEFAULT_IDLE_S = 600
SOURCE_POOL_IDLE_S = {"ceo": 3600, "researcher": 600, "strategist": 600,
                      "builder": 1200, "archivist": 300,
                      "health_checker": 600, "code_reviewer": 900}


def source_idle_timeout(node_id: str, role: str, declared: float | None = None,
                        is_gate: bool = False) -> float:
    """Source explicit agent limits precede pool defaults; gates use CLI default."""
    if is_gate:
        return DEFAULT_IDLE_S
    if declared is not None:
        if isinstance(declared, bool) or not math.isfinite(declared) or declared <= 0:
            raise ValueError("source inactivity allowance must be finite and positive")
        return declared
    if node_id == "overwatch":
        return 1800
    return SOURCE_POOL_IDLE_S.get(role, DEFAULT_IDLE_S)


def configure_client(client: Any, idle_timeout_s: float,
                     wall_timeout_s: float = SOURCE_WALL_S,
                     activity_callback: Callable[[], None] | None = None) -> Any:
    """Copy supported client controls without resetting any enclosing clock."""
    if not all(math.isfinite(value) and value > 0
               for value in (idle_timeout_s, wall_timeout_s)):
        raise ValueError("Create idle and wall limits must be finite and positive")
    if callable(getattr(client, "with_node_timeout", None)):
        client = client.with_node_timeout(max(1, math.ceil(wall_timeout_s)))
    if callable(getattr(client, "with_inactivity_timeout", None)):
        client = client.with_inactivity_timeout(idle_timeout_s, policy=POLICY)
    if activity_callback is not None and callable(getattr(client, "with_activity_callback", None)):
        client = client.with_activity_callback(activity_callback)
    return client


class ActivityClock:
    """One composite owner clock; output activity cannot extend its hard wall."""

    def __init__(self, idle_timeout_s: float, wall_timeout_s: float,
                 on_expire: Callable[[], None] | None = None) -> None:
        if not all(math.isfinite(value) and value > 0
                   for value in (idle_timeout_s, wall_timeout_s)):
            raise ValueError("Create idle and wall limits must be finite and positive")
        self.idle_timeout_s = idle_timeout_s
        self.wall_timeout_s = wall_timeout_s
        self.started = time.monotonic()
        self.deadline = self.started + wall_timeout_s
        self._last_activity = self.started
        self._started_at = datetime.now(timezone.utc).isoformat()
        self._last_activity_at: str | None = None
        self._observations = 0
        self._reason: str | None = None
        self._on_expire = on_expire
        self._lock = threading.Lock()
        self._closed = threading.Event()
        self._thread: threading.Thread | None = None

    def _expiry_locked(self, now: float) -> str | None:
        if self._reason is None:
            if now >= self.deadline:
                self._reason = "wall"
            elif now >= self._last_activity + self.idle_timeout_s:
                self._reason = "idle"
        return self._reason

    def touch(self) -> None:
        """Called only for newly observed complete output lines or EOF tails."""
        with self._lock:
            if self._closed.is_set() or self._expiry_locked(time.monotonic()) is not None:
                return
            self._last_activity = time.monotonic()
            self._last_activity_at = datetime.now(timezone.utc).isoformat()
            self._observations += 1

    def expiry(self) -> str | None:
        with self._lock:
            return self._expiry_locked(time.monotonic())

    def remaining_wall_s(self) -> float:
        return max(0.0, self.deadline - time.monotonic())

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            now = time.monotonic()
            return {"policy": POLICY, "idle_timeout_s": self.idle_timeout_s,
                    "wall_timeout_s": self.wall_timeout_s,
                    "started_at": self._started_at, "last_activity_at": self._last_activity_at,
                    "elapsed_s": now - self.started,
                    "wall_remaining_s": max(0.0, self.deadline - now),
                    "idle_remaining_s": max(0.0, self._last_activity + self.idle_timeout_s - now),
                    "observation_count": self._observations,
                    "expiry_reason": self._expiry_locked(now),
                    "activity_source": "actual transport stdout/stderr complete lines or nonempty EOF tail",
                    "polling_resets_activity": False}

    def start(self) -> None:
        if self._thread is not None or self._closed.is_set():
            raise RuntimeError("Create activity clock can only be started once")

        def watch() -> None:
            while not self._closed.is_set():
                with self._lock:
                    now = time.monotonic()
                    expired = self._expiry_locked(now)
                    remaining = min(self.deadline, self._last_activity + self.idle_timeout_s) - now
                if expired is not None:
                    if self._on_expire is not None:
                        self._on_expire()
                    return
                self._closed.wait(min(0.1, max(0.001, remaining)))

        self._thread = threading.Thread(target=watch, name="create-fidelity-idle-wall", daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._closed.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join()
