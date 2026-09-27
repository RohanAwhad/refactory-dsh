# CEO discovery and project-worker acceptance — 2026-09-27

This acceptance covers issues #253 and #254. It uses one new native CEO
conversation and one config-dispatched Codex worker. Existing adapter acceptance
in [release validation](release-validation-20260926.md) remains the evidence for
other backends; no three-backend or full-authoring campaign was repeated.

## Native run and identity

The run used the isolated `codex/ceo-discovery-project-workers` worktree based on
`8c3f2a61b738e8305e0d7b1ee10345b7deb91e3d`, with the implementation uncommitted.
Its returned build ID was
`3fe92e2189811c471ec44dae73e96ac644cff4f6e85935b40a82810c1759aaeb`;
documentation ID was
`188ca63a95177c6ace3e2d505551c714d963f050c0b9bcf0ef1791cf152ecbb9`.
Subsequent deterministic review fixes and reconciliation with merged authoring
changes change those IDs. This is evidence for the actual tested development
build, not a claim that a later commit ran another model acceptance.

The fixture `discovery_acceptance/worker_once` was installed after the build. It
has an `emit` agent (role `builder`) followed by a deterministic check requiring
`answer.txt` to contain exactly `42\n`. Live help listed it, including its missing
reviewed-guidance warnings. The project file contained:

```json
{"worker_backend":"dsh","node_routes":{"emit":{"worker_backend":"codex"}}}
```

The native CEO prompt required discovery through help, a mode-help read, a docs
search and page read, an effective-worker check, and one `wf_open` in a fresh
absolute work directory. It prohibited source archaeology, optimization, repair,
retry and out-of-graph delegation. No token cap was imposed. The CEO made ten
tool calls in eight steps: help, modes, mode help, docs search, workers, docs
page read, wf_open, wf_next, artifacts and wf_state. It used no shell, source
reader or web tool.

Session: `session-aa19f9b1-8682-4671-857c-db80ca251446`.
The CEO used the configured DeepSeek Flash selection. Summed assistant usage was
278,279 total tokens: 37,984 input, 238,080 cached input and 2,215 output;
643 reasoning tokens were reported within output. These figures are separate
from the native worker's usage.

Worker `worker-7c2827b6ce754da79d7d7729b4c61ef0` completed between
19:30:30.929 and 19:30:42.293 UTC. Its requested route was Codex with native
model/provider/effort selection and `skip` permissions. The actual native thread
reported `gpt-6-astra`, `openai`, `high`, Codex runtime `0.153.4`, adapter
`@deepseek-ai/dsh-subagent` `0.1.5-rc.3`, and 45,249 measured tokens. The measurement
excludes nested workers. There was one attempt. Independent file inspection and
`checked.json` verified exact `42\n` output.

## Terminal and panel

Public terminal calls read workers, mode help and the shipped routing page, then
prepared the same fixture with `console open --workdir <fresh-directory>`.
The generated node specification selected Codex from the same project map.
This was preparation evidence and did not launch a second model worker.

In native Chrome, the Modes panel selected the runtime-installed fixture. Its
**Mode help and effective project workers** disclosure returned the same mode,
build/docs identities and effective project defaults. Runtime controls showed
**Use project defaults**, inherited permission policy and no token limit.
Attaching the existing run showed finished/done, both nodes complete, the actual
Codex route and measured usage, and verified output facts. No second panel model
launch was claimed. Deterministic tests cover the shared preparation and panel
argument mapping.

The canonical `artifacts` list returned zero entries for the custom fixture's
noncanonical filenames. The graph's check, independent file inspection and panel
outputs established success; the CEO disclosed that artifact-list limitation.

## Checks and cleanup

Focused checks cover file/environment/profile/explicit precedence, backend
switches, role/node maps, invalid native fields, nested and saved-run isolation,
CLI/chat preparation, live package discovery, docs bounds and integrity, stale
coverage, broken links, generated chat-to-parser mappings and panel controls.
Final focused validation after reconciliation with main `364eeffc85` passed:

- 145 Python tests across discovery, project configuration, workers, interactive
  preparation, worker permissions, profiles, recovery and API docs.
- 164 TypeScript tests across the tool, generated invocation contract, profiles,
  body and steering ledger (including the independently merged panel changes).
- `pnpm run build`, including offline docs/coverage checks, host/client typechecks
  and both production bundles; normal commit/push hooks.

Exploratory wider runs also encountered existing failures. A detached baseline at
`3fd35ac7c9` reproduced two legacy graph-cycle assertions in `test_nn.py`, seven
service-mock failures in `service.spec.ts`, and three GUI-loop failures involving
sandbox PID inspection, missing-run wording and a missing `cve_patch_pack`
fixture. The wider tool-live run passed 98/100 checks, with a sandbox stop-status
failure and the same stale missing-run wording assertion. These are not a claim
that the entire repository test suite is green.

Private transcripts, terminal JSON, requested-route provenance and the plan are
retained under `/private/tmp/refactory-discovery-acceptance-20260927`; graph
artifacts remain in this worktree's `.factory/discovery-acceptance-20260927`.
The owned server on port 60213 was stopped after acceptance; the worker had
already completed, and its owned Chrome tab was closed. The temporary project file was copied to evidence and removed.
No old run was resumed, credential contents were published, or original tracked
checkout was modified.
