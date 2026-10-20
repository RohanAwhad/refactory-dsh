# Replay harness

The replay harness scores a verifier implementation against the evidence log:
a set of known gaps that reviews missed, each with the method that found it.

## Running

```bash
python -m refactory_dsh.replay_harness --evidence entries.json --output baseline.json --repo /path/to/repo
```

The `--repo` argument names the repository to create temporary worktrees from
each entry's `pre_fix_commit`. The harness checks out each commit, passes the
worktree path to the verifier as its third argument, and removes the worktree
afterwards. Entries whose commit is not reachable are skipped with a message.

### Input format

The `--evidence` file is a JSON array of entries. Each entry must have:

| Field | Type | Description |
|---|---|---|
| `id` | string | Unique entry identifier from the evidence log (e.g. `2`, `5`) |
| `brief` | string | The original request / brief text |
| `gap_description` | string | What the gap is |
| `method` | string | The method that found it (e.g. `M1`, `M3`) |
| `pre_fix_commit` | string | The commit hash before the fix was applied |
| `diff` | string | The diff from the pre-fix commit |
| `expected_finding_file` | string | File name that must appear in a matching finding |
| `expected_finding_terms` | list[string] | Key terms; at least one must appear alongside the file |

### Output format

The `--output` file is a JSON object:

```json
{
  "total_entries": 10,
  "found": 6,
  "missed": 4,
  "noise": 2,
  "per_method": {
    "M1": {"found": 2, "missed": 1, "noise": 1},
    "M3": {"found": 1, "missed": 0, "noise": 0},
    ...
  }
}
```

### Scoring

A finding matches when it mentions the entry's `expected_finding_file` AND
at least one of the `expected_finding_terms` (case-insensitive).

- **found**: the verifier produced at least one matching finding.
- **missed**: the verifier produced no matching finding.
- **noise**: each blocking finding that does NOT match counts as one noise
  finding. Noise is reported but not marked wrong automatically.
- **skipped**: the entry's pre-fix commit was not reachable in the repo.

### Committed baseline

The baseline for the current verifier is committed at
`refactory/tests/fixtures/verifier_baseline.json`. It has not been
measured yet: the PR ships the harness and fixtures, and the measured
baseline is produced after merge by running:

```bash
python -m refactory_dsh.replay_harness \
  --evidence refactory/tests/fixtures/evidence_log_entries.json \
  --output refactory/tests/fixtures/verifier_baseline.json \
  --model claude-sonnet-4-20250514 \
  --repo .
```

The `--model` argument is required: the replay harness needs a real
model-backed verifier to produce meaningful results. A stub that returns
no findings would write a baseline of all zeros, which is not useful.

Record the result on #400. Reviewers must not block on its absence.

### Unit tests

`refactory/tests/test_replay_harness.py` uses at least three evidence-log
entries as fixtures with a stubbed verifier, and asserts the harness counts
found, missed and noise correctly.
