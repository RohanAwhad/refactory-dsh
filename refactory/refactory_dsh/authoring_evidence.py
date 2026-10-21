"""Current runtime receipts and requirement traceability for authoring.

The runtime checks identity, coverage and evidence references. Workers still own
semantic review: a well-formed evidence map is not proof of correctness.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any
import uuid

# Shell exit codes from the kernel, the OOM killer or the sandbox's own resource
# limits.  A test runner does not exit with these codes on purpose; classifying them
# as 'uncertain' avoids spending a code attempt on a resource kill.
_RESOURCE_EXIT_CODES: frozenset[int] = frozenset({
    128 + 9,   # SIGKILL — OOM killer, manual kill, cgroup limit
    128 + 24,  # SIGXCPU — RLIMIT_CPU exceeded
    128 + 25,  # SIGXFSZ — RLIMIT_FSIZE exceeded
})

from refactory_dsh.verification import M2_METHOD, M3_METHOD, M11_METHOD, M12_METHOD

REPO_CHANGE_REVIEW_INSTRUCTIONS = (
    "\n## Code-change review instructions\n"
    "This is a change to an existing repository. Read the diff file by file. For "
    "each changed function, trace the call path from each entry point the change "
    "touches. Verify the change is wired into the route a real run takes — not a "
    "step only one runtime reaches, a gate the fast mode no longer passes through, "
    "or a check reading the wrong file. Check correctness, edge cases, missing "
    "tests, scope creep and stubs. Confirm each new or modified function is "
    "reachable from the runtime's actual execution path, naming that path.\n\n"
    "Apply these verification methods to each changed function:\n"
    + M2_METHOD + "\n" + M3_METHOD + "\n" + M11_METHOD + "\n" + M12_METHOD + "\n\n"
    "## Repository questions\n"
    "For each function listed below (or, for a design review, each "
    "[changed] site), answer these three questions in a ```json fenced block with "
    'this shape: [{"function":"qualified.name", "duplicates":"where else the '
    'repository does the same thing, with path:line evidence", "callers":"who calls '
    "it and from which user path (the target's entry points: CLI commands, API "
    'routes, scheduled jobs, test harnesses), with path:line evidence", '
    '"shared_state":"what it shares with what already runs (state, files, facts, '
    'locks), with path:line evidence"}]\n'
    "Each answer gives path:line evidence, or states "
    '"absent: <what was searched>" when the answer is genuinely "nowhere" or '
    '"shares nothing". An entry with no path:line evidence and no absent: marker '
    "is incomplete.\n\n"
    "For every factual claim about existing code — whether it cites a file or not, "
    'including premises like "X calls Y", "this runs on every route", "the walker '
    'never does Z" — verify the claim at file:line. A false claim is BLOCKING.\n'
)

ARTIFACT_REVIEW_INSTRUCTIONS = (
    "\n## Artifact review instructions\n"
    "This is a standalone artifact or package. For visual deliverables, render "
    "actual representative output and inspect it at a useful size; record the "
    "renderer and image path. Text existing in SVG/XML does not prove it is "
    "visible or readable. For exchange-format files, use an established consuming "
    "application or independent format reader to open representative actual output "
    "and inspect imported units, structure, geometry and required semantics. "
    "Record normal loading, audit findings and recovery/repair separately. "
    "Your own tag parser, or rendering a paired format, does not establish "
    "interoperability. If rendering or visual inspection is unavailable, say so "
    "and leave the visual requirement unverified. If a required consumer check "
    "cannot run, name that unverified requirement rather than reporting a pass. "
    "Preserve the current artifact and evidence, name the missing validation "
    "dependency, and give the CEO or person the exact action needed to unblock it. "
    "Rewriting the artifact or repeating the same probe cannot supply an "
    "unavailable tool; do not prescribe those as a remedy. "
    "For visual output, exercise small and large dimensions and "
    "inspect rendered labels and bounds; distinguish geometry correctness from "
    "presentation quality.\n"
)

_PATH_LINE_RE = re.compile(r'\b[\w/._-]+\.\w+:\d+\b')

EVIDENCE = "authoring-evidence.json"
AUDIT = "intent-audit.json"
REVIEWERS = ("health_checker", "code_reviewer", "qa_director", "overwatch", "focused_qa")
INPUTS = ("brief.json", "strategy.json", "design.md", "implementation-plan.txt")
# Longer inputs are cited by path and sha256: the evidence is inlined into every reviewer prompt.
INLINE_LIMIT = 2048
SETTINGS = ("sample_knobs", "allow_regression", "check", "task_ref", "single_shot", "requires_input")

REVIEW_CONTEXT = (
    "\n## Authoritative validation and intent baseline\n{{runtime_evidence}}\n"
    "Large requirement inputs (design, strategy, plan) are referenced by path and "
    "sha256 above rather than inlined. Read those files from the run directory and "
    "read authoring-evidence.json for the full validation receipt.\n"
    "Prior validation/review diagnostics (resolve any evidence-format errors in your own report): "
    "{{repair_feedback}}\n{{qa_gate}}\n{{overwatch_review}}\n"
    "The runtime has already executed canonical smoke/acceptance on this revision. "
    "Read its receipt and actual output files; do not repeat a full model/Task feed. "
    "Use targeted spot checks only for a concrete uncovered requirement. A numeric "
    "score, source inspection and requirement coverage are different evidence. "
    "The brief, accepted criteria/design and current user intent ledger are binding. "
    "Only explicit user changes or exclusions can alter them; an author, coordinator, "
    "reviewer or README cannot waive an ask. Missing required evidence is failed or "
    "unverified, never pending-but-passed. Do not modify the implementation. "
    "If a required tool/credential is unavailable, write `UNAVAILABLE:` with the "
    "dependency and exact unblock action; do not request recoding or unchanged retries.\n"
)

# A full graph's repair writes its findings to `validation` (create_ops.repair);
# `repair_feedback` is the fast graphs' name for the same thing and is never set
# in a full graph, so its reviewers would otherwise see a raw `{{repair_feedback}}`.
FULL_REVIEW_CONTEXT = REVIEW_CONTEXT.replace("{{repair_feedback}}", "{{validation}}")

AUDIT_INSTRUCTIONS = (
    "\nWrite intent-audit.json as {\"requirements\":[{\"id\":\"baseline id\", "
    "\"requirement\":\"the concrete ask(s) checked\", \"status\":\"pass|fail|unverified|excluded\", "
    "\"clauses\":[{\"clause\":\"one distinct obligation\", \"evidence\":[{\"path\":\"file\", \"detail\":\"finding\"}]}], "
    "\"evidence\":[{\"path\":\"actual file path\", \"detail\":\"command/output or precise finding\"}], "
    "\"reason\":\"explanation\", \"user_change\":\"ledger id or brief, only for an explicit user exclusion\", "
    "\"user_quote\":\"exact text of that explicit exclusion\"}]}. "
    "For every constraint or correction row, list each distinct clause — every separate "
    "obligation in the text — in the \"clauses\" array, each with its own evidence from "
    "the diff. Omitting a clause that is in the text, or listing a clause with no "
    "evidence, is a structural failure. "
    "Cover EVERY baseline id in authoring-evidence.json, decomposing compound asks "
    "into separate rows with the same id when needed. Map every ask, accepted criterion, "
    "design obligation and current ledger instruction to actual current evidence. "
    "Evidence paths are relative to the run directory or absolute delivered file paths. "
    "Cite existing individual files, never directories, line-number suffixes or intent-audit.json itself. "
    "Write every probe fixture you create into the run directory, under a path "
    "namespaced to your own node (for example `scratch/<node id>/<name>`), never /tmp "
    "or any other outside location and never a peer's shared name: concurrent "
    "reviewers stage probes beside each other, so a flat shared name can be clobbered "
    "by a peer. The audit accepts only files inside the run "
    "directory or the delivered artifact, so a fixture kept elsewhere cannot be cited and "
    "its verification is lost. "
    "A pass needs a supporting file and specific evidence; an exclusion needs the "
    "actual explicit user change id, exact user_quote and reason. Report omissions as fail/unverified "
    "with precise reasons, even if other reviewers passed.\n"
    "If authoring-evidence.json contains a non-empty \"process_notes\" list, write a "
    "\"process_notes\" key in intent-audit.json as [{\"id\":\"ledger-N\", "
    "\"text\":\"the directive\", \"followed\":true|false, "
    "\"detail\":\"where in the run artifacts this was or was not followed\"}]. "
    "Cite run artifacts other than intent-audit.json. Process notes are not "
    "baseline requirements and never block the gate; this section is informational "
    "so a person reviewing the run can see whether each directive was honoured.\n"
)


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def _committed_source(root: Path) -> dict[str, str]:
    """A run worktree's identity: the attempt it holds, as committed (#351).

    What the run delivers is its branch, so build output, installed environments, tool
    caches and anything a check rewrites on disk leave a sign-off current, while a new
    attempt -- a new commit -- makes it stale (the review comment on #330: identity
    must not count `lib/`, `dist/`, ...).
    """
    from refactory_dsh import worktree

    return {name: blob for name, blob in worktree.committed(root).items()
            if not any(part.startswith(".env") for part in Path(name).parts)}


def source_files(root: Path) -> dict[str, str]:
    from refactory_dsh import worktree

    if worktree.is_run_worktree(root):
        return _committed_source(Path(root))
    ignored = {".git", ".factory", ".venv", "node_modules", "__pycache__", ".pytest_cache", ".origin.json"}
    files = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if any(part in ignored or part.startswith(".env") for part in relative.parts):
            continue
        if path.is_symlink():
            raise ValueError(f"cannot attest a symlink in the delivered source: {relative}")
        if path.is_file():
            files[str(relative)] = hashlib.sha256(path.read_bytes()).hexdigest()
    return files


def intent(workdir: Path) -> list[dict[str, Any]]:
    from refactory_dsh.steering import Ledger

    # Gate visits/consumption are bookkeeping, not later user instructions.
    result = []
    for entry in Ledger(workdir).entries():
        if entry["kind"] not in ("Initial Idea", "info", "correction", "constraint", "verdict"):
            continue
        if entry["kind"] == "verdict":
            # Strip the consumed field so marking a verdict used does not change
            # the identity digest (which current_problems compares).
            entry = {**entry, "text": "\n".join(
                line for line in entry["text"].splitlines()
                if not line.startswith("consumed:")
            )}
        result.append(entry)
    return result


def identity(workdir: Path, root: str) -> dict[str, Any]:
    return {"root": root, "files": source_files(Path(root)), "intent": intent(workdir),
            "inputs": {name: hashlib.sha256((workdir / name).read_bytes()).hexdigest()
                       if (workdir / name).is_file() else None for name in INPUTS}}


def requirements(ctx: Any) -> list[dict[str, str]]:
    result = [{"id": "brief", "text": str(ctx.facts.get("brief") or ctx.read_text("brief.json"))}]
    for name, key in (("strategy.json", "strategy"), ("design.md", "design"),
                      ("implementation-plan.txt", "plan")):
        if not ctx.exists(name):
            continue
        raw = ctx.read_text(name)
        if len(raw) > INLINE_LIMIT:
            sha = hashlib.sha256(raw.encode()).hexdigest()
            result.append({"id": key, "path": name, "sha256": sha})
        else:
            result.append({"id": key, "text": raw})
        if key == "strategy":
            try:
                strategy = json.loads(raw)
            except ValueError:
                strategy = {}
            if isinstance(strategy, dict):
                criteria = strategy.get("acceptance_criteria", [])
                if isinstance(criteria, list):
                    result.extend({"id": f"criterion-{index + 1}", "text": str(item)}
                                  for index, item in enumerate(criteria))
    for index, entry in enumerate(intent(ctx.workdir)):
        if entry["kind"] in ("correction", "constraint"):
            if entry.get("scope") == "process":
                continue
            # C5: CEO notes are context, not auditable requirements.
            if entry.get("source") == "ceo":
                continue
            result.append({"id": f"ledger-{index + 1}", "text": entry["text"], "kind": entry["kind"]})
    return result


def record(ctx: Any, outcome: dict[str, Any]) -> dict[str, Any]:
    root = str(ctx.facts.get("artifact") or ctx.facts.get("package_path") or "")
    if not root or not Path(root).is_dir():
        raise ValueError("runtime evidence needs the delivered package/artifact directory")
    reqs = requirements(ctx)
    process_notes = [
        {"id": f"ledger-{index + 1}", "text": entry["text"], "kind": entry["kind"], "scope": "process"}
        for index, entry in enumerate(intent(ctx.workdir))
        if entry["kind"] in ("correction", "constraint") and entry.get("scope") == "process"
    ]
    payload = {"schema": 1, "id": uuid.uuid4().hex,
               "identity": identity(ctx.workdir, root), "requirements": reqs,
               "process_notes": process_notes,
               "settings": {key: ctx.knob(key, None) for key in SETTINGS},
               "receipts": {name: hashlib.sha256((ctx.workdir / name).read_bytes()).hexdigest()
                            for name in ("smoke.json", "revision.json", "focused-check.json", "check.txt", "test-guardedness.json")
                            if (ctx.workdir / name).is_file()},
               "outputs": runtime_outputs(ctx), "validation": outcome}
    ctx.write_json(EVIDENCE, payload)
    # Immutable historical receipts survive repairs and explicit runtime redo.
    history = ctx.workdir / ".refactory" / "validation-history" / payload["id"]
    history.mkdir(parents=True, exist_ok=True)
    (history / EVIDENCE).write_text(json.dumps(payload, indent=2) + "\n")
    for name in ("smoke.json", "revision.json", "check.txt", "focused-check.json", "test-guardedness.json", AUDIT):
        path = ctx.workdir / name
        if path.is_file():
            (history / name).write_bytes(path.read_bytes())
    ctx.set_fact("runtime_evidence", json.dumps(payload))
    return payload


def runtime_outputs(ctx: Any) -> dict[str, Any]:
    """Bind the canonical execution's actual files as well as its JSON receipt."""
    smoke = ctx.read_json("smoke.json", {}) if ctx.exists("smoke.json") else {}
    root = Path(smoke["workdir"]) if smoke.get("workdir") else None
    if root is None or not root.is_dir() or not root.resolve().is_relative_to(ctx.workdir.resolve()):
        return {}
    return {"root": str(root), "files": source_files(root)}


