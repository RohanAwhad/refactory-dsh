# Command reference

Start with [Get started](getting-started.md) or [Terminal and GUI](interfaces.md). All commands below run from the repository root. Examples with `<placeholders>` need your own values. Model-backed authoring and execution incur provider usage.


Every command is `uv run refactory <verb>`. `uv run refactory <verb> --help` is the current truth for one verb.

For `create_fast`, `edit_fast`, `design_fast`, focused QA, repair bounds and shared
elapsed/token controls, see [Fast modes and run budgets](budgets-fast-modes.md). Saved configurations use [optimization profiles](optimization-profiles.md); durable observation and explicit recovery use [live controls](live-control.md).

## Create and build

| command | what it does |
|---|---|
| `refactory ceo "<what you want>" --mode create --task <module:Class>` | build a harness for a class of work: the Task lists the items and judges each pass, and the produced package binds a `data` node to it. Research, a strategy you approve, a build, structural admission, registration and canonical smoke, then three parallel reviews and an audit; runtime depends on model latency and the number of review/repair cycles. |
| `refactory ceo "<what you want>" --mode create --single` | the special case: a harness that runs once, for one input, with no Task binding. |
| `refactory ceo "<what you want>" --mode design --check "<command>"` | design a checked artifact instead of a package; this is the default mode. |
| `refactory ceo "<what you want>" --mode edit --revise <package>/<mode>` | revise a mode; refuse a measured regression by default and report unavailable comparisons explicitly. |
| `refactory ceo <package>/<mode>` | run that mode instead of authoring anything. |

An edit inherits the source mode's Task and data-node requirements when no new
`task_ref` or `single_shot` knob is supplied. The final `create.json` describes the
emitted package's actual bindings, including `task_refs` and bindings per mode;
its legacy `task_ref` field is populated when there is exactly one distinct Task.

