"""A :class:`ModelClient` backed by a one-shot DSH run.

The profile's own model selection (`--provider`, `--model`) is per invocation, so a
caller that names one gets that model for the completions it makes and nothing else
changes.

The outer loop's optimizers need exactly one thing from a model: text in, text
out. DSH already exposes that as ``dsh --profile headless "<task>"``, which is
the same mechanism the inner loop uses to run a mode, so nothing new is
introduced to reach a model and the credentials come from the same place.

The subprocess runs in a scratch directory: the headless profile is a full agent
with tools, and an analysis prompt must not be able to write into the run.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

from refactory_dsh.paths import SOURCE_CLI_ENTRY, dsh_command, dsh_root, node_bin

#: The overlay that keeps a node's completion from backgrounding its own work. Checked
#: in rather than generated, so what a node's model call is configured with is
#: reviewable in the repository.
NODE_PATCH = Path(__file__).resolve().parent / "patches" / "agent-node.yml"

#: Wall-clock ceiling for one completion, before the subprocess is called hung.
#:
#: It is a hang detector rather than a budget: the authoring modes' agent nodes are
#: tool-using tasks that write a package, run it and write up what happened, and one
#: of them (`qa_director`, which builds and runs an adversarial probe) exceeded a
#: five-minute ceiling in a live create run. A caller that knows its own nodes can
#: set `timeout_s`; the default has to cover the slowest node a shipped mode declares.
DEFAULT_TIMEOUT_S = 1800


#: How many times one completion may be attempted before it is called failed.
#:
#: A transient transport failure is not the node's fault: it is the network, the
#: provider, or a rate limit. One blip used to discard an entire authoring run — the
#: issue that added this retried nothing and lost thirty-five minutes of finished
#: work — so the client retries, and only a client that keeps failing fails the node.
DEFAULT_ATTEMPTS = 3


#: Allow the CLI's five-second root-disposal deadline to settle before escalation.
#: SIGTERM invokes DSH's managed subprocess cleanup; SIGKILL cannot run it.
SHUTDOWN_GRACE_S = 10

#: What the child says when the failure was on the wire rather than in the model.
#:
#: `dsh` wraps provider transport errors with `TRANSPORT:`, and the socket-level
#: spellings below are what the same failure looks like when it happens lower down.
#: Deliberately narrow: a run is retried for a *transport* failure, and anything a
#: model or a key or a prompt caused comes back unchanged, because retrying those
#: wastes the next three attempts and hides the real error.
TRANSPORT_MARKERS = (
    "transport:",
    "econnreset",
    "econnrefused",
    "etimedout",
    "enotfound",
    "eai_again",
    "socket hang up",
    "fetch failed",
    "network error",
    "connection reset",
    "rate limit",
    "429",
    "500 internal",
    "502 bad gateway",
    "503 service unavailable",
    "504 gateway",
)


def is_transport_failure(text: str) -> bool:
    """Whether a failed completion was a transport failure rather than a bad answer."""
    lowered = (text or "").lower()
    return any(marker in lowered for marker in TRANSPORT_MARKERS)


def failure_cause(stderr: str) -> str:
    """The line that says *why* a completion failed, not the model's last paragraph.

    The child's stderr is mostly the model's own reasoning. Prefer the explicit
    diagnostic `dsh` writes. Reporting the tail of the prose made a transport
    failure read like a code-review defect ("Confirmed non-blocking (design fault).
    Let me write the review now."), which sends a reader looking in the wrong place.

    A runtime bootstrap crash may happen before DSH can report anything. Node ends
    those stacks with its version banner; retain the error header instead of that
    banner or a stack frame. Do not expose the full reasoning/stack transcript.
    """
    lines = [line.strip() for line in (stderr or "").splitlines() if line.strip()]
    for line in reversed(lines):
        if line.startswith("dsh:") or is_transport_failure(line):
            return line
    for line in reversed(lines):
        if re.match(r"^(?:[A-Za-z_$][\w$]*Error|Error)(?: \[[A-Z0-9_]+\])?:\s*\S", line):
            return line
    return lines[-1] if lines else ""


class DshModelClient:
    """One-shot completions through ``dsh --profile headless``.

    Implements ``factory.outer_loop.optimizers.ModelClient``. A completion that fails
    on the wire is retried, bounded, with a backoff; one that fails for any other
    reason is raised straight away with the cause named.
    """

    def __init__(
        self,
        timeout_s: int = DEFAULT_TIMEOUT_S,
        model: str = "",
        provider: str = "",
        attempts: int = DEFAULT_ATTEMPTS,
        backoff_s: float = 1.0,
        dsh_home: str | Path | None = None,
        runtime_root: str | Path | None = None,
        dsh_cli: str | None = None,
    ) -> None:
        self._timeout_s = timeout_s
        self._runtime_root = Path(runtime_root or dsh_root()).expanduser().resolve()
        self._dsh_cli = (dsh_cli if dsh_cli is not None else os.environ.get("DSH_CLI", "")).strip()
        self._model = model or os.environ.get("REFACTORY_MODEL_ID", "")
        self._provider = provider or os.environ.get("REFACTORY_MODEL_PROVIDER", "")
        # Resolve before containment rewrites HOME. This is a location, never a key.
        selected_home = dsh_home or os.environ.get("REFACTORY_MODEL_DSH_HOME") or os.environ.get("DSH_HOME", "").strip()
        self._dsh_home = Path(selected_home or Path.home() / ".dsh").expanduser().resolve()
        self._attempts = max(1, int(attempts))
        self._backoff_s = max(0.0, float(backoff_s))

    def context(self) -> dict[str, str]:
        """Nonsecret settings safe to carry across the model subprocess boundary."""
        return {
            "dsh_home": str(self._dsh_home), "provider": self._provider, "model": self._model,
            "runtime_root": str(self._runtime_root), "dsh_cli": self._dsh_cli,
        }

    def _environment(self) -> dict[str, str]:
        # Never resolve a credential here or copy it into the environment. The DSH
        # credentials service reads its managed store in the selected home.
        return {**os.environ, "DSH_HOME": str(self._dsh_home)}

    def _readiness_unavailable(self, reason: str) -> dict[str, object]:
        return {
            "ready": None, "status": "diagnostic-unavailable", "reason": reason,
            "provider": self._provider or "(runtime default)",
            "model": self._model or "(runtime default)", "authenticated": False,
            "warning": "Credential diagnostic unavailable for the selected runtime/configuration; "
                       "credential readiness is unverified. Normal execution will perform its own checks.",
        }

    def readiness(self, *, workdir: Path, require_ready: bool = True) -> dict[str, object]:
        """Describe local configuration; report known failures only when require_ready is false."""
        # Inspect only the runtime this client will actually launch. A custom CLI
        # or source checkout need not use the repository's installed composition.
        if self._dsh_cli:
            return self._readiness_unavailable("external-launcher")
        if (self._runtime_root / SOURCE_CLI_ENTRY).exists():
            return self._readiness_unavailable("source-launcher")
        probe = Path(__file__).with_name("model_readiness.mjs")
        try:
            proc = subprocess.run(
                [node_bin(), str(probe), str(self._runtime_root), self._provider, self._model],
                cwd=str(workdir), env=self._environment(), capture_output=True, text=True,
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired):
            return self._readiness_unavailable("diagnostic-process-unavailable")
        try:
            result = json.loads(proc.stdout)
        except (ValueError, TypeError):
            # Provider parse errors can quote document values: never relay stderr.
            return self._readiness_unavailable("diagnostic-response-unavailable")
        if not isinstance(result, dict):
            return self._readiness_unavailable("diagnostic-response-unavailable")
        if result.get("status") in ("missing-credential", "invalid-configuration"):
            if require_ready:
                raise RuntimeError(str(result.get("error") or "model credential configuration is invalid"))
            return result
        if proc.returncode or not (
            result.get("ready") is True
            or (result.get("ready") is None and result.get("status") == "diagnostic-unavailable")
        ):
            return self._readiness_unavailable("diagnostic-response-unavailable")
        return result

    def complete(
        self, prompt: str, *, system: str = "", workdir: Path | None = None
    ) -> str:
        """Return the model's answer to ``prompt``.

        ``workdir`` is where the completion runs, and it is not a detail: the agent
        this spawns has tools and writes files with relative paths, so a completion
        with the checkout as its working directory writes a node's artifacts into the
        repository. The reviewing nodes did exactly that — their `reviews/*.md`
        appeared at the checkout root while the run's own `reviews/` stayed empty, and
        the QA synthesis, reading the run directory, found no reviews at all. A caller
        with no run directory to name gets the scratch directory instead.

        Raises ``RuntimeError`` on a non-zero exit or timeout. The loop survives
        that and records it: a failed round contributes no proposals and lands in
        ``Optimizer.proposal_failures``, so a run whose proposer never answered
        says so in its report instead of looking like a search that found nothing.
        Raising is what makes the failure the caller's to handle -- this is the
        only place a dead key, a wrong model name and an exhausted quota, which all
        present as "the score did not move", are distinguishable from a mode that
        simply has no better version.
        """
        task = f"{system}\n\n{prompt}" if system else prompt
        # The launcher resolves its own entry; the child's working directory stays
        # the node's, not the checkout's.
        cmd = [
            *dsh_command(root=self._runtime_root, cli=self._dsh_cli),
            "--profile",
            "headless",
            # A node is one completion whose answer is the node's output, so a model
            # that backgrounds its subagents and ends its turn has not done the node's
            # work. See the overlay for why the base profile's default is wrong here.
            "--patch",
            str(NODE_PATCH),
        ]
        # The headless profile takes these for one invocation only: nothing is
        # written back to settings, so one optimized run against another model cannot
        # change what the next one uses.
        if self._provider:
            cmd += ["--provider", self._provider]
        if self._model:
            cmd += ["--model", self._model]
        cmd.append(task)

        scratch = Path(os.environ.get("REFACTORY_MODEL_SCRATCH", "/tmp")) / "refactory-model"
        run_dir = Path(workdir).expanduser().resolve() if workdir is not None else scratch
        run_dir.mkdir(parents=True, exist_ok=True)

        last = ""
        for attempt in range(1, self._attempts + 1):
            try:
                proc = self._run_completion(cmd, run_dir)
            except subprocess.TimeoutExpired as exc:
                # A tool-using completion may already have changed its workdir.
                # A deadline is not a transport failure: replaying the prompt can
                # duplicate real work even after its subprocesses have shut down.
                raise RuntimeError(
                    f"model completion timed out after {self._timeout_s}s; "
                    "requested runtime shutdown; not retried"
                ) from exc
            else:
                if proc.returncode == 0:
                    return proc.stdout
                cause = failure_cause(proc.stderr)
                if not is_transport_failure(proc.stderr):
                    # The model answered wrongly, the key is dead, the prompt is
                    # rejected: retrying spends two more completions on the same
                    # answer. Fail now and keep the diagnosis accurate.
                    raise RuntimeError(
                        f"model completion failed (exit {proc.returncode}): {cause}"
                    )
                last = cause or f"exit {proc.returncode}"
            if attempt < self._attempts:
                import time as _time

                _time.sleep(self._backoff_s * (2 ** (attempt - 1)))
        raise RuntimeError(
            f"model completion failed on the wire {self._attempts} time(s); last cause: {last}"
        )

    def _run_completion(self, cmd: list[str], run_dir: Path) -> subprocess.CompletedProcess[str]:
        """Keep the CLI alive long enough to dispose its managed tool processes.

        ``subprocess.run(timeout=...)`` kills the immediate child before raising,
        bypassing the DSH CLI's SIGTERM handler and stranding detached tool jobs.
        Do not signal process names, inferred descendants, or unrelated groups.
        """
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            cwd=str(run_dir), env=self._environment(),
            # A terminal Ctrl-C reaches the Python caller only. Forward one
            # shutdown request: DSH escalates a second signal to immediate exit.
            start_new_session=os.name == "posix",
        )
        try:
            stdout, stderr = proc.communicate(timeout=self._timeout_s)
        except BaseException:
            # Includes KeyboardInterrupt raised in this calling thread, not a
            # main-thread interrupt while complete() runs in a fork worker.
            # Cleanup precedes propagation; never reinterpret it as output.
            try:
                proc.terminate()
            except ProcessLookupError:
                pass
            try:
                proc.communicate(timeout=SHUTDOWN_GRACE_S)
            except subprocess.TimeoutExpired as exc:
                proc.kill()
                # An uncooperative launcher may leave inherited output pipes
                # open in a descendant. Do not wait on those pipes indefinitely.
                try:
                    proc.communicate(timeout=1)
                except subprocess.TimeoutExpired:
                    if proc.stdout is not None:
                        proc.stdout.close()
                    if proc.stderr is not None:
                        proc.stderr.close()
                    proc.wait(timeout=1)
                raise RuntimeError(
                    "model runtime did not finish shutdown within "
                    f"{SHUTDOWN_GRACE_S}s; forced exit; child cleanup unconfirmed; not retried"
                ) from exc
            raise
        return subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)
