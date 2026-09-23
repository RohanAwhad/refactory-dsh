# QA review: `blurb` / `writer`

Adversarial QA-director pass over the emitted package, run from the repo root with the repo venv.

- **Package under test:** `.factory/packages/blurb/` (`__init__.py`, `mode.py`, `ops.py`), emitted by the `create_blurb` run.
- **Harness:** `.factory/create_blurb/.qa/qa_checks.py` → `.factory/create_blurb/.qa/qa_checks.out`.
- **Command:** `.venv/bin/python .factory/create_blurb/.qa/qa_checks.py`
- **Environment:** Python 3.13 (`.venv`), keyless, no network, in-process scripted model client; a failing check never aborts the run.
- **Result:** **88 checks, 82 pass, 6 fail.** Every brief clause and every `chosen` / `why` / `will_not` claim in `strategy.json` is mapped to a check in the coverage table below.

The mechanical core is clean: the validator is green, the start node is the agent and the terminal is the deterministic op, the graph is exactly two nodes and one edge, the word/cliche semantics are exactly the strategy's, the score matches the strategy's expression at every point of its `0..3` domain, and the `max_words` knob is exact. The defects are outside that core: one functional brief clause, one acceptance artifact, and documentation drift.

## Verdict

BLOCKING: the topic named in the task never reaches the agent on any framework path. `Writer().evaluate(workdir, model=Scripted(...), task={"topic": "ZETA_TASK_7"})` with no out-of-band fact seed renders the literal `{{topic}}` into the captured model prompt, not `ZETA_TASK_7` (check D-8). The stock `WalkerEvaluator` does the same (check D-9). The prompt's advertised `topic.json` fallback has no producer anywhere under `refactory/` — no Python file names `topic.json` (check J-5). Only two out-of-band routes deliver a topic, neither the brief's `task`: a caller fact `facts={"topic": ...}` (check D-5) or a caller-seeded `intent-ledger.md` folded in by `steering.for_run`. No shipped evaluator, smoke, or the DSH path seeds either. The brief's "drafts a short product blurb about the topic named in the task" is therefore unmet on every framework-driven path. `strategy.json` concedes the point ("The topic slot is where the framework fights the brief") and ratifies the caller convention; the emitted package matches that convention, but the convention is not what the brief asks for.

BLOCKING: the run's own acceptance artifact cannot be executed and contradicts the given strategy. `strategy/acceptance-criteria.md` §J, run exactly as printed, dies immediately at `registry.load(PROJECT, NAME)` with `PackageError: cannot import package PosixPath('/Users/akash/cursor/refactory-fresh-test'): No module named 'refactory_packages./Users/akash/cursor/refactory-fresh-test'` — its arguments are swapped for the `load(name, project)` signature (check I-4). With only that call fixed to `registry.load(NAME, PROJECT)`, it dies at its own §C assertion because `graph.knob_consumers() == {'max_words': ['check', 'draft']}`, not the required `{'max_words': ['check']}` (check C-8b). `strategy.json` explicitly requires the `draft` node's `knobs == ["max_words"]`, so the sibling criterion and the strategy cannot both hold. There is no emitted-source defect here, but the run's acceptance evidence is broken and its two criteria sets are unreconciled, so this blocks the run rather than passing.

Non-blocking findings: emitted node `notes` are shorter than `strategy.json`'s (check K-3), the `0.19999999999999996` float for the over-limit / 3-cliche case (check K-4), and a caller-supplied `max_words` fact shadowing the knob in the rendered prompt while the check still enforces the knob (see "Non-blocking observations"). The keyless smoke's `exit_ok=False` is **not** a defect: it is the documented consequence of an agent start with no model, and both the strategy and the sibling artifact say it is not draft evidence (checks I-3, J-7).

## BLOCKING finding 1 — the task's topic never reaches the agent

**What I ran.** In one scripted-model run I passed the topic only through the channel the brief names, `task`, with no `facts`:

