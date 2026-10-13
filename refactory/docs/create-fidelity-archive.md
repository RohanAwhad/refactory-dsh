# Native Create archival mapping

The pinned `3ab6f8f7ca792c874f2654574d7f7fd6dd18b896` archivist role says
to regenerate the performance report after archive notes, then attempt
MemPalace storage. These duties apply to Create even when no scored experiment
exists. Native `archivist_plan` and `archivist_build` keep their original model
role, graph IDs, nonblocking scheduling and markdown paths.

The actual archivist writes its complete note first, including the exact
finalization command and separate receipt path, then executes:

```sh
python -m refactory_dsh.create_fidelity_ops archive-update plan --project /absolute/run
python -m refactory_dsh.create_fidelity_ops archive-update build --project /absolute/run
```

It executes only the command for its assigned phase. The helper does not author
a missing note. It regenerates `.factory/performance_report.json` from complete
available strategy, observation, QA, gate and archive source bytes. Each input
has its own SHA-256. Recorded gate verdicts/pattern counts and observations are
derived from those records; they do not certify fresh approval or build
acceptance. No score, experiment ID or experiment total is invented. A failed
actual precheck remains visible in the report and build note.

Separate `.factory/archive/create-{phase}-receipt.json` records actual process
arguments, report inputs/digests, memory handling and the optional integration
attempt/errors. It points to immutable invocation report and receipt snapshots
under `.factory/archive/native-finalization/`. The helper never appends results
to the note whose bytes it already hashed. Revising a note requires another
finalization; earlier immutable receipts remain intact. Shared report updates
use a bounded interprocess lock, current-note rescans and atomic replacement.
A late plan update includes already available build observations. Only exact
helper-generated output paths in its validated index are excluded from archival
JSON inputs; arbitrary authored files in the same directories remain inputs.

Existing valid CEO memory arrays remain byte-for-byte unchanged. An absent
memory file is initialized to `[]`; malformed/non-array memory fails without
overwriting it. This one-plan helper adds no cross-cycle entries without actual
multi-experiment evidence. Experiment markdown/JSON sidecars and pattern rules
remain conditional on real applicable experiments, never a fabricated optimizer
record for creating an unscored mode.

The pinned MemPalace writer explicitly returns a no-op when the optional module
is absent. The native helper performs a real availability inspection and records
`attempted=true`, `status=not_installed` for that verified branch. Errors in the
inspection are unavailable. An installed module is also unavailable/nonzero:
complete project-owned storage support is not yet established. The reviewed
reference MemPalace 3.6.0 `KnowledgeGraph(db_path=...)` accepts a local SQLite
path, but the source writer additionally uses episodic storage, global locks,
embedding configuration and shared playbooks. The helper does not import or
execute that unknown installed integration, use a global home, or claim storage
succeeded. Supporting an installed backend needs separate proven project-owned
storage and actual tests before this boundary can be removed.

Archival `ok` means the report and applicable optional no-op were completed; it
does not override a failed precheck. Missing/unavailable/failing finalization
receipts cannot establish archivist semantic fidelity. Frozen v8/v9 snapshots
and their historical archives are unchanged; this mapping requires a fresh
source-bound authoring trial.