def delivery_identity(workdir: Path) -> dict[str, str] | None:
    path = workdir / EVIDENCE
    if not path.exists():
        return None
    evidence = json.loads(path.read_text())
    return {"receipt": digest(evidence),
            "identity": digest(identity(workdir, evidence["identity"]["root"]))}


def current_problems(ctx: Any, workers: tuple[str, ...] = ()) -> list[str]:
    """Legacy reports remain readable; new evidence cannot authorize old sign-off."""
    if not ctx.exists(EVIDENCE):
        return ["authoritative runtime evidence is missing"] if ctx.facts.get("runtime_evidence") else []
    evidence = ctx.read_json(EVIDENCE, {})
    now = delivery_identity(ctx.workdir)
    problems = []
    if now is None or now["identity"] != digest(evidence.get("identity")):
        problems.append("artifact, accepted inputs or user intent changed after validation; rerun validation and reviews")
    if evidence.get("validation", {}).get("status") != "pass":
        problems.append("required runtime validation did not pass")
    if evidence.get("settings") != {key: ctx.knob(key, None) for key in SETTINGS}:
        problems.append("validation inputs or comparison policy changed after execution")
    for name, expected in evidence.get("receipts", {}).items():
        path = ctx.workdir / name
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            problems.append(f"authoritative receipt {name} changed after validation")
    if "outputs" in evidence and evidence["outputs"] != runtime_outputs(ctx):
        problems.append("canonical runtime output files changed after validation")
    if workers:
        from refactory_dsh.agent_delivery import receipts_in

        receipts = receipts_in(ctx.workdir)
        for worker in workers:
            receipt = receipts.get(worker, {})
            if (receipt.get("status") != "delivered" or not receipt.get("current")
                    or receipt.get("execution", {}).get("authoring") != now):
                problems.append(f"{worker} has no current delivery/sign-off for this artifact, intent and runtime receipt")
    return problems