```python
Writer().evaluate(
    workdir,
    model=Scripted(" ".join(f"w{i}" for i in range(40))),
    task={"topic": "ZETA_TASK_7"},   # facts omitted on purpose
)
```

**What happened.** `result.exit_ok is True`, but the captured prompt is:

```
'Write a short product blurb about {{topic}}. If {{topic}} is still literal, read topic.json in the run directory and use the topic it names. At most 40 words. ...'
```

`ZETA_TASK_7` does not appear; the literal `{{topic}}` does. The same run through the stock evaluator fails identically:

```python
WalkerEvaluator(model=Scripted(...), project=PROJECT).evaluate(inst, inst.state_dict(), workdir)
# captured prompt contains '{{topic}}'
```

`render()` merges only `{**ctx.knobs, **ctx.facts}` (`refactory/refactory_dsh/nn/executor.py:170-186`); `ctx.task` is stored on the context but never substituted. The stock `WalkerEvaluator` calls `mode.evaluate(workdir, model=..., memory=..., max_steps=...)` and passes neither `task` nor `facts` (`refactory/refactory_dsh/evaluators.py:109-131`), so the loop never seeds one either.

**The fallback is inert.** The prompt tells the model to read `topic.json` "if `{{topic}}` is still literal". A repo-wide search of `refactory/**/*.py` for `topic.json` returns nothing: no code path creates that file. A caller would have to write it by hand; nothing in the harness does.

**What works.** Two out-of-band routes deliver something. First, when the caller *also* seeds the fact — `facts={"topic": "ZETA_TOPIC_9"}` — the sentinel appears verbatim in the prompt and the run scores the scripted draft correctly (check D-5, `PASS`). Second, a caller-seeded `intent-ledger.md` in the work directory is folded into the agent prompt by `steering.for_run` (observed: a ledger containing `ZETA_LEDGER_TOPIC` puts that string in the prompt). Both are caller interventions outside the `task`; the literal `{{topic}}` remains in the prompt in the ledger route, and `ZETA_TASK_7` appears in neither. So the wiring the strategy chose is real; the gap is the brief's `task` channel and every framework-driven path.

**Why this is written as blocking.** The brief requires an agent that drafts about "the topic named in the task". On the only paths the harness can drive, it cannot. `strategy.json` explicitly accepts this as a caller convention ("topic delivery is a caller convention (`facts={'topic': ...}`)"), and its own `why` says the framework fights the brief here; the package faithfully implements that concession. But a package whose central content is unknowable to the model in every shipped run does not satisfy the brief, and the strategy's workaround is not what a user of the mode gets.

**What would clear it.** Either fold `task` into the render values for an agent start (a harness change the strategy forbids), or add a producer that writes the topic to a fact/file before the agent (a node the strategy forbids), or have the mode itself read `ctx.task` in a pre-agent op (also forbidden). Under the strategy's own `will_not` list there is no allowed fix inside the package, which is the substance of the finding.

## BLOCKING finding 2 — the acceptance artifact does not run and contradicts the strategy

**What I ran.** The Python block in `strategy/acceptance-criteria.md` §J, extracted verbatim and executed with `.venv/bin/python`, and then the same script with only the rejected call repaired.

**What happened (as printed).**

```
File ".../registry.py", line 129, in load
    raise PackageError(f"cannot import package {name!r}: {exc}") from exc
refactory_dsh.nn.package.PackageError: cannot import package
PosixPath('/Users/akash/cursor/refactory-fresh-test'):
No module named 'refactory_packages./Users/akash/cursor/refactory-fresh-test'
```

`registry.load` is `load(name: str, project: Path | None = None)`; the artifact calls `registry.load(PROJECT, NAME)` (`acceptance-criteria.md` line 361), so the project path is used as the package name. The script never reaches an assertion.

**What happened (call repaired).**

```
File ".../acdoc_section_j_argfix.py", line 128, in <module>
    assert graph.knob_consumers() == {"max_words": [check.id]}, graph.knob_consumers()
AssertionError: {'max_words': ['check', 'draft']}
```

