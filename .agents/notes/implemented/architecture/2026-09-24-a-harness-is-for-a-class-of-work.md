# Agent Note: A harness is for a class of work

Status: implemented

English | [中文](2026-09-24-a-harness-is-for-a-class-of-work.zh.md)

## Problem

**The code did not say what the system produces.** The product is an agentic harness for a class of work — "write the build script for any Java source tree", not "write the script for this tree" — and nothing a run acted on said so. `create` produced a single-shot mode by default, and naming a Task was an optional second flag that did not by itself produce a per-item harness, so a harness could be tuned until one hardcoded example passed and shipped as if it generalised. Every shipped example package was single-instance or deterministic, so nothing in the catalogue contradicted the reading.

**The outer loop could not tell memorising from working.** Every candidate was graded on the same items the search was tuned on, and the score had no cost for size, so a search could win by memorising the items or by growing the graph. A hard sanity run — `create` for a CWE-78 patch harness — had already shown the shape of this: the produced harness's scorer could be gamed (deleting the test, or a `pytest.ini` that narrowed collection, scored 1.0), and while the pipeline refused to ship it, the loop itself had no way to see it.

**The documents described a mode without saying what it was for.** The tour's definition was "A Python class: a graph of nodes with typed knobs, and a `score()`", which is a program rather than a harness.

## Decision

**`create` asks which of the two things you are building, before any model call.** `--task <module:Class>` (the `task_ref` knob) names the class of work: the produced package binds a `data` node to that Task, runs its subgraph once per item the Task lists, and is judged per item by the Task's `verify`. `--single` (the `single_shot` knob) is the explicit special case. Naming neither is refused before any model call: `refactory_dsh/cli.py` refuses it at the command line, `refactory_dsh/console.py`'s `create` verb forwards `--task`/`--single` to the refusal `create.py` raises before the mode is loaded, and `refactory_dsh/create_ops.py`'s `prepare` refuses it for every other entry point. The Task binding is [The v2 modes and the workflow vocabulary they needed](2026-09-22-v2-modes-and-the-workflow-vocabulary.md).

**The `data` node is the harness's input port.** `create_ops.py`'s `baked_item_problems` parses the produced source and refuses a package that writes its items into the graph with `inline_items`, naming the file and line; `unfeedable_data_node_problems` refuses one that declares a `data` node with neither a non-empty `task_ref` nor a `source_path` with a `source_format`. `check_package` runs both before the package is registered, and it parses rather than imports the model-written code. What a harness is pointed at is material, not meaning: a harness with its items in the graph is a harness for those items. The node's iteration on both runtimes is [A data node iterates on both runtimes](2026-09-23-a-data-node-iterates-on-both-runtimes.md).

**A score is a claim about the class.** The outer loop evaluates every candidate on the train part of a Task's items and re-evaluates the winner on the held-out part; the audit names `train`, `holdout`, `gap`, `overfit`, `threshold` and `instances`, lands in `<run root>/holdout.json` and in the run report, and travels with `refactory best`; `refactory adopt` refuses an overfit winner unless `--allow-overfit` is given. `refactory_dsh/task.py` partitions the items by sorted instance id and records the part in the run directory as `subset.json`, not as a mode knob the search can move, so a search cannot choose its own exam. A selection of one item cannot be split, and the audit records that rather than inventing a number. The default fraction is 0.3, and `overfit` is `train − holdout > 0.15`.

**Size is the search's preference, not the mode's semantics.** A candidate's search score is `raw_score − parsimony × nodes`, applied only in `Loop._evaluate` in `refactory_dsh/loop.py`; the mode's `score()` is untouched, and the unpenalised `raw_score` is recorded beside it in every candidate row and journal record, whose field lives in `optim/base.py`. The coefficient is `LoopConfig.parsimony`, default 0.01, and a person sets it — `--parsimony` on `refactory optimize` in `cli.py`, and the console's settings table in `console.py`.

## Consequences

`create` refuses a brief that names no class of work, and `--single` is the name for the special case; `refactory/tests/test_create.py`, `refactory/tests/test_cli.py` and `refactory/tests/test_gui_loop.py` hold both.

A produced harness carries the Task binding and an open input port, so it is used later on any item of the class from the command line, the console or the panel.

A run reports both numbers: what the winner scored on its training items (unpenalised, `raw_score`) and what it scored on the held-out ones, with the gap. A harness that only works on what it was tuned on is visible as such instead of looking finished.

`adopt` can refuse: an overfit winner needs `--allow-overfit`, and the audit lands beside the package either way.

Two tests changed meaning rather than numbers. The tutorial's option table is proven complete against the loop's own `--help` by `refactory/tests/test_docs_tutorial.py`, so a new flag cannot be added without the page learning it. `Loop.install` refuses two same-state proposals in one batch as `E-duplicate`, because the changed ranking surfaced a batch slot spent on a point already scored.

## Alternatives considered

**Keeping single-shot as the default and asking for a Task only when wanted.** Rejected: that is the state that produced harnesses which had memorised one example.

**Splitting the items inside the graph, as a knob the search can move.** Rejected: a search that can choose its exam is not being examined.

**Recording the held-out instance ids in the subset file instead of the fraction.** Rejected: a candidate that adds a data node would then be filtered to nothing rather than getting its own deterministic split.

**Putting the size cost inside a mode's `score()`.** Rejected: that would make "score" mean two things and put a search preference into the mode's semantics.
