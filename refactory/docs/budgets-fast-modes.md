# Fast authoring and shared run budgets

`default_pack/create_fast`, `default_pack/edit_fast` and `default_pack/design_fast`
are ordinary, inspectable modes. Their graphs implement directly from the brief,
run one focused QA stage, and allow one repair by default. They do not invoke the
full research or multiple-reviewer graphs. The existing `create`, `edit` and
`design` modes remain available.

Create and Edit produce reusable mode packages. They retain structural package
admission and canonical runtime smoke; Edit retains regression comparison.
Design produces an artifact, including a small change in a copied existing
project. It is not an optimization target.

## Acceptance status

Fast modes are implemented and selectable from the CLI and native GUI, but their
complete authoring paths have not yet passed release acceptance. All six bounded
Create/Edit/Design checks exhausted their token allocations before graph completion.
The CLI Design run reached a focused check and QA artifact; that partial result is
not a completed fast run. [Issue #234](https://github.com/akashgit/refactory-dsh/issues/234)
remains open for successful completion through focused QA and bounded repair in both
interfaces. See the [recorded outcomes](release-validation-20260926.md#fast-mode-outcomes).

## Choose the graph

In the terminal, choose `--mode create_fast`, `--mode edit_fast` or
`--mode design_fast` with `refactory ceo`. In the native GUI, select the same mode
from the mode catalogue and configure its knobs. The direct authoring commands
also accept `--fast`:

```bash
uv run python -m refactory_dsh.design --fast \
  --name tiny-helper --brief 'Write helper.py with add(a, b) and a small assertion test.' \
  --check 'python helper.py' --repair-attempts 1 --max-elapsed-s 360
```

For a change to an existing project, pass `--source-project /absolute/project`
to fast Design, or set its `source_project` knob. Refactory copies the source to
the output artifact and applies emitted new or changed files there. It does
not replace the source project. Dependency directories, Git metadata, previous
run output and `.env*` files are excluded; source symlinks are refused. File
deletion is not part of this small-change emission contract.

`repair_attempts` is one shared bound across structural admission and focused QA,
from zero to three. It counts repairs after the first implementation, so one
permits at most two implementations. The required Design `check` runs as a bounded
command against the materialized artifact. Create/Edit can also name an explicit
`check`; an empty check is reported as not requested, not passed. Focused QA asks
the worker to inspect requested behavior and relevant regressions, with evidence
for each check and an explicit list of checks not run.

Read `focused-check.json`, `focused-qa.json` and `fast-report.json` in the run
directory. They separate command results, model-authored review, structural
admission and runtime smoke. Exhausted repair produces an unresolved report and
no success report. A model's favorable review is not proof of artifact quality.

## Allocate resources

Run-wide token and elapsed limits are **optional and unset by default**. Fast mode
is defined by its smaller graph and repair bound, not a universal token quota.
The release-check cutoff was a test allocation. Read [Choosing a budget](budget-strategy.md)
for the usage breakdown, tradeoffs and revised acceptance strategy before selecting
another limit.

Elapsed time and tokens are separate from candidate counts, graph steps and one
completion's hang timeout. CLI controls are `--max-elapsed-s`, `--max-tokens`,
`--validation-reserve-s` and `--validation-reserve-tokens`. The GUI exposes the
same effective run settings. Python graphs accept the same fields:

```python
run = mode.run(
    workdir,
    model=client,
    resource_limits={"max_elapsed_s": 360, "max_tokens": 30000,
                     "validation_reserve_s": 60, "validation_reserve_tokens": 5000},
)
```

The values above illustrate accounting controls; they are not an allocation known to
complete fast authoring.

The shared `.refactory/resource-usage.json` ledger aggregates coordinator and
nested-worker usage, retries and validation. Repeated cumulative receipts for
the same worker replace its previous total instead of counting it twice. The
outer loop reserves validation resources before admitting another candidate;
required final validation can use the reserve. Fast package smoke and fast
Design's check are explicit validation stages. Fast graph nodes declare their resource
phase independently of the worker role. Design uses final-validation resources for
its check, QA, gate and report; Create/Edit retain that reserve for canonical smoke
and reporting. A repair returns to the work allocation. Mode authors can set
`nn.Node.resource_role` to `work` or `final_validation`; omission retains the prior
role-based/inherited behavior.

Elapsed limits are checked at graph boundaries and while owned model/check
processes run. Arbitrary in-process Python operations are checked when they
return; they cannot be forcefully interrupted safely. Token limits are enforced
at provider usage boundaries. Each worker's in-flight provider request may
overshoot; this is not an exact provider-side token cap. A backend without token
telemetry refuses a token-limited launch. Missing usage or pricing is shown as
unavailable (`null`), and estimated cost remains an estimate. Candidate counts
never stand in for token or time measurement.

An exhausted budget before required validation is incomplete/unvalidated, not a
success. Owned processes are asked to shut down and partial artifacts remain.
The ledger stops charging elapsed time when the owning run pauses or finishes.

## Resume deliberately

At a completed node boundary, supported resume retains completed work and its
consumption. An explicit larger allocation can extend the limit; it does not
reset usage. The direct authoring commands accept `--resume RUN_DIR` with
`--fast` for a saved fast graph. Python uses
`ModeClass.resume(workdir, model=client, resource_limits={...})`.

An interrupted node with an unknown side-effect outcome is never automatically
replayed. Inspect its artifacts before an explicit restart. Changed graph
instructions or a changed Design source-project snapshot invalidate dependent
results and require a new run. Partial artifacts are evidence, not a claim that
the interrupted node completed.

The Python executor conservatively refuses partial-fork recovery because its
saved record does not yet support restoring individual branch facts. It does
not replay successful sibling branches or silently retry an unknown worker.

The final-validation reserve belongs to the outer run. During optimization, a
candidate's internal validation nodes cannot spend the winner holdout reserve.
Nested searches reuse their enclosing ledger and do not reset usage, extend the
allocation or pause the enclosing clock. Deterministic candidates share elapsed
limits without requiring a model client; no provider usage remains unavailable.

Focused QA is bound to a digest of the materialized output after the requested
check. If QA edits those files, the gate requires another implementation/check
cycle within the same repair limit; a passing review cannot silently approve a
changed, unchecked artifact.

Custom model clients used with token caps must implement `with_budget(budget)`
and report measured or explicitly estimated usage into that shared ledger.
Clients without this protocol are refused at the completion boundary, including
optimizer proposals. Supplying such a client to a deterministic graph does not
force a model call or invent usage. Existing uncapped clients stay compatible.