§C requires `graph.knob_consumers() == {"max_words": [<check id>]}`. The emitted graph has `draft` declaring `max_words` as well, because `strategy.json` says the draft renders `{{max_words}}` into its prompt and lists `knobs: ["max_words"]` on the `draft` node. `validate_state` is green either way (V7 only requires at least one consumer), and the emitted package follows the strategy; the sibling artifact's sole-consumer criterion is the incompatible one. Checks C-8 (strategy view, `PASS`) and C-8b (sibling view, `FAIL`) record both.

**Why this is written as blocking.** Not because the emitted source is wrong — it is not — but because the run's acceptance evidence is unusable: the artifact's own executable form cannot run, and when repaired it asserts a criterion the strategy forbids the package to satisfy. A create run gated on this artifact either cannot execute its proof or reloops on a conflict it cannot resolve.

## Acceptance-criteria coverage

Brief clauses (from `brief.json` / the task brief):

| # | Criterion | Checks | Result |
|---|---|---|---|
| BC1 | one mode, `writer` | A-3, A-4, A-5 | PASS |
| BC2 | graph starts with an AGENT node | C-2, J-1 | PASS |
| BC3 | agent drafts about the topic named in the task | D-5 (seeded fact) PASS, D-10 (intent ledger) PASS; **D-8, D-9 (task channel) FAIL**; J-5 (fallback) FAIL | **BLOCKING** |
| BC3 | draft is at most 40 words by default | D-4, F-9, G-9, H-2 | PASS |
| BC4 | a deterministic op then checks the draft | E-1, E-2, E-6, E-7 | PASS |
| BC4 | the check counts words | E-4, F-1 | PASS |
| BC4 | the check flags `revolutionary`, `game-changing`, `cutting-edge` | E-5, F-2, F-3, F-4 | PASS |
| BC5 | `score() == 1.0` when the check passes | G-1 | PASS |
| BC6 | `score() == 0.5` when only the word count fails | G-3 | PASS |
| BC7 | `score()` falls off with the number of cliches otherwise | G-4, G-5, G-6 | PASS |
| BC8 | one knob `max_words`, int, 20..60, default 40 | H-1, H-2, H-3, H-4, H-5 | PASS |

Strategy `chosen` / `why` / `will_not` claims:

| Strategy item | Checks | Result |
|---|---|---|
| minimum two-node graph, agent start → op terminal | C-4, C-5, J-2 | PASS |
| one unconditioned edge; no gate, reloop, finish, fork, join, data, port | C-5, J-2, C-11 | PASS |
| `max_words` is the only knob; `check` consumes it; `draft` renders it | H-1, D-3, D-4, C-8 | PASS |
| `score()` reads only produced `run.facts` (`within_limit`, `cliche_count`) | G-10, G-11, G-12, J-4 | PASS |
| `fact_produced` invariant over the two facts, and it rejects a graph that drops them | C-10, C-10b | PASS |
| topic is a caller-seeded `{{topic}}` fact; `topic.json` is the DSH fallback | D-5, D-10 PASS; J-5 **FAIL** (no producer) | **BLOCKING** |
| distinct case-insensitive word-boundary flags, domain 0..3 | F-2, F-3, F-4, F-5, F-6 | PASS |
| raw `len(text.split())`, no Markdown/punctuation/dash normalisation | E-4, F-1, F-7, F-8, J-8 | PASS |
| falloff base 1.0/0.5, −0.1 per cliche, both-fail strictly below within-limit and below 0.5 | G-5, G-6, G-7, G-8 | PASS |
| no harness-side task→fact mapping and no DSH runtime change | J-6 (package side); D-8/D-9 are the consequence | PASS (by design) |
| keyless smoke is not draft evidence | I-3, J-7 | PASS |
| `check` produces the four declared facts and returns facts only (no file) | E-2, E-3, E-7 | PASS |
| `check.call` resolves through the package's own ops module | A-6, B-4, C-9, K-2 | PASS |

