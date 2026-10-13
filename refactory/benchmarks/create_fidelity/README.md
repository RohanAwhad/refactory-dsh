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

Fresh preparations record `private-native-temp-v1`: short real per-lane
`/private/tmp/cf-*` directories owned by the current user with mode 0700. Native
Claude receives its supported `CLAUDE_CODE_TMPDIR` override; ordinary `TMPDIR`
alone does not redirect its `/tmp/claude-UID` runtime. Policies explicitly allow
the lane's temp access and deny peer/operator access and shared global temp
writes. Directory device/inode/owner/mode identity is verified before use and
removal. Creation and explicit `cleanup-native-temp ROOT` receipts live under
the operator; cleanup refuses running or unconfirmed lane ownership. Perform
cleanup after native verification and final evidence collection.

`native-probe ROOT remote-reference` explicitly runs one real native CEO request
before full reference authoring. A probe-only supported `FACTORY_CLAUDE_BIN`
passthrough adds `--tools '' --max-turns 1 --max-budget-usd 0.10` to the same
native CLI, retaining the same provider and source `opus` alias. The owned
35-second process deadline includes cleanup; the source request has a
20-second inactivity allowance. It writes only private probe/runtime state and
never authors benchmark deliverables. Full launch preflight requires the actual
marker, successful native return code and matching frozen config/CLI identity.
Auth status alone cannot pass this requirement. Missing ADC/auth errors remain
failures; credential references and provider/model changes require separate
explicit configuration. V9's global-temp failure remains historical failure.
The optional `prepare --native-adc-reference /absolute/existing/adc.json` maps
native `GOOGLE_APPLICATION_CREDENTIALS` to that single nonsymlink file with
literal-file read-only policy. Preparation records path/type/configuration
metadata without reading, hashing or copying its contents, or permitting the
surrounding home/config directory. This retains native Vertex authentication
and source aliases; it introduces no provider/model fallback. Failed preflight
controls are retained and a changed reference requires a fresh configuration.

`live ROOT --lanes dsh-candidate` can launch a ready DSH lane while reference
invocation readiness remains unavailable. Each selected DSH lane still requires
the observed served route, project isolation, runtime isolation and native temp
isolation. A selected reference also requires both native readiness receipts.
Preflight records the observed config/policy hashes; launch rejects changed or
missing bindings and records the source preflight hash/time in
`operator/selected-launch.json`. Mixed selections require every selected lane
to be ready. The overall pair status remains unavailable when reference readiness
fails, and unselected lanes never dispatch or gain a passing score.

Preparation freezes source bytes, lockfiles, brief, checker, topology contract,
runtime route, and timeout allowances. Changing any source requires a new trial.
Authoring context v3 also supplies the same explicit external-check ownership
to every lane, with a separate frozen digest. The public booking brief and
56-case checker stay unchanged. The checker runs under the operator after
authoring; internal gates must still evaluate actual local tests and defects,
while final benchmark acceptance cannot pass without the external checker.
Version 3 also assigns actual cross-lane public-brief/common-instruction comparison
and frozen source/runtime/checker identity checks to the outside operator. Each
strategy/report separates a local Create verdict from an external acceptance
table: deliberately unavailable operator evidence stays PENDING/UNVERIFIED and
cannot by itself fail local authoring, nor supply a benchmark PASS. Every locally
required check, observed protected-file violation and genuine defect still
controls the original gates; later precheck/archive must execute at their actual
stages. All configured lint/health commands remain required; unconfigured lint
is reported as not executed, never as a tool pass. Configurations and trial.json
record `authoring_context_version: 3` and the common boundary hash. The operator
must actually verify identities before full acceptance; no context grants it.
Older trials retain their original context and cannot gain this clarification
through re-scoring.
Supply `--baseline-runtime-root` and `--candidate-runtime-root` to keep each
worker launcher anchored to its frozen checkout. These override the common
`--runtime-root`. Installed dependency bytes, symlink bindings and launcher
bytes have a separate identity checked before and after execution. Shared
published dependencies may be copied into a disposable snapshot; workspace
bindings must point into the corresponding frozen checkout. Do not use the
original dirty checkout as an unrecorded runtime dependency.
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
The pending request names its exact `input_paths`: candidate/reference use
`.factory/strategy/current.md` and `user-intent.md`; baseline uses `strategy.json`
and `brief.json`. Baseline recovery uses its documented native
`Mode.run(start=checkpoint.node, facts=checkpoint.facts)` API because the pinned
`Mode.resume` rejects newly added human ledger context. This mapping is recorded
in `raw/resume-*.json`; the native gate still consumes the fresh answer itself.

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

The reference execution configuration is separately versioned with
`prepare --remote-route`. The default `native-python-v1` preserves the original
single native executor call, including its recorded inline-Python quoting
failure. `native-shell-prefix-v1` selects the same executor's supported shell
fallback for exactly `init_user_intent` and `synthesize_qa`. Their exact Python
code and project argument remain separate shell arguments, including paths
containing spaces or quotes. The source checkout is never patched.

