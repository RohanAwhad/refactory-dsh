# 4. Ship it on DSH

The Python walker is a **debug runtime**. DSH is the runtime that ships. This
tutorial lowers a mode to refactory's IR, runs it on DSH's spine, and shows the two
agreeing.

A debug runtime that disagrees with the real one is worse than none, so the
comparison is a test, not a manual step.

## What "lowering" means

A mode is a `Graph` of `Node`s and `Edge`s. Refactory's own language has the same
vocabulary, and DSH's `workflow-spine` already executes it. So the seam is a
**translation of data**, not a reimplementation:

```bash
uv run python -c "
import json
from pathlib import Path
from refactory_dsh import lowering, registry

mode = registry.resolve(Path('.'), 'circle_packing/adaptive')[1]()
path, workflow = lowering.write_mode_graph(mode.state_dict(), Path('/tmp/tour/graphs'), name='circle_packing')
written = json.loads(Path(path).read_text())
print('nodes:', len(written['nodes']), 'edges:', len(written['edges']), 'start:', written['start_node'])
"
```

```
nodes: 8 edges: 8 start: task_input
```

Two details worth knowing:

- **The reference check is a test-time oracle, not a runtime step.**
  `tests/test_ir_parity.py` lowers every shipped mode through both `refactory_dsh/ir.py`
  and the reference and compares the bytes, so a graph that cannot lower is caught
  on the Python side. Install the reference with `uv sync --group reference`.
- **The IR has no field for the facts a node produces.** So
  `lowering.write_declarations(state, workdir)` writes them alongside the graph and
  the node runner reads them back from the run directory. That is a known wart of
  the boundary, not something to "clean up" silently.

## Run it on DSH

The `science` profile mounts the spine, so one command drives the whole graph:

```bash
node --import tsx/esm apps/cli/src/bin.ts --profile graph run \
  --mode circle_packing \
  --task "Call wf_next repeatedly until it reports done. Do nothing else." \
  --budget 1 \
  --graphs /tmp/tour/graphs \
  --workdir /tmp/tour/work
```

DSH runs every deterministic node itself. The CEO model is only there to call
`wf_next`; the ops run in-process against the same Python the walker uses.

> **Steering discovery.** The graph-startup plugin discovers the steer command
> through the `refactory.steer` Python entry point, using `--python-bin`'s
> python (defaults to `$VENV_BIN`, else `$REFACTORY_DSH_HOME/.venv/bin`, else
> `<cwd>/.venv/bin`). For a direct launch from the checkout root, no extra
> flags are needed; otherwise pass `--python-bin /path/to/.venv/bin`.

The result is written where a reader can find it:

```bash
cat /tmp/tour/work/facts.json
```

## The gate: both runtimes must agree

```bash
uv run python -m pytest refactory/tests/test_equivalence.py -q --run-integration
```

```
6 passed
```

That suite runs the same mode both ways and compares the score. It covers:

- the spine's own validator accepting the lowered graph;
- the same score from DSH and from the walker for the same seed;
- facts produced on disk by DSH becoming the same number through `score()`.

It is marked `integration` because it spawns a real session, which is why the plain
`pytest refactory/tests/` run reports it as skipped.

## Evaluating one candidate on DSH

Inside a loop you rarely want a whole run — you want one candidate scored by the
runtime that ships:

```python
from refactory_dsh.dsh_runtime import evaluate_on_dsh

result = evaluate_on_dsh(mode, state, workdir, timeout_s=900)
```

Then the loop selects its evaluator with a flag:

```bash
uv run refactory optimize circle_packing/adaptive \
  --epochs 1 --batch-size 2 --optimizer random --seed 5 \
  --evaluator dsh --runs-root /tmp/tour/dsh-run
```

`--evaluator walker` (the default) is fast and is for searching. `--evaluator dsh`
is the one that ships.

## The loop itself, on DSH

The loop is also a mode — `loop_mode/loop` — so the same machinery runs it:

```bash
uv run python -m pytest refactory/tests/test_loop_as_mode.py -q --run-integration
```

```
21 passed
```

Those tests drive the loop's eight steps as nine nodes on DSH's spine and assert
the gate routed `done` to the report, reaching the same best score as `Loop` does.

## What to take away

- The seam between the two runtimes is a **data lowering**, checked on both sides.
- The same fact has one home: the Python op writes it, DSH's node runner records
  it, and `score()` reads it either way.
- Anything that changes the executor must keep both runtimes agreeing, or the
  equivalence suite fails — which is the point of having it.

Next: [drive it from the GUI](05-drive-it-from-the-gui.md).

### One execution owns its run directory

`console run` and `ceo` keep the selected work directory exclusively owned from
before input/ledger preparation until their DSH process settles. A second run, or
`console open`, refuses an owned directory before archiving its invocation. Wait
for the owner to finish or choose another `--workdir`. Once it finishes, a new
invocation keeps the existing sequential archive behavior.

`.refactory/invocation.lock` is an inert rendezvous file for a local OS lock, not a
PID record. Leave it in place: normal exit, handled failure, and process exit
release the descriptor automatically, so an old file does not need manual stale
lock removal. On POSIX the owned DSH child inherits that descriptor; terminating
only its Python parent does not free the directory while DSH still runs.

This protects the Python launch/prepare boundary. `console open` alone owns only
its lowering call; a separate long-lived `wf_open` driver must manage its own
execution lifetime. It is not distributed locking, protection against external
file deletion, or general descendant-process containment. The Windows byte-lock
path protects the Python call; inherited-child behavior after abrupt parent exit
has only been established on POSIX. Timeout and direct interruption keep ownership until the launcher has awaited
the selected runtime's shutdown, as described below.


### Timeout and interruption await owned shutdown

On POSIX, `ceo` and `console run` request the selected DSH CLI's graceful shutdown
when their deadline expires or the calling thread is interrupted, and wait up to
ten seconds before forced-exit escalation. A deadline still fails; ordinary
nonzero child exits remain failures. An uncooperative runtime produces an explicit
forced-exit/cleanup-unconfirmed error. Files already written are not rolled back.

The console's intermediate Python owner participates in the same chain. Each
owned CLI has a separate session so terminal Ctrl-C is forwarded once, rather
than reaching DSH twice. On the main thread, a temporary handler converts default
SIGTERM into an unwinding exit and is restored afterward. Existing custom or
ignored handlers are preserved. Run-directory ownership remains held while this
cleanup is awaited, and normal terminal input/output stay inherited where they
were inherited before.

This is cleanup through the runtime's managed subprocess service, not signalling
arbitrary descendants. SIGKILL, worker-thread process-wide signals, custom signal
policies, Windows termination and escaped descendants still need their own
supervision. A CLI/runtime exit alone cannot certify arbitrary process-tree
quiescence. The actual acceptance check uses a real DSH-managed deterministic
child and unrelated sentinel; it is not a model or solver-performance result.
