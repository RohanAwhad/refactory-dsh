# Paired Create benchmark

Run from the repository root with `PYTHONPATH=refactory`. This driver does not
launch model work unless `live` is explicitly selected and preflight succeeds.
Use isolated source checkouts or git archives at the revisions in
`tests/fixtures/create_fidelity/v1/baseline.json`. Do not point it at a mutable
original checkout. No benchmark operation repairs authored deliverables.

```sh
python -m benchmarks.create_fidelity.harness prepare /tmp/create-pair \
  --reference /path/to/pinned/remote-factory \
  --baseline /path/to/pinned/refactory-dsh \
  --candidate /path/to/candidate/refactory-dsh \
  --dsh-python /path/to/dsh/.venv/bin/python \
  --remote-python /path/to/remote/.venv/bin/python \
  --runtime-root /path/to/installed/refactory-dsh \
  --hidden /path/to/historical/evidence
python -m benchmarks.create_fidelity.harness preflight /tmp/create-pair \
  --route-receipts /path/to/actual/sol61-high-probe
python -m benchmarks.create_fidelity.harness live /tmp/create-pair
```

Preparation freezes source bytes, lockfiles, brief, checker, topology contract,
runtime route, and timeout allowances. Changing any source requires a new trial.
The declared 12-hour full-mode ceiling is an initial cap, not a calibrated
completion estimate. Director stages retain 3600 seconds; builder and Overwatch
retain 1800. Timeout records distinguish the layer that stopped execution and
whether process cleanup was confirmed.

`live` runs the reference and updated DSH baseline concurrently. Candidate work
can be invoked separately with `--lanes dsh-candidate`. The request and input
files are identical except for explicitly disclosed native runtime mappings.
The reference uses its native role defaults; those aliases are not evidence of
actual served models. DSH requires observed Sol 6.1/high for directors and
children. An unavailable route blocks the launch, without fallback.

The user authorized Sol 6/high after Sol 6.1 was unavailable. Use
`--model gpt-6-sol --route-authorization "User approved Sol 6/high fallback on 2026-10-12"`
when preparing that distinct trial. Its receipts are evaluated against Sol 6,
and must not be described as Sol 6.1 evidence.

Each lane pauses for real human approval after producing a strategy. Inspect
`LANE/project/approval-required.json` and the strategy and intent files,
then deliver exactly that decision:

```sh
python -m benchmarks.create_fidelity.harness approve /tmp/create-pair dsh-candidate \
  --strategy-digest DIGEST_FROM_PENDING_REQUEST --verdict proceed \
  --approver "human reviewer"
```

`reloop` requires feedback. Decisions bind exact strategy and intent bytes and
are consumed once. Never preapprove or fabricate the human's answer.

After authoring, verify delivered code with native discovery and distinct
invocation directories, then run the unchanged standalone checker:

```sh
python -m benchmarks.create_fidelity.harness native /tmp/create-pair dsh-candidate
python -m benchmarks.create_fidelity.harness check /tmp/create-pair dsh-candidate
python -m benchmarks.create_fidelity.harness report /tmp/create-pair
```

The report includes JSON, Markdown and annotated HTML under `operator/report/`.
Static source graphs and runtime observations are labeled separately. Missing
traces, semantic reviews, receipts or invocations cannot become passing evidence.
The unchanged 56-case checker is independently scored from native package reuse.
Do not compare speed or cost causally from one diagnostic pair.

OS isolation currently requires macOS Seatbelt. It proves peer/evidence content
denial in child processes and private runtime writes. It is not a claim of a
general adversarial security boundary. Keep all historical evidence roots in
`--hidden` and never copy result artifacts between lane directories.