def audit_problems(ctx: Any) -> list[str]:
    if not ctx.exists(EVIDENCE):
        return []
    evidence = ctx.read_json(EVIDENCE, {})
    try:
        audit = ctx.read_json(AUDIT, {})
    except ValueError:
        audit = {}
    rows = audit.get("requirements", []) if isinstance(audit, dict) else []
    baseline = {item["id"]: item for item in evidence.get("requirements", [])}
    changes = {key for key, value in baseline.items() if value.get("kind") in ("correction", "constraint")}
    changes.add("brief")
    covered = set()
    problems = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str) or row["id"] not in baseline:
            problems.append("intent audit contains an unknown requirement id")
            continue
        key = row["id"]
        covered.add(key)
        if row.get("status") == "excluded":
            change = row.get("user_change")
            quote = row.get("user_quote")
            if (not isinstance(change, str) or change not in changes or not row.get("reason") or not isinstance(quote, str)
                    or not quote.strip() or quote not in baseline.get(change, {}).get("text", "")):
                problems.append(f"{key}: exclusion has no explicit user change; reviewers cannot waive requirements")
            continue
        # ── clause-level check for constraint/correction rows ──
        base_item = baseline.get(key, {})
        if base_item.get("kind") in ("correction", "constraint"):
            clauses = row.get("clauses")
            if not isinstance(clauses, list) or not clauses:
                problems.append(f"{key}: constraint/correction row has no clauses; "
                                "list every distinct obligation from the text")
                continue
            for ci, clause in enumerate(clauses):
                if not isinstance(clause, dict) or not clause.get("clause"):
                    problems.append(f"{key}: clause {ci + 1} has no text")
                    continue
                clause_evidence = clause.get("evidence")
                if not isinstance(clause_evidence, list) or not clause_evidence:
                    problems.append(f"{key}: clause {ci + 1} ({clause.get('clause', '')!r}) has no evidence")
        refs = row.get("evidence")
        if row.get("status") != "pass" or not row.get("requirement") or not isinstance(refs, list) or not refs:
            problems.append(f"{key}: required evidence is failed or unverified: {row.get('reason', 'no supporting evidence')}")
            continue
        for ref in refs:
            cited = Path(str(ref.get("path", ""))) if isinstance(ref, dict) else Path()
            roots = (ctx.workdir.resolve(), Path(evidence["identity"]["root"]).resolve())
            # A relative path names a file in the run directory or in the artifact: a
            # reviewer of a run in a worktree cites the repository's files as `src/x.py`.
            path = next((candidate for candidate in ((root / cited).resolve() for root in roots)
                         if candidate.is_file()), (ctx.workdir / cited).resolve())
            if (not isinstance(ref, dict) or not ref.get("detail") or not path.is_file()
                    or not any(path.is_relative_to(root) for root in roots)
                    or path.name == AUDIT):
                problems.append(f"{key}: missing or invalid evidence reference {str(path)!r}; "
                                "cite an existing individual file within the run or delivered artifact, with detail")
    problems.extend(f"{key}: no requirement evidence in intent audit" for key in sorted(set(baseline) - covered))
    return problems


