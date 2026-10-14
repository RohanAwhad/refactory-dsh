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
