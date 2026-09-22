"""A :class:`ModelClient` backed by a one-shot DSH run.

The outer loop's optimizers need exactly one thing from a model: text in, text
out. DSH already exposes that as ``dsh --profile headless "<task>"``, which is
the same mechanism the inner loop uses to run a mode, so nothing new is
introduced to reach a model and the credentials come from the same place.

The subprocess runs in a scratch directory: the headless profile is a full agent
with tools, and an analysis prompt must not be able to write into the run.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from refactory_dsh.paths import dsh_root, node_bin

#: Wall-clock ceiling for one completion, before the subprocess is called hung.
#:
#: It is a hang detector rather than a budget: the authoring modes' agent nodes are
#: tool-using tasks that write a package, run it and write up what happened, and one
#: of them (`qa_director`, which builds and runs an adversarial probe) exceeded a
#: five-minute ceiling in a live create run. A caller that knows its own nodes can
#: set `timeout_s`; the default has to cover the slowest node a shipped mode declares.
DEFAULT_TIMEOUT_S = 1800


class DshModelClient:
    """One-shot completions through ``dsh --profile headless``.

    Implements ``factory.outer_loop.optimizers.ModelClient``.
    """

    def __init__(self, timeout_s: int = DEFAULT_TIMEOUT_S, model: str = "") -> None:
        self._timeout_s = timeout_s
        self._model = model

    def complete(self, prompt: str, *, system: str = "") -> str:
        """Return the model's answer to ``prompt``.

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
        cmd = [
            str(node_bin()),
            "--import",
            "tsx/esm",
            "apps/cli/src/bin.ts",
            "--profile",
            "headless",
        ]
        if self._model:
            cmd += ["--model", self._model]
        cmd.append(task)

        scratch = Path(os.environ.get("REFACTORY_MODEL_SCRATCH", "/tmp")) / "refactory-model"
        scratch.mkdir(parents=True, exist_ok=True)
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                cwd=str(dsh_root()),
                timeout=self._timeout_s,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"model completion timed out after {self._timeout_s}s") from exc
        if proc.returncode != 0:
            raise RuntimeError(
                f"model completion failed (exit {proc.returncode}): {proc.stderr[-500:]}"
            )
        return proc.stdout
