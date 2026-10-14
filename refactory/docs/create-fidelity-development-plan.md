# Create development loop

The implementation is tracked by parent #310 and draft PR #311. Repeated full
booking runs are replaced by this ordered development loop. Historical trials
remain immutable. Public Create adoption and release acceptance remain separate.

1. **[#335](https://github.com/boston-harness-group/refactory-dsh/issues/335): timing.**
   Match pinned source inactivity, hard-wall and role/default precedence. Use
   real controlled subprocesses to test activity, silence, wall expiry and cleanup.
2. **[#336](https://github.com/boston-harness-group/refactory-dsh/issues/336): fast gate.**
   Run the existing full graph with controlled responses, including real package
   execution, every logical stage, build rework, approval freshness, QA joins and
   failed-precheck archival. Target under 60 seconds; stop at 120 seconds.
3. **[#337](https://github.com/boston-harness-group/refactory-dsh/issues/337): tiny live smoke.**
   Have full Create author `portable_probe/read`: read a runtime JSON object,
   write it to invocation-local `result.json`, reject malformed/non-object input.
   Use an empty study project, concise public brief, unchanged original fanout
   bounds, all gates and exact delegated strategy review. One live trial is
   capped at 15 minutes and 40 model dispatches. Application checks take seconds.

From the repository root, with its Python environment:

```sh
PYTHONPATH=refactory python -m benchmarks.create_fidelity.fast_check \
  --output /tmp/create-fast-result.json
```

The JSON result links retained logs, JUnit results and actual test artifacts.
The two full-path tests assert all 29 stages, once without repair and once with
build repair. Scripted responses prove orchestration, not live model quality.

Do not launch another live run after a failure until its first concrete cause
has a local reproduction and passing regression. Do not add more providers,
re-run the reference, or restart booking/update/plugin while developing the tiny
smoke. A timeout is an incomplete diagnostic result, never a workflow pass.

## Execution result, 2026-10-14

The timing correction (#335), fast gate (#336), and tiny fixture/checker with
dispatch limits (#337) are implemented in draft PR #311. At code revision
`b358a7bbe9f8859f2e253e83757dc142f5f91d64`, the expanded fast gate passed
**204 tests in 50.9 seconds**, with zero skips and no model calls. Both full-path
tests exercised all 29 stages. The [full CI run](https://github.com/boston-harness-group/refactory-dsh/actions/runs/38640612246)
also passed: 1,791 Python tests, 60 environment/integration skips, plus install,
TypeScript, web acceptance and harness checks.

One actual Sol 6/high tiny smoke ran after those checks. It completed eight
stages and stopped in `graph_explorer` after **121.5 seconds**, at the fixed
500,000-token limit: 501,032 metered tokens, two model CLI sessions, and 22
observed provider requests. The token ceiling had been calibrated before launch;
the 15-minute and 40-CLI-dispatch ceilings were unchanged. No retry followed.
The controller confirmed that its owned process group was empty, and terminal
files were preserved before the independent checker ran.

The bootstrap worker consumed 430,010 total tokens across 16 provider requests:
387,311 cache-read tokens, 34,440 cache-write tokens, 48 uncached input tokens,
and 8,211 output tokens. Its 19 tool calls included sequential edits and one
rejected edit after a shell copy, followed by the required destination read.
These are observed token categories, not a dollar-cost estimate. This result
shows that a tiny application alone does not make the full authoring workflow
inexpensive; repeated context and tool sequencing need attention.

No Package was delivered. The independent checker stopped on the missing
artifact, so none of the eight application cases ran. **Live completion remains
unproven and #337 stays open.** Before another live attempt, reproduce the
bootstrap tool sequence locally and establish a measured request/context budget.
Preserve every source responsibility and the failed trial; do not silently
increase the cap or restart the larger benchmarks. Public adoption remains held.
