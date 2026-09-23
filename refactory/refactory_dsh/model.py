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

import os
import subprocess
from pathlib import Path

from refactory_dsh.paths import dsh_root, node_bin

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


class DshModelClient:
    """One-shot completions through ``dsh --profile headless``.

    Implements ``factory.outer_loop.optimizers.ModelClient``.
    """

    def __init__(
        self,
        timeout_s: int = DEFAULT_TIMEOUT_S,
        model: str = "",
        provider: str = "",
    ) -> None:
        self._timeout_s = timeout_s
        self._model = model
        self._provider = provider

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
        # Absolute, because the child's working directory is the node's, not the
        # checkout's.
        cmd = [
            str(node_bin()),
            "--import",
            "tsx/esm",
            str(dsh_root() / "apps" / "cli" / "src" / "bin.ts"),
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
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                cwd=str(run_dir),
                timeout=self._timeout_s,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"model completion timed out after {self._timeout_s}s") from exc
        if proc.returncode != 0:
            raise RuntimeError(
                f"model completion failed (exit {proc.returncode}): {proc.stderr[-500:]}"
            )
        return proc.stdout