def check_failure_kind(
    result: Any,
    *,
    budget: Any = None,
) -> str:
    """Classify a check's failure from structural signals, never from its output.

    A check's stdout and stderr are the target project's text.  Word-scanning
    them for ``credentials``, ``ModuleNotFoundError`` or ``timed out``
    misclassifies ordinary test failures.  Only exit code, timeout flag, signal
    and budget state decide the kind.
    """
    if budget is not None:
        snapshot = budget.snapshot() if hasattr(budget, "snapshot") else {}
        if snapshot.get("exhausted"):
            return "resource_exhausted"
    if getattr(result, "timed_out", False):
        return "uncertain"
    rc = getattr(result, "returncode", None)
    if rc is not None and rc < 0:
        return "uncertain"
    if rc is not None and rc in _RESOURCE_EXIT_CODES:
        return "uncertain"
    # Preparation step failure (install, generate, setup) — structural.
    prep = getattr(result, "prep_failure", None)
    if isinstance(prep, dict):
        if prep.get("timed_out"):
            return "uncertain"
        prep_rc = prep.get("returncode")
        if isinstance(prep_rc, int) and prep_rc < 0:
            return "uncertain"
        if prep_rc in (126, 127):
            return "unavailable"
    if rc in (126, 127):
        return "unavailable"
    return "code_failure"


