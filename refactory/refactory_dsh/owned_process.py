"""Settle an owned CLI through its shutdown lifecycle before returning failure."""
from __future__ import annotations

import os
import signal
import subprocess
import threading
from contextlib import contextmanager
from typing import Any, Iterator

# DSH's own root-disposal deadline is five seconds.
SHUTDOWN_GRACE_S = 10


@contextmanager
def _termination_as_exit() -> Iterator[None]:
    """Let a CLI's default SIGTERM path unwind its owned subprocess first."""
    main = threading.current_thread() is threading.main_thread()
    previous = signal.getsignal(signal.SIGTERM) if main else None
    changed = main and previous == signal.SIG_DFL

    def terminate(signum: int, _frame: Any) -> None:
        raise SystemExit(128 + signum)

    if changed:
        signal.signal(signal.SIGTERM, terminate)
    try:
        yield
    finally:
        if changed:
            signal.signal(signal.SIGTERM, previous)


def shutdown(proc: subprocess.Popen, *, label: str = 'model runtime') -> None:
    """Request runtime-owned disposal; a forced exit reports unconfirmed cleanup.

    Only this direct process is signalled. The selected runtime owns its managed
    tool processes. No PID discovery or unrelated process groups are involved.
    """
    try:
        proc.terminate()
    except ProcessLookupError:
        pass
    try:
        proc.communicate(timeout=SHUTDOWN_GRACE_S)
    except subprocess.TimeoutExpired as exc:
        proc.kill()
        # Escaped descendants may retain pipes; never wait on them indefinitely.
        try:
            proc.communicate(timeout=1)
        except subprocess.TimeoutExpired:
            if proc.stdout is not None:
                proc.stdout.close()
            if proc.stderr is not None:
                proc.stderr.close()
            proc.wait(timeout=1)
        raise RuntimeError(
            f'{label} did not finish shutdown within {SHUTDOWN_GRACE_S}s; '
            'forced exit; child cleanup unconfirmed; not retried'
        ) from exc


#: Grace period before ending a process whose stop condition holds.
#: Long enough for the coordinator's final report to be written.
STOP_CONDITION_GRACE_S = 60


def run_owned(argv: list[str], *, timeout: float | None = None,
              label: str = 'DSH runtime',
              stop_condition: Any | None = None,
              **kwargs: Any) -> subprocess.CompletedProcess:
    """Run one owned CLI, preserving inherited terminal I/O and failure truth.

    POSIX isolates its signals so a terminal interrupt reaches this owner once;
    the owner forwards a single shutdown request and awaits disposal. CLI calls
    on the main thread also unwind default SIGTERM. Existing custom/ignored
    handlers and worker-thread signal policy remain the caller's responsibility.

    When *stop_condition* is given (a callable returning bool), it is polled
    after each wait interval. When it returns True, the process's stdin is
    closed (sending EOF to the coordinator), a grace period is waited, and if
    the process has not exited, it is terminated the same way a timeout ends it.
    """
    import time as _time
    with _termination_as_exit():
        proc = subprocess.Popen(argv, start_new_session=os.name == 'posix', **kwargs)
        try:
            if stop_condition is not None:
                stdout, stderr = _communicate_with_stop(
                    proc, timeout=timeout, stop_condition=stop_condition, label=label)
            else:
                stdout, stderr = proc.communicate(timeout=timeout)
        except BaseException:
            shutdown(proc, label=label)
            raise
    return subprocess.CompletedProcess(argv, proc.returncode, stdout, stderr)


def _communicate_with_stop(proc: subprocess.Popen, *, timeout: float | None,
                           stop_condition: Any, label: str) -> tuple[Any, Any]:
    """Poll stop_condition while waiting for the process; end it on terminal status."""
    import time as _time

    deadline = _time.monotonic() + timeout if timeout else None
    poll_interval = 1.0
    while proc.poll() is None:
        if deadline is not None and _time.monotonic() >= deadline:
            raise subprocess.TimeoutExpired(proc.args, timeout)
        # Check stop condition
        try:
            should_stop = stop_condition()
        except Exception:
            should_stop = False
        if should_stop:
            # Close stdin to signal EOF to the coordinator
            if proc.stdin is not None:
                try:
                    proc.stdin.close()
                except OSError:
                    pass
            # Wait a grace period for the coordinator's final report
            try:
                proc.wait(timeout=STOP_CONDITION_GRACE_S)
            except subprocess.TimeoutExpired:
                # Grace period expired; terminate the process group
                shutdown(proc, label=label)
            break
        try:
            proc.wait(timeout=min(poll_interval, (deadline - _time.monotonic()) if deadline else poll_interval))
        except subprocess.TimeoutExpired:
            continue
    return None, None
