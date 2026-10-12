# Reviewed reference delta

Reference: `3ab6f8f7ca792c874f2654574d7f7fd6dd18b896`; historical comparison:
`3a74443e2b53dd55e847cea6d7af4b8d4865e279`. The graph was reconstructed from
an isolated git archive, yielding 29 nodes and 33 edges. Topology is unchanged.

* `health_checker` and `code_reviewer` now have explicit default review prompts.
* Strategists must specify an acceptance outcome, scope, and evidence for each
  hypothesis. Builders must deliver that evidence at or above the stated scope.
  Code review adds an eighth, mandatory acceptance-evidence category.
* Health checking adds an advisory patch-target audit. Findings do not alone
  change the gate verdict.
* Builder PR reuse/preservation instructions changed. Benchmark runs prohibit
  publishing generated projects, so this is an explicit artifact-only override
  in both lanes, not permission to publish test artifacts.
* Shared primitives validate function nodes and change DataNode topology and
  runtime inputs. Create has no DataNode; the native adapters must nevertheless
  validate generated modes against their own pinned runtime.

The graph fixture includes complete inherited role prompts, not only node text.
Known reference executor defects (including malformed bootstrap shell quoting)
are failures to record; they are not behavioral requirements to reproduce.
JSON director plans and prose quality summaries are separate artifacts in DSH.

No user patch is transplanted. The local patch inventory in `baseline.json`
records digests and disposition. Public Create remains the rollback baseline.

An additional optional IR-compatibility run against this exact remote snapshot
rejects DSH's existing `command_timeout_s` and `resource_role` serialization
fields. The same failure reproduces on the frozen `ec99b4ec` DSH baseline for
both counting modes. This is a pre-existing cross-library schema mismatch,
separate from the independent Create behavior contract; the compatibility path
is not claimed as verified. The installed-environment regression suite skips
that optional library unless it is explicitly provided.