Sibling `strategy/acceptance-criteria.md` criteria that the emitted package is checked against (beyond the strategy):

| Sibling item | Check | Result |
|---|---|---|
| §C `knob_consumers == {'max_words': ['check']}` | C-8b | **FAIL** (emitted `['check', 'draft']`; conflicts with `strategy.json`) |
| §J script runs as printed | I-4 | **FAIL** (`PackageError`, swapped `load` args) |

## Non-blocking observations

- **Node `notes` drift (K-3, FAIL).** The emitted notes are short; `strategy.json`'s notes are long. Example, `draft`: emitted `"Draft the blurb; one model call writes draft.md."` vs strategy `"the start node: one model call writes the single draft file. ..."`. The emitted text matches `design.md`, so this is strategy-vs-design documentation drift with no behavioural effect.
- **Float artifact (K-4, PASS-with-note).** `score(over-limit, cliche_count=3)` is `0.19999999999999996`, not the `0.2` printed in the strategy. The formula is exactly the strategy's `max(0.0, (1.0 if within else 0.5) - 0.1*C)`; every strict inequality in the strategy holds (0.19999999999999996 < 0.3 < 0.4 < 0.5 and < 0.7 < 0.8 < 0.9).
- **`max_words` fact shadows the knob in the prompt.** `render()` merges `{**ctx.knobs, **ctx.facts}`, so a caller passing `facts={"max_words": 999}` gets `"At most 999 words."` in the prompt while `check` still reads the knob (`max_words=40`) and computes `within_limit` at 40. Observed: prompt says `At most 999 words.`, run facts carry `max_words: 999`, `within_limit: True` for a 40-word draft. Not reachable through the strategy's `topic`-only convention, but it is a real way for the model's instruction and the check to disagree; seeding only `topic` (or not naming a knob as a bare fact) would remove it.
- **Caller-injected facts cannot forge the score.** Passing `facts={"within_limit": True, "cliche_count": 0}` into a 41-word run is overwritten by the op: observed `word_count=41, within_limit=False, cliche_count=0, score=0.5`. The check op is the only producer of those facts on the path.
- **Keyless smoke (not a defect).** `.venv/bin/python -m refactory_dsh.registry smoke blurb/writer --project .` exits 1 with `"draft: ExecutionError: agent node 'draft' needs a model client"` and `score: 0.0`; `sandbox.smoke` returns `ok=True` with `exit_ok=False`; the shipped `refactory package smoke` refuses with exit 2. This is the expected agent-start-no-model behaviour and is explicitly not draft evidence.
- **`cliche_terms` order.** The op returns terms in `CLICHES` order (`revolutionary`, `game-changing`, `cutting-edge`), matching the strategy's ops note "in `CLICHES` order" — not sorted. No criterion requires sorted.

## Registry CLI evidence

| Command | Observed |
|---|---|
| `.venv/bin/python -m refactory_dsh.registry verify blurb --project .` | `{"blurb": []}`, exit 0 |
| `.venv/bin/python -m refactory_dsh.registry list --json --project .` | contains `"blurb/writer"` |
| `.venv/bin/python -m refactory_dsh.registry show blurb/writer --project .` | contains `"max_words"`; agent start, op terminal, `scored: true` |
| `.venv/bin/python -m refactory_dsh.registry smoke blurb/writer --project .` | exit 1, `exit_ok: false`, `"agent node 'draft' needs a model client"` (expected) |

## Pipeline effect of this report

Replaying `create_ops.synthesize_qa` and `create_ops.gate_qa` on a copy of `reviews/health-check.md`, `reviews/code-review.md`, and this `reviews/qa.md` (in a scratch workdir) gives `qa_blocking: 1`, `qa_missing: 0`, then `gate_qa` returns `reloop` and writes `reloop: a BLOCKING finding`. The two `BLOCKING:` lines above are flush-left, so `synthesize_qa`'s `line.strip().startswith("BLOCKING")` predicate detects them; the `**BLOCKING:**` bold markers the earlier reviews used were invisible to that predicate.