def failure_kind(detail: str, *, timed_out: bool = False, returncode: int | None = None) -> str:
    lowered = detail.lower()
    if any(word in lowered for word in ("budget exhausted", "resource exhausted", "reserve retained", "budgetexhausted")):
        return "resource_exhausted"
    if "cancelled" in lowered or "canceled" in lowered:
        return "cancelled"
    if timed_out or "timed out" in lowered or "timeouterror" in lowered or "killed by sig" in lowered or returncode is not None and returncode < 0:
        return "uncertain"
    if re.search(r"ModuleNotFoundError: No module named ['\"]refactory_packages\.", detail):
        return "code_failure"
    if (returncode in (126, 127) or re.search(r"UNAVAILABLE:", detail, re.I)
            or any(word in lowered for word in ("credentials", "api key", "unauthorized", "no model",
                                                "modulenotfounderror", "command not found", "model completion failed"))):
        return "unavailable"
    return "code_failure"


def stop_unrepairable(kind: str, detail: str) -> None:
    if kind != "code_failure":
        raise ValueError(f"validation {kind}: {detail}. Preserve the artifact; "
                         "restore the named dependency/credentials or inspect worker/resource state before explicit recovery; code repair was not attempted")


def wire_reviews(graph: Any) -> None:
    """The full graphs retain distinct health, code, adversarial and audit roles."""
    from dataclasses import replace

    # A repair must re-enter work admission rather than spend validation reserve.
    graph.nodes["emit"] = replace(graph.nodes["emit"], resource_role="work")
    for name in ("fork_qa", "join_qa", "synthesize_qa", "gate_qa", "gate_overwatch", "report"):
        graph.nodes[name] = replace(graph.nodes[name], resource_role="final_validation")
    for name in REVIEWERS[:-1]:
        node = graph.nodes[name]
        audit = name == "overwatch"
        graph.nodes[name] = replace(
            node, prompt_template=node.prompt_template + FULL_REVIEW_CONTEXT + (AUDIT_INSTRUCTIONS if audit else ""),
            reads=(*node.reads, EVIDENCE), writes=(*node.writes, AUDIT) if audit else node.writes,
            resource_role="final_validation",
        )
    for name in ("gate_qa", "gate_overwatch"):
        node = graph.nodes[name]
        graph.nodes[name] = replace(node, reads=(*node.reads, EVIDENCE, *((AUDIT,) if name == "gate_overwatch" else ())), max_iterations=12,
                                   writes=(*node.writes, "validation.txt"),
                                   produces=(*node.produces, "validation", "attempts_used", "review_attempts_used"),
                                   knobs=(*node.knobs, "review_attempts"))
    report = graph.nodes["report"]
    graph.nodes["report"] = replace(report, reads=(*report.reads, EVIDENCE, AUDIT))


