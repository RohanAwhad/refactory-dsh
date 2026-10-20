# Replay harness

The replay harness scores a verifier implementation against the evidence log:
a set of known gaps that reviews missed, each with the method that found it.

## Running

```bash
python -m refactory_dsh.replay_harness --evidence entries.json --output baseline.json
```

### Input format

The `--evidence` file is a JSON array of entries. Each entry must have:

| Field | Type | Description |
|---|---|---|
| `id` | string | Unique entry identifier (e.g. `EL-001`) |
| `brief` | string | The original request / brief text |
| `gap_description` | string | What the gap is |
| `method` | string | The method that found it (e.g. `M1`, `M3`) |
| `diff` | string | The diff from the pre-fix commit |
| `expected_finding_pattern` | string | Substring to match in verifier findings |

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

- **found**: the verifier produced at least one finding that contains the
  entry's `expected_finding_pattern` (case-insensitive substring match).
- **missed**: the verifier produced no matching finding.
- **noise**: each blocking finding that does NOT match the expected pattern
  counts as one noise finding. Noise is reported but not marked wrong
  automatically.

### Committed baseline

The baseline for the current verifier is committed at
`refactory/tests/fixtures/verifier_baseline.json`. It records the model,
date and per-entry results from a real run over the evidence log.

### Unit tests

`refactory/tests/test_replay_harness.py` uses at least three evidence-log
entries as fixtures with a stubbed verifier, and asserts the harness counts
found, missed and noise correctly.
