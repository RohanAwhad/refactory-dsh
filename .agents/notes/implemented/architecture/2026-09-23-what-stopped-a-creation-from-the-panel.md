# Agent Note: What stopped a creation from the panel

Status: implemented

English | [中文](2026-09-23-what-stopped-a-creation-from-the-panel.zh.md)

## Problem

Creating a mode from the Web GUI failed with `create failed (exit 2).` and nothing else. Six issues were filed from that one attempt, and each hid the next.

**The reason was destroyed on the way up.** The authoring entry points report a handled refusal as `{"error": …}` on stderr; `console._forward` captured stdout only, so the payload was `{"output": ""}` and the tool could only say "failed (exit 2)" (issue #61).

**The tool had no way to answer a person's gate.** `create`, `edit` and `design` pause at `gate_strategy`, a gate a person owns; the tool's schema had no verdict field, so a headless run from the GUI refused it, always (issue #60).

**Authoring runs were synchronous, capped at ten minutes, and unwatchable.** The console's timeout was 10 minutes in the tool and the run happened in-process, so a real creation — minutes of research and strategy before a line of code — was killed mid-flight with nothing to resume from and no run id to poll (issue #62).

**The research stage was never checked.** `research-plan.json`'s only contract was `writes=("research-plan.json",)`, which any bytes satisfy. Two independent runs wrote a prose status message about subagents "running in the background" and left `research/` empty, and the design proceeded on unverified assumptions (issue #63). The reason the subagents never wrote anything is the one that mattered: a node's model call runs on the one-shot profile, whose spawn subagent tool defaults to `backgroundMode: continuable` — so the agent ended its turn with its researchers still running, and the node's output was a sentence about them.

**The panel could optimize a mode but not use one.** The tool's only execute action was `start`, which is the outer loop; `refactory run` existed as a CLI verb with no console verb and therefore no tool action, so a person who had just created a mode could not run it once and see what it did. The console gained the `run` verb; [the panel can set a mode's knobs and use it once](2026-09-24-the-panel-can-set-a-modes-knobs-and-use-it-once.md) is where the gesture, the knob fields and the read-back were finished.

**Nothing checked that the mode did what the brief asked.** `validate` proved a package was structurally a mode: it imports, declares a valid graph, and satisfies the optional bindings it was given. A brief whose entire subject was a Python package produced a mode that took no package at all — it built a fixture hardcoded in `ops.py` — and passed every check. The pipeline had no representation of "the brief says this mode consumes X".

**A node's model call ran in the checkout.** The reviewing nodes are completions that spawn an agent with tools, and the completion ran with the checkout as its working directory, so those agents wrote `reviews/*.md` at the repository root while the run's own `reviews/` stayed empty. The QA synthesis reads the run directory, so it synthesized no reviews at all, and the overwatch gate sent the build back to `emit` — a reloop that cost fifteen minutes and looked like a model-quality problem. Found by the run that proved the other five fixes.

**Every creation shared one work directory.** `--workdir` defaulted to the fixed `.factory/create`, so a second run wrote its brief, research and strategy over the first one's, and a cancelled run's leftovers were what the next run read (issue #64).

**The emit prompt never said what a path was relative to.** A model that read "relative path" as relative to the checkout wrote `refactory/refactory_packages/<name>/mode.py`; `materialise` joined that onto the package root and nested the package inside itself, the `is_relative_to` guard passed because the nested path really is inside the root, and `validate` reported `__init__.py is missing` four directories away from the mistake — with a retry prompt that said nothing about paths, so the loop repeated it (issue #65).

## Decision

**A forwarded refusal keeps its words, and a failed forward is a failed call.** `_forward` captures stderr too, prefers the message inside the entry point's own `{"error": …}` line, and `console.main` returns 2 with the reason at the top level. The tool renders that reason instead of an empty payload.

**The tool forwards a person's gate answers.** `approve` is an array of `GATE=VALUE` on `create`/`edit`/`design`, passed as repeated `--verdict`. The action descriptions say the call cannot pause to ask, so the model asks the user for the plan approval first and passes it with the call. That is the whole capability: the command line and the console had it, the tool did not.

**A node's model call cannot background its own work.** `DshModelClient` runs the one-shot profile with a checked-in overlay (`refactory_dsh/patches/agent-node.yml`) that patches the spawn subagent tool to `backgroundMode: one-shot` and `enableRunInBackground: false` — the same decision the graph profile already made for the CEO, for the same reason. One node is one completion whose answer is the node's artifact.

**The research stage is validated, and its findings must exist.** `record_findings` writes each direction's finding to `research/<slug>.md` when no subagent left one, and `gate_research` requires a JSON array of `{focus, slug, finding}` with three to five non-empty, uniquely-slugged entries whose files are on disk. A failure writes `research-review.txt`, sets `research_review`, and reloops into the prompt that reads it — exactly what the design review does for the strategy.

**The tool can use a mode once.** A `run` console verb forwards to the interactive entry point (`refactory run`: the mode on DSH, the person answering any gate) and the tool gained a matching action, named like the CLI's rather than like the optimizer's. The one-shot runner prints its report from the child process — the terminal's stdout, not the console's buffer — so the console reads the run directory back and reports where the run is, how it ended and what it recorded.

**The brief's inputs are declarable, and the mode must have a way to receive them.** `--input NAME` (`--knob requires_input=…` from `ceo`, `inputs` from the tool) lists what the brief says the mode operates on. `validate` refuses a mode with no declared knob named after an input and no read under that name, the reason goes back into the emit prompt, and the design and emit prompts are told the list — the first bullet of the issue, which is the only half that can be checked mechanically. Whether the work *satisfies* the brief remains a review judgement: the strategy review already refuses a design that drops an ask, and the reviews read the brief.

**A node's model call runs in the node's own run directory.** `DshModelClient.complete` takes the directory to run in, the walker passes the node's run directory, and a caller with none gets the scratch directory rather than the checkout; the launcher is named absolutely now that the child's working directory is not the repository. The vendored `ModelClient` protocol stays text in, text out, so a client without the argument is asked without it.

**Authoring runs are isolated, watchable and given long enough.** The three entry points default their work directory to `.factory/create/<name>`, `.factory/design/<name>` and `.factory/edit/<name>`; the console forwards a `--workdir` when a caller gives one; `status` reads a run's `.refactory/spine-status.json` and reports the nodes done, the nodes due, the error and the run's own facts; and the console's timeout is 90 minutes, because the authoring verbs are the long ones — a real creation on this checkout took 48 minutes, which killed the first 10-minute ceiling mid-flight and would have killed a 45-minute one three minutes from the end.

**The emit prompt states the path convention, and a prefix that restates the package root is stripped.** Paths are relative to the package's own root — `__init__.py` means exactly `__init__.py` — and `_package_relative` strips a leading `<name>/`, `refactory/refactory_packages/<name>/`, `refactory_packages/<name>/` or `.factory/packages/<name>/`, refusing an absolute or escaping path by name. `sandbox.missing_init` now names the root it looked in, what that root holds, and the convention, so the retry can act on it.

## Consequences

The path a person takes from the panel — and the console path it forwards to — now either produces a package or says why not. Every fix has a test that fails without it: the stderr reason, the verdict argv, the authoring status shape, the research gate's four refusals, the per-name work directory, the stripped prefix, the actionable missing-manifest message, and the work directory a completion is told to run in.

The three changes that only a real run could have found came from one: the 45-minute ceiling was measured against a 48-minute creation, the foreground-only subagent patch was confirmed by five findings files appearing where two attempts had produced a sentence, and the completion's working directory was confirmed by `reviews/*.md` appearing at the checkout root. A `create` run through the console now ends with a validated, registered package (`buildroot_pack`, `smoke_score: 0.5`, 37 inner runs, 48 minutes). The tool's specs cover the GUI-facing half (verdict forwarding, the failure text, the status card, the timeout).

The library READMEs and the authoring tutorials say what changed where a reader meets it: the GUI tutorial now shows the approval and the status call, the authoring tutorial lists the research gate among the stages, and the README's shipped-modes list no longer claims `phase_pack`, which was only ever a package one machine had made.

**What remains.** An authoring run can be watched but not resumed: the run directory keeps everything, and re-running with the same `--workdir` starts over rather than continuing from the cursor. The issue asked for watch *or* resume, and watch is what this delivers.

## Alternatives considered

**Give the tool a `steer`-style verdict field and let the model decide when to ask.** Rejected as the whole answer: the call cannot pause, so a model that runs `create` first and asks afterwards has already failed the run. The description tells it to ask first, and `approve` is how the answer travels.

**Raise the timeout and leave the rest alone.** Rejected: the killed run in the report also had no way to see how far it had got, and the next thing a person asks after "it timed out" is "where was it".

**Make the tool start a background authoring run and return a run id.** Deferred, not rejected: it is the honest shape for a twenty-minute call, and it needs a job handle the console does not have yet. The run directory is the handle this change provides (`status --run <dir>`), and a resumed run is the next step.

**Guess the brief's inputs from its prose.** Rejected: a checker that reads English would pass a mode whose prose matched and fail one that said the same thing differently, and the failure would be unactionable. The person declares the name; the check is mechanical.

**Let `run` stream the child's output through the console's buffer.** Rejected: the console's contract is one JSON object on stdout, and the runner writes its report on the child's inherited stdout — capturing it would take the report away from a person running the console by hand. The run directory answers the same question durably.

**Validate the research plan at the prompt only.** Rejected: the prompt already asked for the array, and the model answered with prose twice. The gate is what makes the contract real, and it costs nothing.

**Wait for background subagents instead of forbidding them.** Rejected: an agent that ends its turn is not waiting, so "wait" would mean the runtime holding the turn open after the model said it was done — a runtime behaviour invented to paper over a profile default.

**Let `materialise` accept the nesting and look for the package deeper.** Rejected: it would make the on-disk layout depend on how a model phrased a path, and the same mistake would then reappear as a package whose modules do not import. Stripping the prefix that merely restates the root keeps the layout fixed and refuses what cannot be made package-relative.