def validate_repo_questions(
    record: Any, expected_functions: list[str], reviewer_name: str,
) -> list[str]:
    """Validate a parsed JSON repo_questions record against the expected function list.

    Returns a list of ``[repo-questions]``-prefixed problem strings (empty = pass).
    """
    problems: list[str] = []
    if not isinstance(record, list):
        problems.append(f"[repo-questions] {reviewer_name}: repo_questions is not a list")
        return problems

    # Normalise expected: strip :line suffix, deduplicate
    normalised_expected: dict[str, str] = {}
    for fn in expected_functions:
        key = re.sub(r':\d+(-\d+)?$', '', fn).strip()
        if key:
            normalised_expected[key] = fn

    # Build a set of function names from the record
    record_functions: set[str] = set()
    for entry in record:
        if isinstance(entry, dict) and "function" in entry:
            record_functions.add(str(entry["function"]).strip())

    # Check every expected function has a matching entry
    for norm_key, orig_fn in normalised_expected.items():
        found = False
        for rf in record_functions:
            rf_norm = re.sub(r':\d+(-\d+)?$', '', rf).strip()
            if rf_norm == norm_key or rf == orig_fn:
                found = True
                break
        if not found:
            problems.append(
                f"[repo-questions] {reviewer_name}: no entry for changed function '{orig_fn}'"
            )

    # Check each entry has the three required answers with path:line evidence
    for entry in record:
        if not isinstance(entry, dict):
            continue
        fn = str(entry.get("function", "?"))
        for key in ("duplicates", "callers", "shared_state"):
            answer = str(entry.get(key, ""))
            if not answer:
                problems.append(
                    f"[repo-questions] {reviewer_name}: entry for '{fn}', answer '{key}' has no path:line evidence"
                )
                continue
            has_path_line = bool(_PATH_LINE_RE.search(answer))
            has_absent = "absent:" in answer.lower()
            if not has_path_line and not has_absent:
                problems.append(
                    f"[repo-questions] {reviewer_name}: entry for '{fn}', answer '{key}' has no path:line evidence"
                )

    return problems


