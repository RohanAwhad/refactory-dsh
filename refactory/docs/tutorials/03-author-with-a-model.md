# 3. Author with a model

Create, Edit and Design are ordinary graphs in `default_pack`. Run them interactively through terminal CEO or GUI chat, or use their Python APIs. They call a model; duration and output quality depend on the workload, model and review cycles.

| Mode | Output | Completion evidence |
|---|---|---|
| Create | A new registered package of modes | Structural validation, reviews and canonical smoke |
| Edit | A revised package with source provenance | The same checks, plus comparison status where meaningful |
| Design | An artifact | The supplied check command and reviews against the brief |

Configure [managed model credentials](../models.md#credentials-for-nested-execution) first. These examples are starting briefs; inspect actual results rather than assuming a particular generated mode name or score.

## Create a reusable harness

```bash
uv run refactory ceo \
  "Draft and check a project README from a supplied brief. Declare the input and output contract clearly." \
  --mode create --name readme_pack --single
```

`--single` deliberately chooses a single-run harness. For a class of work, use `--task module:TaskClass` instead. The importable [Task](../datasets.md) lists inputs, prepares each item and verifies the result; Create binds the generated mode to it. The final `create.json` records the actual binding.

The current graph surveys existing packages, researches the problem, develops a strategy, asks you to approve it, emits files, validates them, runs parallel reviews and an audit, then registers the package and performs the canonical smoke. Repair loops are bounded by the declared controls. Full model/Task validation belongs to that final smoke; review stages use bounded checks for their findings.

A sample run does not establish quality on every future input. A failed inner execution is a failure, not a measured zero. A specifically unavailable model may leave an explicit unmeasured score/reason; it is not proof of working authentication or a passed model run. Inspect `smoke.json` and the final report.

When the mode needs a named input, declare it in the brief and through `--knob requires_input=<name>` on CEO. The Python Create entry point also supports `--input` and repeatable `--sample NAME=VALUE`; samples exercise supplied inputs rather than only defaults. Read that entry point's help for its own flags.

Inspect the generated package with:

```bash
uv run refactory package show readme_pack
uv run refactory package verify readme_pack
```

The catalogue/report names its generated mode IDs. Use the actual ID to run it. `package smoke` without `--model` is keyless; agent-containing modes need the model-backed path. Structural admission is not a guarantee of safe arbitrary generated code; see [hardening](../hardening.md).

## Edit an existing mode

```bash
uv run refactory ceo "Improve the search strategy while preserving its input and output contract" \
  --mode edit --revise circle_packing/adaptive --name revised_pack
```

Edit uses the Create graph with the source and revision intent. It writes a new package; the source remains available. The Python driver preserves the graph's canonical report rather than independently repeating keyless before/after passes.

Comparable deterministic executions use the same accepted sample inputs. A measured regression is refused by default; `--knob allow_regression=1` on CEO explicitly allows that tradeoff. Model-dependent or incompatible-input comparisons are marked unavailable with a reason, not reported as non-regressions. Inspect `revision.json`, `before_score`, `after_score`, `regressed` and `revision_note`.

The Python entry point supports `--sample NAME=VALUE`, `--allow-regression` and `--resume RUN_DIR`. These are entry-point-specific flags, not all aliases on CEO. A supported resume keeps completed stages; rerunning an ordinary CEO launch is a fresh invocation, not generic continuation.

## Design an artifact

```bash
uv run refactory ceo \
  "Build wordcount.py for a UTF-8 input file. Include sample.txt and verify line, word and character counts." \
  --mode design --name wordcount --check "python wordcount.py sample.txt"
```

Design researches and prepares a design document for your approval, implements the artifact, executes the check and runs reviews. The check executes from the artifact directory. Its exit status establishes only what the command checks; correct semantics may need additional evidence. Visual deliverables need a rendered inspection, and exchange files need a suitable independent consumer.

The original brief, approved design and explicit steering remain the acceptance baseline. A README limitation or reviewer summary cannot silently waive requested behavior. If an external validation dependency is missing, preserve the artifact and report the unmet requirement honestly.

The artifact is published under `.factory/artifacts/<name>`, with its exact absolute path in the run report. Design does not register a mode or produce a numeric optimization objective. Use it when the deliverable is the artifact itself.

## Approval, time and evidence

Leave user gates interactive when you want to review the plan. `--approve gate_strategy=proceed` supplies deliberate upfront authorization for unattended work. Approval history and scope matter; [the interactive tutorial](06-run-it-with-a-person.md) explains them.

Create/Edit's `smoke_timeout_s` knob bounds each full smoke pass (default 3600 seconds); it is separate from an agent completion deadline and does not bound total authoring time. API flags and graph knobs are documented by their respective help/catalogue surfaces.

Use the exact work directory returned by the launch. Research, design, review, progress and final report files belong to that run. Do not confuse authoring evidence with an optimizer's `analysis/generation-*.md` records.

Fast Create/Edit/Design graphs with one focused QA stage and bounded repair are implemented. Their complete CLI/GUI acceptance remains open in [#234](https://github.com/akashgit/refactory-dsh/issues/234) after the bounded checks exhausted their token allocations; see [fast modes and budgets](../budgets-fast-modes.md). Users can define their own modes today; research and QA are graph choices, and the platform does not require every mode to use the full authoring pipeline.

Next: [run with a person](06-run-it-with-a-person.md) or [drive GUI chat](05-drive-it-from-the-gui.md).
