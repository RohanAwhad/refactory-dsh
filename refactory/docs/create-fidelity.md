# Create fidelity candidate

`default_pack/create_fidelity` is an opt-in implementation of the pinned remote
Create-v2 contract. The public `create`, `design`, and fast variants keep their
existing graph identities. Controlled tests establish protocol behavior; they
do not establish live model fidelity or authorize a public rollout.

## Use

```sh
refactory ceo "Build a reusable package for my task" \
  --mode create_fidelity --name my_package --workdir /tmp/my-create-run \
  --provider openai --model gpt-6.1-sol --effort high
```

The provider must actually serve the requested model and effort. Directors,
researchers, strategists, reviewers, and gates execute through DSH. A run stops
at the human strategy gate until the person reviews the current strategy and
original intent and provides a fresh decision. Standing approvals are rejected.

The generated portable package lives under the run's `.factory/packages/`.
`name` and `brief` identify the request. `requires_input` declares required
runtime parameter names; `project_path` selects the read-only project to study.
`output_folder` may select a directory inside the native package workspace.
`child_attempts` defaults to 2, and `director_parallelism` to 7. `variant` also
supports isolated `update-existing` and native `plugin` packages; see the
[variant and adoption contract](create-fidelity-variants.md). No Task or optimization score
is required for a portable, deliberately unscored generated mode.

## Reviewed native mappings

The candidate retains 29 stages, 33 edges, original gate owners, research and
strategy planning, three parallel QA branches and their join, Overwatch,
documentation review, final precheck, and non-blocking archives.

The actual archivist finalizes its own note with native `archive-update`, which
regenerates a sourcebound performance report and records the optional MemPalace
attempt. See [the archival mapping and installed-backend boundary](create-fidelity-archive.md).

Each director is a composite native operation with a DSH CEO planning call,
independent DSH child calls, complete raw report collection, CEO quality review,
and bounded retries of rejected children. Research plans 3–7 children; strategy
plans 2–5; QA plans 2–5 custom testers plus native workflow-validation and CLI
integration testers. The source's 3600-second director and 1800-second builder
and Overwatch allowances are retained. Supplied outer limits still apply and
appear in worker evidence.

Large evidence bundles use bounded reading passes by the same logical role and
model route, followed by the original task's final assessment. The runtime keeps
the complete source bytes and records exact UTF-8 ranges, actual worker and
delivery receipts, claim excerpts, coverage findings and the final reconciliation
digest under `.refactory/create-fidelity-context/`. Missing ranges, stale inputs,
invalid excerpts or an unbound final response fail the stage. All reading passes
and the final task share the original stage deadline and cancellation scope.
Reading evidence does not replace the role's required execution or review work.
This staging applies to DSH execution. An explicitly selected native builder
retains its existing direct delivery and backend context handling.

Read and adjudication requests enumerate the exact IDs allowed in their coverage
fields. Requirement labels inside source documents do not expand that vocabulary;
their substantive findings still belong in quoted claims. Unknown coverage IDs
fail validation rather than being silently removed or admitted. This restriction
does not narrow the original requirements or the final role's review duties.

When the read records themselves exceed the ceiling, the same role adjudicates
bounded groups of complete prior responses. Every positive finding needs an
explicit source disposition; adverse, unresolved and absent-page findings carry
forward verbatim under stable identities. The final task receives the complete
remaining records with the full strategy, intent and decision obligations.
All intermediate responses remain preserved and digest-bound. The runtime limits
adjudication depth and calls and refuses a reduction that makes no size progress.
Each adjudication request states its exact maximum response size in UTF-8 bytes
and asks for compact JSON. Formatting guidance never permits shortening retained
findings; a response that cannot fit while preserving them still fails.

The configured prompt byte ceiling is not a claim about an unknown provider's
context window. Oversized fixed instructions or final reconciliation records fail
explicitly; they are never truncated. Small inputs retain direct delivery. These
receipts establish supplied evidence and model responses; independent semantic
review must still judge whether the role performed its duties correctly.

Remote workflow registration becomes native package discovery, validation,
admission, skill export, and invocation. Source Study becomes a read-only AST
and repository inventory. Reports retain their full bodies and provenance in
addition to summaries. AI repair critique is appended to
`.factory/reviews/repair-feedback.md`; human strategy revisions are appended to
the original intent and return through strategy planning. This narrow path
mapping preserves the exact human-approved strategy and intent across a
builder-only repair. Changing either invalidates approval.

Archives must settle before terminal success. A failed final precheck may
produce a diagnostic archive but remains failed. Native shell commands retain
their declared timeouts, and uncertain worker cleanup is recorded as unknown.

## Paired benchmark

See the [benchmark README](../benchmarks/create_fidelity/README.md), pinned
[source contract](../tests/fixtures/create_fidelity/v1/reference.json),
[semantic delta](../tests/fixtures/create_fidelity/v1/semantic-delta.md), and
[shared protocol](../tests/fixtures/create_fidelity/v1/protocol.md).
The source-derived oracle does not import candidate expectations.
The [remote IR boundary](create-fidelity-ir-boundary.md) describes the separate
optional schema compatibility checks and unresolved active-extension boundaries.

The booking benchmark separates artifact capability (the frozen 56-case checker
and native discovery/reuse) from authoring fidelity (roles, child dispatch,
causal joins, report consumption, approvals, routes, and lifecycle evidence).
Missing evidence is unavailable, never passed. The checker and peer artifacts
are hidden by an inherited OS sandbox. Each lane has private runtime/session
stores; only read-only credential references may be shared.

The DSH `openai` provider returns `UNKNOWN_MODEL` for `gpt-6.1-sol` on the
current host. The user subsequently authorized a Sol 6/high fallback, and a
bounded `gpt-6-sol` probe completed with the matching observed route. Benchmark
trials must record this override explicitly. Live reference/baseline/candidate
completion, human strategy approval, final booking deliverables, and semantic
output review remain acceptance requirements.