def check_review_questions(
    review_text: str, changed_functions: list[str], reviewer_name: str,
    *, require_brief_trace: bool = False, target: str = "",
) -> list[str]:
    """Validate the review text for repository questions (markdown reviewers).

    Returns a list of ``[repo-questions]``-prefixed problem strings (empty = pass).
    """
    problems: list[str] = []

    # Find the ## Repository questions section
    rq_match = re.search(
        r'(?:^|\n)##\s+Repository questions\s*\n(.*)',
        review_text, re.DOTALL,
    )
    if rq_match is None:
        problems.append(
            f"[repo-questions] {reviewer_name}: the review has no '## Repository questions' section with a JSON block"
        )
        return problems

    section_text = rq_match.group(1)
    # Stop at next ## heading
    next_heading = re.search(r'\n##\s+', section_text)
    if next_heading:
        section_text = section_text[:next_heading.start()]

    # Find a ```json fenced block
    json_match = re.search(r'```json\s*\n(.*?)```', section_text, re.DOTALL)
    if json_match is None:
        problems.append(
            f"[repo-questions] {reviewer_name}: the review has no '## Repository questions' section with a JSON block"
        )
        return problems

    try:
        record = json.loads(json_match.group(1))
    except (json.JSONDecodeError, ValueError):
        problems.append(
            f"[repo-questions] {reviewer_name}: the review has no '## Repository questions' section with a JSON block"
        )
        return problems

    problems.extend(validate_repo_questions(record, changed_functions, reviewer_name))

    # Brief-example trace check
    if require_brief_trace:
        bt_match = re.search(
            r'(?:^|\n)##\s+Brief example trace\s*\n(.*?)(?:\n##\s+|\Z)',
            review_text, re.DOTALL,
        )
        if bt_match is None:
            problems.append(
                f"[repo-questions] {reviewer_name}: the design review has no "
                "'## Brief example trace' section with path:line evidence and runtime names"
            )
        else:
            trace_text = bt_match.group(1)
            citations = _PATH_LINE_RE.findall(trace_text)
            if len(citations) < 2:
                problems.append(
                    f"[repo-questions] {reviewer_name}: the design review has no "
                    "'## Brief example trace' section with path:line evidence and runtime names"
                )
            else:
                # Check for runtime names
                if target and "refactory" in target.lower():
                    runtime_names = ("walker", "dsh", "ceo", "gui", "fast")
                else:
                    runtime_names = ("cli", "api", "server", "worker", "test",
                                     "walker", "dsh", "ceo", "gui", "fast")
                trace_lower = trace_text.lower()
                has_runtime = any(name in trace_lower for name in runtime_names)
                if not has_runtime:
                    problems.append(
                        f"[repo-questions] {reviewer_name}: the design review has no "
                        "'## Brief example trace' section with path:line evidence and runtime names"
                    )

    return problems


def require_completion(ctx: Any) -> None:
    workers = ("focused_qa",) if "repair_attempts" in ctx.knobs else REVIEWERS[:-1]
    problems = current_problems(ctx, workers) + audit_problems(ctx)
    if problems:
        raise ValueError("authoring sign-off is incomplete or stale: " + "; ".join(problems))