For a fresh comparator use `--remote-route native-shell-segments-v2`. It adds an
explicit controller correction to the two commands above. The pinned executor
does not follow HALT edges, including its bootstrap branches. The controller
can start another native execution only along these declared edges:
`gate_has_factory → discover`, `gate_factory_md_exists → create_factory_md`, and
`gate_precheck → archivist_build`. Bootstrap continuation requires the native
parser's exact diagnostic containing the actual canonical `HALT` token. AI and
user HALTs, command failures and unexpected diagnostics always stop. Source
node fields, prompts, roles, gates and allowances are preserved. Each segment
uses public `Workflow.subgraph` for all nodes reachable from its selected start,
including native implicit fork/join links. All original edges whose endpoints
are retained remain exact, including optional branches and reloops; full
workflow metadata is preserved except for explicit native `runtime_inputs` at
the boundary: only retained-node reads backed by hash-verified actual prior
native outputs are added. This declares the supplied intent/profile/review
artifacts without weakening any retained node's input contract. Native
constructor validation stays enabled. Only unreachable predecessors are excluded
from each execution segment. The public native `completed_files` state restores
only actual prior native result outputs with
verified unchanged hashes. Four segments and the overall deadline bound this
controller; it has no implicit retry loop.

`raw/launch.json` records the original/effective commands, full graph hashes,
route and execution-correction label. `raw/remote-segment-N.json` records each
segment graph/start hash, original and full effective graph hashes, excluded
unreachable predecessors, restored input hashes, unmodified native result/events,
bound native runtime-input hashes and any causal declared-edge continuation.
These receipts do not turn the
corrected route into evidence of the original single-call behavior. Historical
failed trials remain unchanged; use a new trial directory for this route.
The earlier `native-shell-segments-v1` full-graph start-node copy is retained as
a diagnostic route. Its v8 failure is preserved: the native constructor rejects
unreachable predecessor nodes when starting at `discover`. V2 does not disable
that check or reinterpret v8 as a successful comparison.

A semantic precheck HALT followed by diagnostic archive remains an overall
failure even when the archive succeeds. The pinned `factory precheck` actually
exits 1 on failed checks, which the native executor reports as a command failure.
This route stops on that actual ABI and cannot claim diagnostic archival for
that path. It does not suppress the exit code or invent a gate verdict. Normal
successful prechecks remain native. Spaced/quoted command-transport controls
also do not establish that the source's separate inline-Python gate commands
support those paths; fresh benchmark projects use ordinary allocated paths.

Fresh remote preparations also record `private-native-home-v1` in trial/config
identity. The remote process receives `HOME=PROJECT/runtime/native-home` and
`CLAUDE_CONFIG_DIR=PROJECT/runtime/claude` before importing the pinned source.
This excludes the original user's `.factory/config.toml`, prompt overrides,
evolved playbooks and workflow/session state. The native Claude runner and
shipped source prompts/playbooks remain in use. Global profile/playbook path
environment overrides are excluded; native authentication environment remains
available. No credential files or original settings are copied, and no new
credential file reference is assumed. If actual native authentication requires
an external file, add only an explicitly authorized read-only credential
reference to a new frozen configuration and isolation policy.

Preflight records actual source prompt/runner resolution and allowlisted
`claude auth status --json` fields under the lane policy. It exposes hashes and
paths instead of prompt, settings or credential contents, and dispatches no
model. Reported login readiness does not establish a working API credential,
the served model or successful authoring. Vertex/third-party authentication and
native source aliases remain separate from DSH's observed Sol 6/high route.
Existing frozen trials retain their
original environment and cannot gain this correction through re-scoring.

Independent semantic, human-decision and README reviews belong under
`operator/reviews/LANE/`, outside every authoring project. The optional
`operator/review-context.json` identifies the operator reviewer and allowlists
actual user decisions in `trusted_user_references`, grouped by lane. It must
come from the real review conversation, never from a worker's output or a
claimed approver name. Without that evidence the relevant rows stay unavailable.
The operator review validator binds source obligations, current raw file hashes,
actual command receipts and the exact consumed approval. An unfilled review
template is not a passing review. See the [operator evidence schema](operator_evidence.md)
for the required fields and unfilled templates.

Report generation records the evaluator version, source hashes and review-context
hash in `operator/report/evaluator.json` and the JSON/HTML comparison. This
separates later independent evaluation from the frozen code that authored the
artifact; re-scoring cannot rewrite a trial's source or runtime identity. Reviews
must follow native/CLI verification, because any later change to the raw evidence
invalidates their hash binding. Observed deadline snapshots are available only
for runs whose worker runtime actually recorded them; older trials cannot gain
missing timing telemetry through re-scoring.

OS isolation currently requires macOS Seatbelt. It proves peer/evidence content
denial in child processes and private runtime writes. It is not a claim of a
general adversarial security boundary. Keep all historical evidence roots in
`--hidden` and never copy result artifacts between lane directories.
