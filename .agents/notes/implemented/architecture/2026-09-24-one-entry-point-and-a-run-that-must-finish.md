# Agent Note: One entry point, and a run that must finish

Status: implemented

English | [中文](2026-09-24-one-entry-point-and-a-run-that-must-finish.zh.md)

## Problem

Two things made this repository hard to hand to somebody else.

**There was no entry point.** Everything worked — the modes, the loop, the console, the GUI — but reaching any of it meant knowing an interpreter path and a module name (`.venv/bin/python -m refactory_dsh.loop …`), and the README led with an architecture diagram rather than with what a person does. The project this was ported from has the opposite shape: `factory ceo "<an idea>" --mode design`, one command, and the mode is a flag. Our own README's four steps were four different module invocations, and one of them (`interactive`) did not work at all: its default run root is relative and DSH refuses a relative session cwd, so the command a person runs to *use* a mode stopped before the graph started.

**A run could be abandoned and still look finished.** `refactory ceo … --mode create` on DSH reached the QA fork, where the CEO spawned the three review subagents *in the background*, ended its turn, and the graph runner saw quiescence and exited 0 with `best_score: 0`. The spine's own status file said `ready` — three nodes still due — and nobody was told. The walker runs an agent node in-process, so the same mode completed there, which is exactly the kind of difference between the two runtimes that this repository exists to not have.

## Decision

**One console script, `refactory`, and one `ceo` verb that takes an idea.** A root `pyproject.toml` declares the three local packages as editable sources (`refactory/`, the vendored `factory`, the vendored `srf`), so `uv sync` is the whole Python install and every documented command is `uv run refactory …`. `ceo "<an idea>" --mode design|create|edit` lowers the authoring mode and runs it on DSH with the terminal attached, so the plan-approval gate reaches the person; `--approve GATE=VALUE` states that answer up front for an unattended run, in the same ledger a resumed run replays. `run`, `optimize`, `list`, `doctor`, the console's read verbs, `package` and `ui` are the rest. Each verb dispatches onto the module that already owns it, so there is still one implementation per behaviour and `cli.py` only decides how a person reaches it.

**The brief travels as knobs, because a DSH run has no task.** `create` and `design` read `name`/`brief`/`check` from the run's task on the walker path and from knobs on the DSH path, and a brief alone yields a name (`_slug`), because refusing to start over a missing name when the sentence already says what is wanted is the wrong thing to be strict about.

**A CEO driving a graph cannot background a node's subagent.** The graph profile mounts `tool-subagent` with `enableRunInBackground: false` and `backgroundMode: one-shot`. The base profile's background-and-continuable default is right for an agent that fans out and collects later, and wrong here: backgrounding a node's subagent *is* abandoning the graph. This is enforcement in the tool rather than a request in the prompt, because a prompt that asks to wait is a prompt that can be ignored — and was.

**Quiescence is an outcome to check, not completion.** The graph runner reads `.refactory/spine-status.json` after the turn; anything but `done` writes a line naming the status and the nodes still due and exits 1. The spine records the cursor in that file when it is `ready`, so the failure says which nodes never ran. A composition with no work directory still exits 0.

**The documents a reader runs are generated, and generated from what the repository ships.** `refactory_dsh/tools/recipes.py` is one list of documented commands with what each should produce; the HTML tour renders it and `tools/check_recipes.py` executes it, so a command the page shows is a command that runs. The tour's catalogue is restricted to `refactory_packages/`, because a page generated from a machine's own `.factory/packages` can never be current on another machine — the same reason the tutorials index quotes `committed_listing()` and a test keeps the two equal.

## Consequences

A newcomer's path is `./install.sh`, `uv run refactory doctor`, `uv run refactory list`, `uv run refactory ceo "…" --mode create`. A fresh clone was verified end to end: install, doctor, the fifteen offline recipes, and the suite. The two example packages the documentation leans on (`counting_pack`, `dataset_probe`) had to ship, because they existed only in the author's workspace and every documented command failed without them. `dataset_probe` gained `min_items`, a knob its Task's check reads, because a shipped mode with no knob has no search surface and a gate says so.

A run that stops is now a failure with a name, which is what the graph profile's own README promised. The cost is that a composition whose CEO ends its turn early exits non-zero where it used to exit zero — correct, and visible.

## Alternatives considered

Making `factory ceo` a shell wrapper around the Python modules was rejected: a wrapper is a second place that knows the commands, and the commands change. Putting the authoring modes' name and brief in the intent ledger instead of knobs was rejected because the ledger is prose folded into prompts, not a value an op can read by name. Fixing the abandonment in the prompt alone was rejected after it failed in practice: the constraint belongs in the tool that allows it. Treating a missing status file as a failure was rejected because a composition may legitimately have no work directory.