## Checks deliberately not run (not claimed as passing)

- A real `refactory run` / DSH execution with a live provider or the TypeScript spine: the keyless environment has no provider and the lowering path was checked statically instead (I-5).
- The `create` end-to-end pipeline beyond this QA node.
- The optimizer/outer-loop over this mode.
- Any semantic judgement of the prose (whether it is a "product blurb"): only the deterministic word count and the three flags are scored, per the strategy; the topic-delivery interface is what was tested.

## Check-by-check exact results

Harness output, verbatim (`.factory/create_blurb/.qa/qa_checks.out`):

```text
== adversarial QA for .factory/packages/blurb ==
project=/Users/akash/cursor/refactory-fresh-test  root=/Users/akash/cursor/refactory-fresh-test/.factory/packages/blurb

[PASS] A-1 blurb imports and declares PACKAGE.name=blurb -- PACKAGE.name='blurb', modes=['writer']
[PASS] A-2 registry.where('blurb') is the workspace root -- where=/Users/akash/cursor/refactory-fresh-test/.factory/packages/blurb
[PASS] A-3 catalogue has exactly one blurb/writer, no sibling -- blurb modes=['blurb/writer'], siblings=[]
[PASS] A-4 one mode; PACKAGE.ids() == ['blurb/writer'] -- len(modes)=1, ids=['blurb/writer']
[PASS] A-5 resolve returns the writer mode -- resolved blurb/writer
[PASS] A-6 PACKAGE.ops is the package's own ops module -- ops=('refactory_packages.blurb.ops',)
[PASS] A-7 PACKAGE.requires == () -- requires=()
[PASS] A-8 registry.verify == {'blurb': []} -- registry.verify == {'blurb': []}
[PASS] A-9 provenance trust == generated -- trust='generated', origin='create-mode'
[PASS] B-1 exactly three Python files -- py files=['__init__.py', 'mode.py', 'ops.py']
[PASS] B-2 imports stay in stdlib + refactory_dsh + blurb -- imports=['__future__', 're', 'refactory_dsh', 'refactory_dsh.nn', 'typing']
[PASS] B-3 no random/uuid/clock/ambient input -- no random/uuid/clock/ambient-input tokens
[PASS] B-4 no foreign package names; call strings are blurb's -- no foreign package names; call strings=['refactory_packages.blurb.ops:check']
[PASS] C-1 validate_state == [] (V0..V12 + invariants) -- validate_state == []
[PASS] C-2 start is the single agent node (V1) -- start='draft' kind=agent
[PASS] C-3 every node reachable (V2) -- reachable == all nodes
[PASS] C-4 one terminal, the deterministic op (V3) -- terminals=['check'] (the op)
[PASS] C-5 minimum shape: 2 nodes, 1 edge, no gate/fork/join/data -- 2 nodes (agent, op), 1 unconditioned edge draft->check, no gate/fork/join/data
[PASS] C-6 check reads the agent's draft file (V6) -- agent.writes=('draft.md',), check.reads=('draft.md',)
[PASS] C-7 produces closure: scripted run exit_ok with no declaration error -- exit_ok=True, facts=['cliche_count', 'cliche_terms', 'draft_text', 'topic', 'within_limit', 'word_count']
[PASS] C-8 knob consumers (strategy wants draft+check) -- knob_consumers={'max_words': ['check', 'draft']} (strategy wants draft+check; sibling acceptance-criteria.md C wants check only)
[FAIL] C-8b sibling acceptance-criteria.md C: check is the sole consumer -- AssertionError: acceptance-criteria.md C requires {'max_words': ['check']}, got {'max_words': ['check', 'draft']}
[PASS] C-9 check.call resolves through nn.resolve (V8) -- resolve('refactory_packages.blurb.ops:check') -> refactory_packages.blurb.ops:check
[PASS] C-10 fact_produced invariant names within_limit + cliche_count -- invariants=['fact_produced'], fact_produced: graph produces ['within_limit', 'cliche_count']
[PASS] C-10b adversarial: invariant rejects a check that drops the facts -- invariant fires: ["I:fact_produced: the score reads ['within_limit', 'cliche_count'], which no node on a path from 'draft' to a terminal produces, so the number would not describe this mode's run; a producer that sits off every path the run can take does not count"]
[PASS] C-11 no ports (V11) -- ports == []
[PASS] C-12 score is not state; unknown state key raises -- unknown 'score' key raises StateError: unknown state dict field(s): ['score']
[PASS] D-1 agent prompt is non-empty and asks for a blurb -- prompt non-empty and names a blurb
[PASS] D-2 agent writes/produces exactly one thing each -- writes=('draft.md',), produces=('draft_text',)
[PASS] D-3 check consumes max_words -- check.knobs=('max_words',)
[PASS] D-4 prompt renders max_words (40 default, 20 when set) -- default prompt contains '40'; max_words=20 prompt contains 'At most 20 words'
[PASS] D-5 topic placeholder: seeded fact appears verbatim -- non-knob placeholders=['topic']; seeded sentinel appears verbatim
[PASS] D-6 scripted run writes draft verbatim with one model call -- model_calls=1; draft.md byte-for-byte equals the scripted text
[PASS] D-7 no-model run fails cleanly, score 0.0 -- exit_ok=False, score=0.0, error="draft: ExecutionError: agent node 'draft' needs a model client"
[FAIL] D-8 ADVERSARIAL: the task topic reaches the agent prompt (no fact seed) -- AssertionError: the task topic ZETA_TASK_7 does not reach the prompt; literal '{{topic}}' is present, prompt='Write a short product blurb about {{topic}}. If {{topic}} is still literal, read topic.json in the run directory and use the topic it names. At most 40 words. Use plain prose, with no headings, bullets, or emphasis. Do not use the words revolutionary, game-changing, or cutting-edge. Write only the blurb to draft.md.'
[FAIL] D-9 ADVERSARIAL: stock WalkerEvaluator delivers the topic -- AssertionError: WalkerEvaluator handed the model the literal {{topic}}: 'Write a short product blurb about {{topic}}. If {{topic}} is still literal, read topic.json in the run directory and use the topic it names. At most 40 words. Use plain prose, with no headings, bullets, or emphasis. Do not use the words revolutionary, game-changing, or cutting-edge. Write only the blurb to draft.md.'
[PASS] D-10 positive control: intent ledger folds a topic into the prompt -- ledger topic appears in the prompt; literal {{topic}} still present=True; task topic present=False
[PASS] E-1 check is an op with a resolvable call, no evaluator -- kind=op, call='refactory_packages.blurb.ops:check', evaluator=''
[PASS] E-2 check writes no file -- check.writes=() (fact-only)
[PASS] E-3 check produces word_count/cliche_count/cliche_terms/within_limit -- check.produces=['word_count', 'cliche_count', 'cliche_terms', 'within_limit']
[PASS] E-4 word_count == len(draft.split()) on probes -- word_count probes={'game-changing': 1, 'one,two': 1, 'a\nb\tc': 3, 'a b c': 3, '   ': 0, '': 0, 'revolutionary.': 1}
[PASS] E-5 cliche_count == distinct flagged terms on probes -- cliche_count probes={'revolutionary': 1, 'REVOLUTIONARY': 1, 'game-changing': 1, 'Cutting-Edge': 1, 'revolutionary game-changing cutting-edge': 3, 'revolutionary revolutionary': 1, 'counterrevolutionary': 0, 'cutting-edges': 0, 'game changing': 0}
[PASS] E-6 check deterministic across two runs -- two runs equal: word_count=40, cliche_count=1, score=0.9
[PASS] E-7 op writes no file (only draft.md in run dir) -- run-dir files=['draft.md']
[PASS] F-1 word semantics probes -- word_count probes={'game-changing': 1, 'one,two': 1, 'a\nb\tc': 3, 'a b c': 3, '   ': 0, '': 0, 'revolutionary.': 1}
[PASS] F-2 cliche semantics probes -- cliche_count probes={'revolutionary': 1, 'REVOLUTIONARY': 1, 'game-changing': 1, 'Cutting-Edge': 1, 'revolutionary game-changing cutting-edge': 3, 'revolutionary revolutionary': 1, 'counterrevolutionary': 0, 'cutting-edges': 0, 'game changing': 0}
[PASS] F-3 cliche matching is case-insensitive -- case variants all flag once: {'Revolutionary': 1, 'REVOLUTIONARY': 1, 'Game-Changing': 1, 'CUTTING-EDGE': 1}
[PASS] F-4 hyphenated terms matched whole -- cliche_count probes={'revolutionary': 1, 'REVOLUTIONARY': 1, 'game-changing': 1, 'Cutting-Edge': 1, 'revolutionary game-changing cutting-edge': 3, 'revolutionary revolutionary': 1, 'counterrevolutionary': 0, 'cutting-edges': 0, 'game changing': 0}
[PASS] F-5 word-bounded, not substring -- substring/plural probes all 0 when word-bounded
[PASS] F-6 distinct-term counting, never above 3 -- distinct counting: x3 same term -> 1; all three -> 3; domain 0..3
[PASS] F-7 space variants not flagged (will_not) -- space variants flagged 0 (no whitespace normalisation), per strategy will_not
[PASS] F-8 dash variants not flagged (will_not) -- en/em/non-breaking-hyphen variants flagged 0 (no dash normalisation)
[PASS] F-9 word-count verdict at n/n+1 for n=20,40,60 -- max_words n: exactly n within, n+1 over, for {20: (True, False), 40: (True, False), 60: (True, False)}
[PASS] G-1 clean pass is exactly 1.0 -- score=1.0
[PASS] G-2 mode is scored -- is_scored() is True
[PASS] G-3 word-count-only failure is exactly 0.5 -- score=0.5
[PASS] G-4 falloff with cliches within the limit -- within-limit scores k=1,2,3 = [0.9, 0.8, 0.7]
[PASS] G-5 both-fail strictly below 0.5 and below within-limit -- over-limit scores k=1,2,3 = [0.4, 0.3, 0.19999999999999996]; each < within [0.9, 0.8, 0.7] and < 0.5
[PASS] G-6 exact strategy score expression on domain 0..3 -- score matches the strategy expression at all 8 points: {(True, 0): 1.0, (True, 1): 0.9, (True, 2): 0.8, (True, 3): 0.7, (False, 0): 0.5, (False, 1): 0.4, (False, 2): 0.3, (False, 3): 0.19999999999999996}
[PASS] G-7 monotone in the word-count verdict -- word-count failure never raises the score above the same cliche count
[PASS] G-8 range [0,1] -- score in [0.0, 1.0] on the full domain
[PASS] G-9 knob sensitivity at the 20/60 bounds -- knob sensitivity: (20w,cap20)=1.0, (21,20)=0.5, (60,60)=1.0, (61,60)=0.5
[PASS] G-10 score reads facts, not the file -- deleting draft.md leaves score unchanged (0.9)
[PASS] G-11 score reads the run, not the mode -- max_words=20 and max_words=60 score the same RunResult identically (0.9)
[PASS] G-12 score is a method, never state -- unknown 'score' key raises StateError: unknown state dict field(s): ['score']
[PASS] H-1 exactly one knob named max_words -- knob_names == ['max_words']
[PASS] H-2 knob is int 20..60 default 40, not frozen -- (kind, lo, hi, default, frozen)=('int', 20, 60, 40, False)
[PASS] H-3 in-bounds coercion incl. integral float -- coerce(20,40,60,20.0)=[20, 40, 60, 20]
[PASS] H-4 out-of-bounds/bool/str coercion raises, no clamp -- coerce refusals: {'19': 'KnobError', '61': 'KnobError', 'True': 'KnobError', '40': 'KnobError'}
[PASS] H-5 state refuses out-of-bounds value -- out-of-bounds state raises StateError: knob 'max_words': 61 is above the upper bound 60
[PASS] H-6 knob consumed by the check op -- knob_consumers={'max_words': ['check', 'draft']} (strategy wants draft+check; sibling acceptance-criteria.md C wants check only)
[PASS] I-1 scripted end-to-end run -- exit_ok=True, model_calls=1, score=1.0
[PASS] I-2 no-model run fails visibly -- exit_ok=False, score=0.0, error="draft: ExecutionError: agent node 'draft' needs a model client"
[PASS] I-3 sandbox smoke ok=True with exit_ok=False (not draft evidence) -- ok=True, payload.exit_ok=False, payload.error="draft: ExecutionError: agent node 'draft' needs a model client"
[FAIL] I-4 acceptance-criteria.md J script runs as printed -- PackageError: cannot import package PosixPath('/Users/akash/cursor/refactory-fresh-test'): No module named 'refactory_packages./Users/akash/cursor/refactory-fresh-test'
[PASS] I-5 lowering: start agent, valid IR, prompt preserved -- start_node=draft, validate_ir=[], lowered prompt == mode prompt
[PASS] J-1 no deterministic op before the agent -- first/start node is the agent (node insertion order=['check', 'draft'])
[PASS] J-2 no gate/reloop/finish/fork/join/data/port -- no gate/reloop/finish/fork/join/data/port; two nodes, one edge
[PASS] J-3 no second knob/mode/seed/budget -- one knob, no memory, no ports
[PASS] J-4 score reads only run facts -- deleting draft.md leaves score unchanged (0.9)
[FAIL] J-5 the prompt’s topic.json fallback has a producer -- AssertionError: the prompt's topic.json fallback has no producer anywhere under refactory/: no .py file names topic.json
[PASS] J-6 workspace-only package, no runtime change -- origin='create-mode', trust='generated' (workspace-only; no runtime change)
[PASS] J-7 keyless smoke is not draft evidence -- ok=True, payload.exit_ok=False, payload.error="draft: ExecutionError: agent node 'draft' needs a model client"
[PASS] J-8 no Markdown/punctuation normalisation -- raw split(): '# Topic' -> word_count=2 (no Markdown normalisation)
[PASS] K-1 emitted agent prompt == strategy.json prompt -- agent prompt_template matches strategy.json exactly
[PASS] K-2 emitted check call/produces/knobs == strategy.json -- check call/produces/knobs match strategy: refactory_packages.blurb.ops:check
[FAIL] K-3 emitted node notes == strategy.json -- AssertionError: node notes differ from strategy.json: {'draft': ('Draft the blurb; one model call writes draft.md.', "the start node: one model call writes the single draft file. The topic arrives as the run fact {{topic}} because render() substitutes only knobs+facts; without that fact it degrades to the literal placeholder and the prompt's topic.json fallback rather than crashing."), 'check': ("Count the draft's words and flag the three marketing cliches.", 'deterministic terminal op. word_count = len(text.split()) on the raw draft; cliche_count = the distinct set of case-insensitive word-boundary matches of revolutionary, game-changing, cutting-edge (0..3), with cliche_terms recording which fired; within_limit = 1 <= word_count <= max_words. It writes no file and returns facts only.')}
[PASS] K-4 strategy decimals vs float formula output -- over-limit k=1,2,3 = {1: 0.4, 2: 0.3, 3: 0.19999999999999996}; k=3 is 0.19999999999999996, not the printed 0.2 (float artifact; strict inequalities hold)

RESULT: 82 pass, 6 fail, 88 total
```