| option | what it does |
|---|---|
| `--mode <mode>` | `design` (the default), `create`, `edit`, `design_fast`, `create_fast`, `edit_fast`, or `<package>/<mode>`. |
| `--name <name>` | the package or artifact name; without it your words become the name. |
| `--check "<command>"` | run in the Design run's worktree, or its artifact directory; fast Create/Edit also accept a focused package check. Failures enter the bounded repair path. |
| `--repair-attempts <n>` | fast authoring repairs after the first implementation, from 0 to 3 (default 1). |
| `--source-project <dir>` | fast Design: copy an existing directory into the artifact directory and apply the change there, instead of working in a worktree; it takes no `--target`, `--base`, `--setup` or `--generate`. |
| `--target <repo>` | Design and fast Design: the git repository to change, given by its root. The run works in a worktree of it on its own branch, `refactory/run-<id>`, and your checkout is left as it is. Without it, the run starts a new project in `~/refactory-projects/<name>`. |
| `--base <rev>` | Design and fast Design: the commit, branch or tag to start from (default: the target's HEAD, or a new project's `main`). |
| `--setup "<command>"` | Design and fast Design: run in the worktree before each check, for example a build. |
| `--generate "<command>"` | Design and fast Design: run in the worktree after setup, before each check; whatever it changes in tracked files is amended into the attempt. |
| `--no-worktree` | Design and fast Design: write the artifact under `.factory/artifacts/<name>` instead of a worktree. |
| `--no-pr` | Design: keep the work on the run's local branch. Without it, a run whose reviews pass opens a pull request: into the target's branch its base came from on GitHub (`--base <branch>`, or the checkout's upstream; otherwise the default branch), or, for a new project, in a GitHub repository created for it then, private and under your gh account (`--knob repo_owner=<account>` and `--knob repo_visibility=public` change that). The approval gate shows which, in its question and in `delivery.md`; a run whose gate you answer up front with `--approve` says it when it starts, as `refactory: delivery — …`, and keeps to it. |
| `--refine <PR>` | Design: follow up an open pull request, given by its URL or `owner/repo#number`, with what you want changed as the brief. The run starts from the pull request's branch in a worktree of its own, goes through the full review, and pushes onto that branch, fast-forward only. It pushes nothing if the branch moved meanwhile, and never changes the pull request's title or description. Without `--target`, the clone to work in is found among `refactory projects`. |
| `--review-comments` | Design, with `--refine`: add the pull request's reviews and comments to the run's steering. |
| `--worker-backend dsh\|codex\|claude_code` | the explicit coding-worker runtime; DSH is the default. |
| `--effort <value>` | DSH reasoning-effort override; external workers use native product settings. |
| `--permission-mode <value>` | New workers default to `skip` (full native permission bypass). Choose `default` for native checks, or a supported backend-specific policy. Saved runs/profiles retain their policies; see [worker permissions](models.md#model-provider-versus-coding-agent). |
| `--max-elapsed-s <n>` / `--max-tokens <n>` | the shared resource allocation, including nested work and validation. |
| `--validation-reserve-s <n>` / `--validation-reserve-tokens <n>` | keep part of that allocation available for required final validation. |
| `--revise <package>/<mode>` | edit only: the mode being revised. |
| `--task <module:Class>` | create only: the class of work the produced harness binds a `data` node to. One of `--task` or `--single` is required. |
| `--single` | create only: build a harness for one run of one thing. |
| `--knob name=value` | set one of the authoring mode's knobs for this run, e.g. `allow_regression=1`. |
| `--workdir <dir>` | where the run's evidence lands (default `.factory/interactive/<package>-<mode>`). |
| `--provider <route>` | the provider route that model is on, e.g. `deepseek-official`. |
| `--model <id>` | the model this run uses, e.g. `deepseek-v4-pro`. |
| `--approve <gate>=<value>` | answer a gate up front, e.g. `gate_strategy=proceed`, for a run with nobody at it. In live mode, a user gate is shown at the terminal with its prompt and accepted verdicts; pick one and optionally add a multi-line note (end with a line containing only `.`) that is recorded as steering. Without `--approve`, run it and answer the gate when it reaches you. `modes` marks a person's gate `(user)`. |
| `--headless` | run without a conversation: one task, one report, then exit, for scripts and CI. When a headless run reaches a user gate, it prints the exact `refactory steer --run <dir> --verdict <gate>=<value>` and `refactory graph-control resume --run <dir>` commands. Without it the run is live — step progress prints as it happens during each turn, and a line you type between turns reaches the CEO. |
| `--timeout <seconds>` | bound the interactive graph process, including authoring; 0 supplies no separate wait limit. An explicit shared elapsed allocation can impose a shorter deadline. |

Fast Create and Design use one strategist, coder and QA agent sequentially. Fast
Design works where Design works, but has no approval gate, so it opens no pull
request and takes none of `--no-pr`, `--refine` or `--review-comments`: its work
stays on the run's branch, which its report names. Fast
Edit starts with the coder and retains source-contract checks. Canonical smoke precedes QA; repairable failures share the repair bound. `allow_regression=true` skips original execution and comparison, retaining revised-mode validation and provenance; false remains the default.
The CLI passes `--check` through the same declared `check` knob exposed in the
native form; use `--knob check_timeout_s=45` to bound that command separately.
These small graphs omit research and reviewer fan-out; [acceptance status](budgets-fast-modes.md#acceptance-status)
remains separate from selecting and launching them.

The full create and design QA gate uses current `BLOCKING: <finding>` statements from
all three review reports. `BLOCKING: none` and scoped empty verdicts such as
`BLOCKING: none in the current emitted artifact` are clean verdicts. A section
title such as `## 11. BLOCKING: findings` is not itself a finding; actual findings
under it still need their own `BLOCKING:` marker. `BLOCKING: none, except ...`
remains blocking. Fenced transcripts and sections headed `Previous review`,
`Superseded`, or `Archived review` are
history; a `Current findings` heading resumes current findings. The synthesis
preserves the full reports and their digests, so changing a report after synthesis
requires fresh synthesis before the gate can proceed.

## Manage packages of modes

| command | what it does |
|---|---|
| `refactory list` | every package and mode this checkout can run. |
| `refactory list modes --json` | the catalogue as JSON, with each mode's `action`, parameters and guidance. |
| `refactory package verify <package>` | import every mode and validate its graph. |
| `refactory package show <package>/<mode>` | one mode, described. |
| `refactory package tools <package>` | the tools it declares, and whether this machine has them. |
| `refactory package add <dir>` | install a package from a directory (`--as` to rename). |
| `refactory package import <dir-or-git-url>` | import one, recording where it came from (`--ref`, `--as`). |
| `refactory package remove <package>` | remove it. |
| `refactory package enable/disable <package>` | include it, or leave it out of the catalogue. |
| `refactory package smoke <package>/<mode>` | run it once, in a sandbox, without a model (free). |
| `refactory package smoke <package>/<mode> --model` | score a mode whose graph has an `agent` node, by supplying a model. |

Package import uses the literal `PACKAGE.name` when available, even if the source
folder has a different name. `--as` copies the package under a new identity and
rewrites its literal self-qualified Python imports, module references and mode
identifiers. Relative imports, comments, docstrings and external dependencies are
preserved. A rename requires a direct literal `PACKAGE = Package(name=...)` (a
positional literal name also works); dynamic identities and self-qualified
f-strings must be prepared explicitly before import. Provenance records the source
path or git URL/ref and original package identity. A refused import leaves no
installed copy.

`refactory package` also accepts `--json`, `--force` and `--project`.

## Use a mode

`refactory ceo <package>/<mode>` runs a mode you already have on DSH with your terminal attached, so a gate reaches you. It is the same `ceo` verb that authors a mode; a positional that names one is what says to run it. Only a whole `<package>/<mode>` identifier (lower-case letters, digits and underscores, each side starting with a letter, around one slash) counts. Anything else is a brief for the default design mode, including a brief that mentions a path, such as `"add a version flag to refactory/refactory_dsh/cli.py"`.

**A run is a live session.** Step transitions and the CEO's messages print as they happen during each turn, not only between turns; a gate a person owns is shown at the terminal with its prompt and accepted verdicts, and a line you type between turns reaches the CEO — which can amend a node's prompt and re-run it. `--headless` builds the older one-shot argv instead, for a script or CI, and then the run prints only its final JSON; when it stops at a user gate it prints the exact steer and resume commands.

| command | what it does |
|---|---|
| `refactory ceo <package>/<mode> --intent "<what it is for>"` | run the mode on DSH with your terminal attached, so a gate reaches you. |
| `refactory ceo <package>/<mode> --knob name=value` | set one of the mode's knobs for this run. |
| `refactory ceo <package>/<mode> --source <dir>` | read the items from here instead of the source the mode declares. |
| `refactory ceo <package>/<mode> --source-format directory\|jsonl\|csv` | how to read `--source` (default: a directory of one item per subdirectory). |
| `refactory ceo <package>/<mode> --workdir <dir>` | where the run's evidence lands. |
| `refactory ceo <package>/<mode> --timeout <seconds>` | how long to wait at a gate; `0` waits for you. |
| `refactory ceo <package>/<mode> --model <id> --provider <route>` | the model this run uses. |
| `refactory ceo <package>/<mode> --approve <gate>=<value>` | answer a gate before the run instead of at it, for a run nobody can ask. In live mode, a gate a person owns is shown at the terminal with its prompt and accepted verdicts; the person picks one. |

## Optimization

`refactory optimize <package>/<mode>` searches a mode's architecture and knobs: propose typed edits, validate, evaluate, keep what improves.

| option | what it does | default |
|---|---|---|
| `--epochs <n>` | rounds of propose → validate → evaluate → select | 2 |
| `--batch-size <n>` | candidates proposed per epoch | 4 |
| `--optimizer <name>` | how candidates are proposed: `random`, `hill_climb`, `evolutionary`, `archive`, `islands`, `map_elites`, `autoresearch` | `autoresearch` |
| `--strategy <name>` | which outer loop drives the search: `default`, `best_of_n`, `reflective`, `reflective_sampler`, `tree_search`, `staged_curriculum`, `staged_v1`, `adaptive_meta`, `autoscientists`, `research_then_optimize` | the harness's own (`default`) |
| `--evaluator walker\|dsh` | which runtime scores a candidate: fast in-process, or the one that ships | `walker` |
| `--preset <name>` | which edits are allowed: `broad`, `structure`, `conservative`, `knob_only` | `broad` |
| `--allowed-ops <name>` | narrow the chosen preset to listed edit operations; repeat for multiple names | the preset |
| `--frozen <name>` | additional knob protected from edits; repeat for multiple names | mode-declared frozen knobs |
| `--profile <name>` / `--profile-version <n>` | load an immutable saved configuration; explicit overrides remain visible in the snapshot | none / latest |
| `--repeats <n>` | independent trials per candidate and winner audit, from 1 to 10 | 1 |
| `--seed <n>` | seed the search RNG; model outputs can still vary | 0 |
| `--elite <n>` | how many parents each round proposes against | 3 |
| `--max-evaluations <n>` | a hard ceiling on evaluated candidates | 64 |
| `--holdout <fraction>` | the share of a Task's items kept out of the search and used to audit the winner afterwards; one item cannot be split, which the audit records | 0.3 |
| `--parsimony <cost>` | what each node of a candidate's graph costs its score: the search compares `raw_score − parsimony × nodes` | 0.01 |
| `--attempts <n>` | retries per batch after a refusal | 3 |
| `--runs-root <dir>` | the run directory itself; read it back with `--run <this path>` | `.factory/loop` |
| `--intent "<words>"` | what the run is for; seeds the ledger the agents read | empty |
| `--model <name>` | model for proposals and evaluated agent nodes; both evaluators carry the same routing context | the configured route |
| `--provider <route>` | the provider route that model is on | your settings default |
| `--worker-backend dsh\|codex\|claude_code` | worker runtime, independent of provider choice | `dsh` |
| `--role-routes <JSON>` / `--node-routes <JSON>` | route overrides; node takes precedence over role, then run | empty |
| `--max-elapsed-s <n>` / `--max-tokens <n>` | shared limits for proposals, trials, nested work and final validation | unlimited |
| `--validation-reserve-s <n>` / `--validation-reserve-tokens <n>` | part of the shared allocation reserved for validation | 0 |
| `--resume` | continue a compatible saved search and retain consumed resources | off |
| `--max-steps <n>` | step ceiling for one inner run, enforced by the executor | 2000 |
| `--history-limit <n>` | how many evaluated candidates the optimizer is shown; bounded so a prompt cannot grow without limit | 200 |
| `--prior-runs <n>` | how many previous runs are summarised as a prior for this one | 3 |
| `--dsh-timeout-s <n>` | wall-clock ceiling for one inner run when the evaluator is DSH | 900 |
| `--verbose` | print every event, and each candidate's own run output, not only the meaningful ones | off |

**For Task-bound modes, candidates use the training items and the winner is audited on held-out items.** Modes without a data split report holdout as not applicable. The part travels in the run directory (`subset.json` beside each candidate), not in a knob, so a search cannot choose its own exam; the audit lands in `<run root>/holdout.json` and in the report as `{"train", "holdout", "gap", "overfit", "threshold", "instances"}`, with `overfit` true when `train − holdout` exceeds 0.15. The score the search compares has the size cost taken off it, and the unpenalised number is recorded beside it as `raw_score`. `refactory best` reports the audit, and `refactory adopt` refuses an overfit winner unless you pass `--allow-overfit`.

## Read the run, and steer it

| command | what it does |
|---|---|
| `refactory runs` | every run under the root, newest first. |
| `refactory profiles` | list saved versions; `profile-save --name NAME --spec JSON` creates a new version. |
| `refactory profile-resolve <package>/<mode> --profile NAME` | preview the exact launch settings and fixed contract. |
| `refactory comparison --run <id>` | all measured, failed and unmeasured candidates, with trial/cost/latency evidence and audit. |
| `refactory status --run <id> --follow` | stream durable state changes without starting or owning work. |
| `refactory status --run <id>` | where it is, and the events since an offset. |
| `refactory leaderboard --run <id>` | candidates by score, each with its diff against its parent. |
| `refactory candidate <id> --run <id>` | one candidate, exactly what it changed. |
| `refactory best --run <id>` | the winner, with the state that scored it and the held-out audit of whether it works beyond its training items. |
| `refactory artifacts --run <id> --contents` | what it left on disk. |
| `refactory adopt --run <id> --as <package>` | write the winner as a new package, input untouched. |
| `refactory adopt --run <id> --as <package> --allow-overfit` | adopt it anyway, with the audit recorded beside the package. |
| `refactory steer --run <id> --note "<words>" --kind info\|correction\|constraint` | add what you said to the run's intent ledger. The proposer reads the ledger before every generation, so a note steers the next one. |
| `refactory steer --run <id> --verdict <gate>=<value>` | record a gate's answer, so a resume replays it. |
| `refactory steer --run <id> --pause` / `--resume` | hold a search at its next generation boundary, or release it. |
| `refactory steer --run <id> --stop` | end a search at its next boundary, recording that a person asked. |
| `refactory steer --run <id> --keep <candidate>` | follow that candidate up: it stays a parent even once a newer batch outscores it. Add `--lineage` for its descendants too. |
| `refactory steer --run <id> --reject <candidate>` | stop searching around that candidate: it leaves the leaderboard, so it cannot be proposed from, reported best or adopted. Add `--lineage` for the whole branch. |
| `refactory steer --run <id> --adopt [--as <package>]` | write the run's current winner as a package now, without ending the search. |
| `refactory stop --run <id>` | request interruption of its recorded owned process; the receipt distinguishes requested cancellation from confirmed termination. |

**A search says what it is doing while it does it.** `refactory optimize` prints the meaningful events as they happen — each candidate's score, the best so far, what the optimizer kept and refused with the running rule tally, each epoch's best, and the held-out audit — while the final JSON stays on stdout for a caller to parse. `--verbose` adds every event and each candidate's own run output, and `refactory status --run <id>` shows the same records, which is what the Web panel reads.

The read verbs pass through the console's own `--project`, `--runs-root`, `--after`, `--limit`, `--state` and `--contents` options.

## Everything else

| command | what it does |
|---|---|
| `refactory doctor` | check local configuration and missing setup; this does not test authentication or quota. Doctor reports which checkout the DSH graph profile's bundle comes from. If it names a different checkout, the profile was last provisioned by that checkout's `install.sh`. Run `install.sh` from the checkout you want to use, or run the `dsh plugin --profile graph add` command that doctor names. |
| `refactory help [--topic <topic>]` | the operating guide, and the current mode and operation schemas. |
| `refactory docs [--page <page>]` | search and read the documentation shipped with this checkout. |
| `refactory workers` | the coding-agent worker configuration and the capabilities each backend reports. |
| `refactory runtime` | the resolved runtime and model route a run would use. |
| `refactory projects [--json]` | the projects Design runs work on, with each run's branch and how it ended. |
| `refactory projects prune [<name>] [--succeeded]` | remove the worktrees and branches that ended runs no longer need; `--succeeded` also removes succeeded runs' worktrees, keeping their branches. |
| `refactory profile-save --name <name> --spec <JSON>` | save an immutable configuration version. |
| `refactory graph-control [--node <id>]` | control a graph run through the spine: pause, resume or stop it. |
| `refactory ui --port <n> --no-open` | start the [browser GUI](interfaces.md) without opening a browser. |


## Model selection and runtime limits

Read [Models](models.md) for managed credentials, resolved routing, worker receipts and unsupported backend capabilities. A random seed does not make provider responses deterministic. Candidate counts, elapsed time and tokens have separate limits; repeated trials and final holdout measurements consume the shared resource allocation. Token enforcement occurs at usage boundaries and can overshoot during an in-flight request.

For samples and resume on authoring APIs, use the documented Python Create/Edit/Design entry points and their `--help`; those flags are not all aliases on `refactory ceo`. [Authoring guide](tutorials/03-author-with-a-model.md).
