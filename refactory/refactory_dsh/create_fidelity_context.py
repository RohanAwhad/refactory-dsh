"""Candidate-only, complete evidence reads with a configured UTF-8 ceiling.

This limits the owner's prompt bytes, not an unknown model's context window.
Small bundles retain the original direct prompt. Large DSH bundles preserve
every document and dispatch evidence-only reads to the original logical role. The
original final task may run only after every range has a valid read receipt.
Native builders retain their existing direct backend/context delivery.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import threading
import time
import uuid
from typing import Any, Sequence

PROTOCOL = "create-fidelity-full-context/v1"
MARKER = "CONTEXT_RECONCILIATION_SHA256="
BASIS_KEYS = ("protocol", "context_id", "logical_node_id", "role", "requested_route", "policy", "deadline_scope",
              "instructions", "fixed_documents", "documents", "pages", "reviews", "aggregations", "frontier",
              "obligations", "obligation_rendering")
UNIVERSE_PROJECTION = "quality-decision-universe/v1"


def _sha(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class Document:
    name: str
    body: str
    source_path: str | None = None
    provenance: dict | None = None
    fixed: bool = False


def decision_universe(plan: dict) -> dict:
    """Complete decision identities/obligations; full tailored text stays paged."""
    return {"tasks": [{key: task[key] for key in ("id", "role", "focus", "output", "mandatory", "coverage")}
                      for task in plan["tasks"]],
            "intent_items": plan["intent_items"], "coverage_items": plan["coverage_items"]}


def final_packet(record: dict, workdir: Path) -> dict:
    """Lossless model-read outputs plus identities; audit metadata stays bound.

    No model claim, excerpt, scope, status or coverage observation is omitted.
    Worker/delivery/inventory metadata is independently checked and bound by the
    reconciliation digest, rather than repeated in the semantic final prompt.
    """
    return {"protocol": record["protocol"], "context_id": record["context_id"],
            "logical_node_id": record["logical_node_id"], "role": record["role"],
            # Before adjudication is complete, fitting reserves the final
            # digest's exact length. Only the real digest can be dispatched.
            "reconciliation_digest": record.get("reconciliation_digest", "0" * 64),
            "documents": [{key: document[key] for key in ("path", "sha256", "bytes", "snapshot", "reviewed_as", "source_kind")}
                          for document in record["documents"]],
            "pages": [{key: page[key] for key in ("page_id", "path", "document_sha256", "start_byte", "end_byte", "page_sha256")}
                      for page in record["pages"]],
            "reviews": [{"input_id": item["input_id"], "raw_response_sha256": item["input_sha256"],
                         "raw_response": item["raw_response"]}
                        for item in adjudication_inputs(record, record["frontier"], workdir)["records"]]}


def adjudication_inputs(record: dict, ids: list[str], workdir: Path) -> dict:
    """Lossless prior outputs and deterministic finding identities for a batch."""
    by_id = {review["page_id"]: review for review in record["reviews"]}
    by_id.update({item["aggregation_id"]: item for item in record["aggregations"]})
    if len(set(ids)) != len(ids) or any(identifier not in by_id for identifier in ids):
        raise ValueError("adjudication names duplicate or unknown prior inputs")
    pages = {page["page_id"]: page for page in record["pages"]}
    records, retained, positives = [], {}, {}
    for identifier in ids:
        item = by_id[identifier]
        raw = (workdir / item["raw_response"]["snapshot"]).read_bytes().decode("utf-8")
        if _sha(raw.encode()) != item["raw_response"]["sha256"]:
            raise ValueError("adjudication prior output changed")
        value = json.loads(raw)
        records.append({"input_id": identifier, "input_sha256": _sha(raw.encode()), "raw_response": raw})
        positive = {}
        if value["scope"] == "page_only":
            page = pages[identifier]
            for kind, values in (("claim", value["claims"]), ("coverage", value["coverage"])):
                for finding in values:
                    suffix = finding["claim_id"] if kind == "claim" else finding["obligation_id"]
                    status = finding["verdict"] if kind == "claim" else finding["status"]
                    stable_id = f"{identifier}:{kind}:{suffix}"
                    content = {"kind": kind, "page_id": identifier, "path": page["path"],
                               "document_sha256": page["document_sha256"], "finding": finding}
                    if status in {"adverse", "unresolved", "not_in_page"}:
                        retained[stable_id] = {"finding_id": stable_id, "status": status, "content": content}
                    elif status in {"supported", "evidence"}:
                        positive[stable_id] = content
        elif value["scope"] == "scoped_adjudication":
            for finding in value["retained_findings"]:
                previous = retained.get(finding["finding_id"])
                if previous is not None and previous != finding:
                    raise ValueError("conflicting retained adjudication finding")
                retained[finding["finding_id"]] = finding
            for decision in value["decisions"]:
                positive[f"{identifier}:decision:{decision['decision_id']}"] = decision
        else:
            raise ValueError("adjudication input is not an actual scoped original-role output")
        positives[identifier] = positive
    return {"records": records, "retained_findings": [retained[key] for key in sorted(retained)],
            "positive_findings": positives}


@dataclass(frozen=True)
class ContextPolicy:
    max_prompt_bytes: int = 196608
    runtime_reserve_tokens: int = 32768
    output_reserve_tokens: int = 16384
    declared_context_tokens: int | None = None
    approved_byte_bpe: bool = False
    accounting: str = "configured_utf8_byte_ceiling"
    max_adjudications: int = 128
    max_adjudication_rounds: int = 16

    def check(self, prompt: str, system: str = "") -> None:
        size = len((system + "\n\n" + prompt if system else prompt).encode())
        if size > self.max_prompt_bytes:
            raise ValueError(f"complete context exceeds configured UTF-8 prompt ceiling: {size}>{self.max_prompt_bytes}")
        if self.approved_byte_bpe:
            if self.declared_context_tokens is None:
                raise ValueError("approved byte-BPE accounting needs an explicit context limit")
            if size + self.runtime_reserve_tokens + self.output_reserve_tokens > self.declared_context_tokens:
                raise ValueError("complete context exceeds declared byte-BPE window with runtime/output reserves")

    def validate(self) -> None:
        for key in ("max_prompt_bytes", "runtime_reserve_tokens", "output_reserve_tokens", "max_adjudications", "max_adjudication_rounds"):
            value = getattr(self, key)
            if type(value) is not int or value <= 0:
                raise ValueError(f"{key} must be a positive integer")
        if self.max_adjudications > 128 or self.max_adjudication_rounds > 16:
            raise ValueError("scoped adjudication is limited to 128 calls and 16 rounds")
        if self.accounting != "configured_utf8_byte_ceiling" or type(self.approved_byte_bpe) is not bool:
            raise ValueError("unsupported context accounting policy")
        if self.declared_context_tokens is not None and (type(self.declared_context_tokens) is not int or self.declared_context_tokens <= 0):
            raise ValueError("declared context limit must be a positive integer")


class PreparedPrompt(str):
    """A same-process prompt carrying its still-active original stage scope."""

    def __new__(cls, body: str, *, session: Any):
        value = super().__new__(cls, body)
        value.session = session
        value.manifest_path = session.relative_manifest
        value.digest = session.record["reconciliation_digest"]
        return value

    @property
    def client(self):
        return self.session.bound_client(self.session.node.id)


class DirectPrompt(str):
    """Exact old text/single dispatch, with an actual-delivery ceiling check."""

    def __new__(cls, body: str, *, policy):
        value = super().__new__(cls, body)
        value.policy = policy
        return value


def _save(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text())


def _artifact_inventory(root: Path) -> dict:
    """All regular files, including caches/metadata; reject links/specials."""
    if root.is_symlink():
        raise ValueError("evidence read application tree contains a symlink")
    result = {}
    def walk(directory):
        with os.scandir(directory) as scan:
            children = sorted(scan, key=lambda entry: entry.name)
        for entry in children:
            path = Path(entry.path)
            mode = entry.stat(follow_symlinks=False).st_mode
            relative = str(path.relative_to(root))
            if stat.S_ISDIR(mode):
                result[relative] = {"kind": "directory"}
                walk(path)
            elif stat.S_ISREG(mode):
                descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                with os.fdopen(descriptor, "rb") as stream:
                    if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                        raise ValueError("evidence read application file changed type")
                    result[relative] = {"kind": "file", "sha256": hashlib.file_digest(stream, "sha256").hexdigest()}
            else:
                raise ValueError("evidence read application tree contains a symlink or special file")
    walk(root)
    return result


class _Session:
    def __init__(self, ctx, node, instructions, documents, client, deadline, policy, obligations):
        self.ctx, self.node, self.policy = ctx, node, policy
        self.client = client
        self.root = ctx.workdir / ".refactory/create-fidelity-context" / re.sub(r"[^A-Za-z0-9_.-]", "_", node.id) / uuid.uuid4().hex
        self.root.mkdir(parents=True)
        self.relative_manifest = str((self.root / "manifest.json").relative_to(ctx.workdir))
        self.stop = threading.Event()
        self.client = self.client.with_cancellation(self.stop)
        allowance = node.timeout_s or self.client.effective_deadlines().get("stage_timeout_s") or 3600
        self.deadline = min(time.monotonic() + allowance, deadline if deadline is not None else math.inf)
        self.timer = threading.Timer(max(0, self.deadline - time.monotonic()), self.stop.set)
        self.timer.daemon = True
        self.source_bytes: dict[str, bytes] = {}
        self.application_before = self.application_snapshot()
        self.record = {"schema_version": 1, "protocol": PROTOCOL, "context_id": self.root.name,
                       "logical_node_id": node.id, "role": node.role, "requested_route": client.route(),
                       "started": datetime.now(timezone.utc).isoformat(), "status": "reading",
                       "policy": asdict(policy), "instructions": self.snapshot("instructions.txt", instructions.encode()),
                       "fixed_documents": [], "documents": [], "pages": [], "reviews": [], "aggregations": [], "frontier": [],
                       "obligations": obligations, "deadline_scope": "one absolute scope across reads and final call"}
        fixed = []
        for name in (".factory/strategy/user-intent.md", ".factory/strategy/current.md"):
            path = ctx.path(name)
            if path.is_file():
                data = path.read_bytes()
                fixed.append((name, data))
                self.source_bytes[name] = data
                self.record["fixed_documents"].append({"path": name, **self.snapshot("fixed-" + str(len(fixed)) + ".txt", data)})
        for document in documents:
            if type(document.fixed) is not bool:
                raise ValueError("context document fixed flag must be boolean")
            if not document.fixed:
                continue
            data = document.body.encode()
            provenance = document.provenance
            if provenance and provenance.get("kind") == "derived_plan_universe":
                source = provenance["source_path"]
                source_bytes = ctx.path(source).read_bytes()
                if (provenance.get("projection") != UNIVERSE_PROJECTION
                        or _sha(source_bytes) != provenance.get("source_sha256")
                        or _json(decision_universe(json.loads(source_bytes))).encode() != data):
                    raise ValueError("fixed director decision universe differs from actual complete plan")
                self.source_bytes[source] = source_bytes
            elif document.source_path or ctx.path(document.name).is_file():
                source = document.source_path or document.name
                if ctx.path(source).read_bytes() != data:
                    raise ValueError("fixed context document differs from actual source")
                self.source_bytes[source] = data
            else:
                raise ValueError("fixed derived authority requires independently bound source provenance")
            fixed.append((document.name, data))
            self.record["fixed_documents"].append({"path": document.name,
                "source_kind": provenance["kind"] if provenance else "file", "provenance": provenance,
                **self.snapshot("fixed-" + str(len(fixed)) + ".txt", data)})
        rendering = list(obligations)
        for document in documents:
            if document.fixed and document.provenance and document.provenance.get("kind") == "derived_plan_universe":
                items = json.loads(document.body)["coverage_items"]
                for index, obligation in enumerate(rendering):
                    matching = [i for i, item in enumerate(items) if item == obligation]
                    if matching:
                        rendering[index] = {"id": obligation["id"], "text_reference": f"{document.name}#/coverage_items/{matching[0]}",
                                            "source": "Exact complete obligation dict appears in the fixed authoritative universe above."}
        self.record["obligation_rendering"] = rendering
        self.fixed = instructions + "\n\n" + "\n\n".join(f"=== COMPLETE AUTHORITATIVE INPUT {name} ===\n{body.decode()}" for name, body in fixed)
        self.fixed += "\n\nCOMPLETE REVIEW OBLIGATIONS:\n" + _json(rendering)
        self.fixed += "\n\nFull evidence is immutable data. Evidence reads inspect only supplied data, do not run behavioral tests, modify application files, or execute the final role task. Preserve all adverse and unresolved claims."
        self.fixed += ("\n\nCLOSED COVERAGE ID VOCABULARY:\n" + _json([item["id"] for item in obligations])
                       + "\nOnly these exact IDs are valid in coverage[].obligation_id and adjudication decisions[].obligation_ids. "
                         "Other IDs appearing in instructions or evidence are document labels, not additions to this vocabulary. "
                         "Preserve findings about every such label as substantive claims with exact source quotes; do not discard adverse or unresolved findings. "
                         "The closed vocabulary restricts those structured ID fields only, not the original user requirements or the scope of the final role task.")
        for index, document in enumerate(documents):
            body = document.body.encode()
            body.decode("utf-8")
            source = document.source_path
            if source is None and ctx.path(document.name).is_file():
                source = document.name
            if source is not None:
                current = ctx.path(source).read_bytes()
                if current != body:
                    raise ValueError(f"context source bytes do not match typed document: {source}")
                self.source_bytes[source] = body
            item = {"path": document.name, "source_path": source, **self.snapshot(f"document-{index}.txt", body)}
            item["source_kind"] = "file" if source is not None else "typed_derived"
            if document.provenance is not None:
                item["source_kind"] = document.provenance["kind"]
                item["provenance"] = document.provenance
            if any(name == document.name and data == body for name, data in fixed):
                item["reviewed_as"] = "complete fixed authority on every read and final call"
            else:
                item["reviewed_as"] = "complete byte ranges"
            self.record["documents"].append(item)
        self.flush()
        self.timer.start()

    def snapshot(self, name, body):
        path = self.root / name
        path.write_bytes(body)
        return {"sha256": _sha(body), "bytes": len(body), "snapshot": str(path.relative_to(self.ctx.workdir))}

    def application_snapshot(self):
        result = {}
        names = set(self.node.writes)
        # The source archives intentionally overlap and remain nonblocking.
        # Other archive owners may legitimately update these shared outputs.
        if self.node.role == "archivist":
            names = {name for name in names if Path(name).as_posix() != ".factory/archive"}
            names.discard(".factory/performance_report.json")
        package_root = self.ctx.workdir / ".factory/packages"
        admitted = (self.ctx.workdir / ".factory/reviews/native-admission.json").is_file()
        if package_root.exists() and (self.node.role == "builder" or (admitted and self.node.role != "archivist")):
            names.add(".factory/packages")
        for name in sorted(names):
            if (self.ctx.workdir / name).is_symlink():
                raise ValueError("evidence read cannot follow an application-output symlink")
            path = self.ctx.path(name)
            if path.is_symlink():
                raise ValueError("evidence read cannot follow an application-output symlink")
            if path.is_dir():
                result[name] = _artifact_inventory(path)
            elif path.is_file():
                result[name] = {"sha256": _sha(path.read_bytes()), "bytes": path.stat().st_size}
            elif path.exists():
                raise ValueError("evidence read application output is not regular")
            else:
                result[name] = None
        return result

    def flush(self):
        _save(self.root / "manifest.json", self.record)
        self.saved_manifest_sha256 = _sha((self.root / "manifest.json").read_bytes())

    def fresh(self):
        if _sha((self.root / "manifest.json").read_bytes()) != self.saved_manifest_sha256:
            raise ValueError("runtime-owned context manifest changed during model dispatch")
        for name, original in self.source_bytes.items():
            if self.ctx.path(name).read_bytes() != original:
                raise ValueError(f"full context evidence changed during review: {name}")
        for group in ("instructions", "fixed_documents", "documents", "pages"):
            entries = [self.record[group]] if group == "instructions" else self.record[group]
            for item in entries:
                data = self.ctx.path(item["snapshot"]).read_bytes()
                digest = item.get("page_sha256", item.get("sha256"))
                if _sha(data) != digest:
                    raise ValueError("immutable context snapshot was modified")
        for review in self.record["reviews"] + self.record["aggregations"]:
            for key in ("supplied_prompt", "raw_response", "application_before", "application_after"):
                item = review.get(key)
                if item is not None and _sha(self.ctx.path(item["snapshot"]).read_bytes()) != item["sha256"]:
                    raise ValueError("supplied context prompt or raw review response changed")
            if review.get("status") == "reviewed":
                if _sha(self.ctx.path(review["delivery_receipt"]).read_bytes()) != review["delivery_receipt_sha256"]:
                    raise ValueError("completed context read delivery receipt changed")
                for name, digest in review["worker_receipt_sha256"].items():
                    if _sha(self.ctx.path(name).read_bytes()) != digest:
                        raise ValueError("completed context read worker receipt changed")
        packet = self.record.get("final_packet")
        if packet is not None and _sha(self.ctx.path(packet["snapshot"]).read_bytes()) != packet["sha256"]:
            raise ValueError("complete final reconciliation packet changed")

    def bound_client(self, identity):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0 or self.stop.is_set():
            raise TimeoutError("full context original stage deadline expired")
        return self.client.with_identity(identity, self.node.role).with_node_timeout(max(1, math.ceil(remaining))).with_deadline_observation("bounded_context", self.deadline)

    def fail(self, error):
        self.stop.set()
        self.timer.cancel()
        self.record.update(status="failed", error=f"{type(error).__name__}: {error}")
        self.flush()
        error.bounded_context_manifest = self.relative_manifest
        error.bounded_context_digest = self.record.get("reconciliation_digest")

    def page_prompt(self, page, text):
        return (self.fixed + "\n\nEVIDENCE READ REQUEST:\n" + _json({"protocol": PROTOCOL,
                "context_id": self.root.name, "logical_node_id": self.node.id, "role": self.node.role, **page})
                + "\nReturn ONLY strict JSON with schema_version:1, scope:'page_only', context_id, logical_node_id, role, page_id, "
                  "document_sha256, start_byte, end_byte, page_sha256, coverage, claims. Echo all identity/ranges exactly. "
                  "coverage is a sparse array using only the exact CLOSED COVERAGE ID VOCABULARY above, with obligation_id, status "
                  "(evidence/adverse/not_in_page/unresolved), reason (nonempty). It may be empty only when concrete claims "
                  "still describe this page; an omitted ID is not satisfied. No read can declare global coverage complete. claims is a nonempty array of "
                  "objects with claim_id, verdict (supported/adverse/unresolved), reason, start_byte, end_byte, quote. "
                  "Each quote must equal the exact original UTF8 bytes in its absolute document range within this page. "
                  "Read-only byte-index/hash computations on the supplied evidence are permitted to obtain exact UTF8 ranges. "
                  "Use only the supplied page snapshot for those computations and print only offsets, hashes or short exact excerpts. "
                  "Do not print or re-read whole original documents into tool history; the runtime supplies every complete range in bounded passes. "
                  "Give substantive evidence observations, not generic PASS or filename assertions. No final task verdict yet."
                + "\n=== EXACT EVIDENCE RANGE ===\n" + text + "\n=== END EXACT EVIDENCE RANGE ===")

    def review(self, page, text):
        from refactory_dsh.agent_delivery import begin, finish
        from refactory_dsh.nn.executor import _ask_model
        identity = f"{self.node.id}:context:{self.root.name}:{page['page_id']}"
        client = self.bound_client(identity)
        prompt = self.page_prompt(page, text)
        system = "Evidence-only read by the original logical role."
        delivery = begin(self.ctx.workdir, identity, prompt, [], reads=list(self.source_bytes), model_context=client.context())
        self.policy.check(delivery["prompt"], system)
        before = set((self.ctx.workdir / ".refactory/workers").glob("*.json"))
        entry = {"logical_node_id": self.node.id, "role": self.node.role, "page_id": page["page_id"],
                 "execution_node_id": identity, "system": system, "delivery_receipt": delivery["receipt"],
                 "supplied_prompt": self.snapshot(page["page_id"] + ".prompt.txt", delivery["prompt"].encode()),
                 "status": "running", "worker_receipts": []}
        application_before = self.application_snapshot()
        before_bytes = _json(application_before).encode()
        entry["application_before"] = self.snapshot("application-" + _sha(before_bytes) + ".json", before_bytes)
        self.record["reviews"].append(entry)
        self.flush()
        try:
            raw = _ask_model(client, delivery["prompt"], system=system, workdir=self.ctx.workdir)
            receipt = finish(self.ctx.workdir, identity, final_response=raw, capture_final=False)
            entry["raw_response"] = self.snapshot(page["page_id"] + ".response.json", raw.encode())
            entry["worker_receipts"] = self.workers(before, identity, delivery["prompt"], system)
            entry["worker_receipt_sha256"] = {name: _sha(self.ctx.path(name).read_bytes()) for name in entry["worker_receipts"]}
            entry["delivery_receipt_sha256"] = _sha(self.ctx.path(entry["delivery_receipt"]).read_bytes())
            entry["review"] = json.loads(raw)
            self.validate_read(entry["review"], page)
            application_after = self.application_snapshot()
            after_bytes = _json(application_after).encode()
            entry["application_after"] = self.snapshot("application-" + _sha(after_bytes) + ".json", after_bytes)
            if application_before != self.application_before or application_after != self.application_before:
                raise ValueError("observed application-output/package drift during evidence-only read; worker attribution unproven")
            entry["application_observation_scope"] = "Before/after full inventories detect persistent changes; not an OS read-only sandbox or proof against transient changes."
            entry["status"] = "reviewed"
            self.fresh()
        except BaseException as error:
            entry.update(status="failed", error=f"{type(error).__name__}: {error}")
            entry["worker_receipts"] = [str(path.relative_to(self.ctx.workdir)) for path in sorted(
                set((self.ctx.workdir / ".refactory/workers").glob("*.json")) - before)
                if self._worker_identity(path) == identity]
            raise
        finally:
            self.flush()

    def _worker_identity(self, path):
        try:
            return _read_json(path).get("node_id")
        except (OSError, ValueError):
            return None

    def workers(self, before, identity, supplied_prompt, system):
        paths = []
        for path in sorted(set((self.ctx.workdir / ".refactory/workers").glob("*.json")) - before):
            try:
                record = _read_json(path)
            except (ValueError, OSError):
                continue
            if record.get("node_id") != identity:
                continue
            if record.get("state") != "completed" or record.get("role") != self.node.role:
                raise ValueError("context read/final worker did not complete in the original role")
            if record.get("requested_route") != self.record["requested_route"] or record.get("native_observations"):
                raise ValueError("context read/final changed requested route or delegated externally")
            actual = record.get("actual_route") or {}
            if actual.get("source") != "runtime-request" or any(actual.get(key) != self.record["requested_route"].get(key) for key in ("worker_backend", "provider", "model", "effort")):
                raise ValueError("context read/final actual route differs from original logical route")
            task = (system + "\n\n" + supplied_prompt if system else supplied_prompt).encode()
            transport = record.get("prompt_transport") or {}
            if (transport.get("kind") not in {"argv", "stdin_file"} or transport.get("encoding") != "utf-8"
                    or transport.get("bytes") != len(task) or transport.get("sha256") != _sha(task)):
                raise ValueError("context read/final transport does not bind exact supplied bytes")
            paths.append(str(path.relative_to(self.ctx.workdir)))
        if len(paths) != 1:
            raise ValueError("context read/final requires exactly one completed original-role worker")
        return paths

    def validate_read(self, value, page):
        keys = {"schema_version", "scope", "context_id", "logical_node_id", "role", "page_id", "document_sha256", "start_byte", "end_byte", "page_sha256", "coverage", "claims"}
        if not isinstance(value, dict) or set(value) != keys or type(value.get("schema_version")) is not int or value["schema_version"] != 1:
            raise ValueError("context evidence read has invalid strict fields")
        if value["scope"] != "page_only":
            raise ValueError("context read must limit claims to this page, not global acceptance")
        expected = {"context_id": self.root.name, "logical_node_id": self.node.id, "role": self.node.role}
        expected.update({k: page[k] for k in ("page_id", "document_sha256", "start_byte", "end_byte", "page_sha256")})
        if any(value[k] != v or type(value[k]) is not type(v) for k, v in expected.items()):
            raise ValueError("context evidence read identity, digest or range mismatch")
        coverage = value["coverage"]
        required = {item["id"] for item in self.record["obligations"]}
        if not isinstance(coverage, list):
            raise ValueError("context evidence read coverage must be a typed array")
        seen = set()
        for item in coverage:
            if (not isinstance(item, dict) or set(item) != {"obligation_id", "status", "reason"}
                    or item["obligation_id"] not in required or item["obligation_id"] in seen
                    or item["status"] not in {"evidence", "adverse", "not_in_page", "unresolved"}
                    or not isinstance(item["reason"], str) or not item["reason"].strip()):
                raise ValueError("context evidence read has invalid obligation coverage")
            seen.add(item["obligation_id"])
        claims = value["claims"]
        if not isinstance(claims, list) or not claims:
            raise ValueError("context evidence read lacks concrete claim/excerpt observations")
        data = self.ctx.path(page["document_snapshot"]).read_bytes()
        ids = set()
        for claim in claims:
            if (not isinstance(claim, dict) or set(claim) != {"claim_id", "verdict", "reason", "start_byte", "end_byte", "quote"}
                    or not isinstance(claim["claim_id"], str) or not claim["claim_id"].strip() or claim["claim_id"] in ids
                    or claim["verdict"] not in {"supported", "adverse", "unresolved"}
                    or not isinstance(claim["reason"], str) or not claim["reason"].strip()
                    or type(claim["start_byte"]) is not int or type(claim["end_byte"]) is not int
                    or not page["start_byte"] <= claim["start_byte"] < claim["end_byte"] <= page["end_byte"]
                    or not isinstance(claim["quote"], str) or not claim["quote"]
                    or claim["quote"].encode() != data[claim["start_byte"]:claim["end_byte"]]):
                raise ValueError("context evidence read has forged or invalid exact claim excerpt")
            ids.add(claim["claim_id"])

    def adjudicate(self, ids, round_number):
        from refactory_dsh.agent_delivery import begin, finish
        from refactory_dsh.nn.executor import _ask_model
        if len(ids) < 2 or len(self.record["aggregations"]) >= self.policy.max_adjudications:
            raise ValueError("bounded adjudication cannot progress within configured call bound")
        inputs = adjudication_inputs(self.record, ids, self.ctx.workdir)
        identifier = f"aggregate-{len(self.record['aggregations']) + 1:04d}"
        request = {"schema_version": 1, "context_id": self.root.name, "logical_node_id": self.node.id,
                   "role": self.node.role, "aggregation_id": identifier, "input_ids": ids,
                   "input_sha256": {item["input_id"]: item["input_sha256"] for item in inputs["records"]}}
        max_response_bytes = sum(len(item["raw_response"].encode("utf-8")) for item in inputs["records"]) - 1
        prompt = (self.fixed + "\n\nSCOPED ADJUDICATION REQUEST:\n" + _json(request)
                  + "\n\nCOMPLETE PRIOR MODEL OUTPUTS:\n" + _json(inputs)
                  + "\nReturn ONLY strict JSON with schema_version:1, scope:'scoped_adjudication', context_id, logical_node_id, role, "
                    "aggregation_id, input_ids, input_sha256, retained_findings, decisions. Echo identity and every exact ordered input ID/digest. "
                    "retained_findings MUST copy EVERY adverse/unresolved finding from the supplied retained_findings verbatim (stable ID, status, full content); "
                    "do not discard, summarize or rewrite any. decisions may combine only supplied positive_findings with explicit original source references. "
                    "Each decision has decision_id, conclusion (substantive nonempty text), obligation_ids (only IDs from CLOSED COVERAGE ID VOCABULARY above), sources (objects with input_id and finding_ids). "
                    "Every positive finding must be referenced; no unknown, duplicate or unread source reference is allowed. "
                    f"The complete response must contain at most {max_response_bytes} UTF-8 bytes (sum of the exact supplied raw responses minus one). "
                    "Return compact JSON with no indentation or formatting whitespace outside strings. Combine only positive findings through the required exact source references. "
                    "Never shorten, omit or rewrite mandatory retained adverse/unresolved/not_in_page data to meet this budget; preserve it completely even if the full required response cannot fit, so the runtime can fail honestly. "
                    "No final global PASS is authorized by this scoped adjudication. Original raw reports and prior outputs remain preserved; "
                    "Use only this complete prior-output packet; do not print whole original documents or other ledger branches into tool history. "
                    "resolve scope/coverage/conflicts honestly without manufacturing missing evidence. Do not execute final application tasks/tests or edit artifacts.")
        execution_id = f"{self.node.id}:context:{self.root.name}:{identifier}"
        client = self.bound_client(execution_id)
        system = "Scoped evidence adjudication by the original logical role."
        delivery = begin(self.ctx.workdir, execution_id, prompt, [], reads=list(self.source_bytes), model_context=client.context())
        self.policy.check(delivery["prompt"], system)
        before = set((self.ctx.workdir / ".refactory/workers").glob("*.json"))
        entry = {"aggregation_id": identifier, "round": round_number, "input_ids": ids,
                 "input_sha256": request["input_sha256"], "logical_node_id": self.node.id, "role": self.node.role,
                 "execution_node_id": execution_id, "system": system, "delivery_receipt": delivery["receipt"],
                 "input_packet": self.snapshot(identifier + ".inputs.json", _json(inputs).encode()),
                 "supplied_prompt": self.snapshot(identifier + ".prompt.txt", delivery["prompt"].encode()),
                 "status": "running", "worker_receipts": []}
        application_before = self.application_snapshot()
        entry["application_before"] = self.snapshot("application-" + _sha(_json(application_before).encode()) + ".json", _json(application_before).encode())
        self.record["aggregations"].append(entry)
        self.flush()
        try:
            raw = _ask_model(client, delivery["prompt"], system=system, workdir=self.ctx.workdir)
            finish(self.ctx.workdir, execution_id, final_response=raw, capture_final=False)
            entry["raw_response"] = self.snapshot(identifier + ".response.json", raw.encode())
            entry["worker_receipts"] = self.workers(before, execution_id, delivery["prompt"], system)
            entry["worker_receipt_sha256"] = {name: _sha(self.ctx.path(name).read_bytes()) for name in entry["worker_receipts"]}
            entry["delivery_receipt_sha256"] = _sha(self.ctx.path(entry["delivery_receipt"]).read_bytes())
            value = json.loads(raw)
            self.validate_adjudication(value, request, inputs)
            # A strict size decrease prevents arbitrary summary growth or an
            # infinite aggregation loop. Noncompressible adverse data fails.
            input_bytes = sum(len(item["raw_response"].encode()) for item in inputs["records"])
            if len(raw.encode()) >= input_bytes:
                raise ValueError("bounded adjudication made no lossless semantic progress")
            entry.update(review=value, status="reviewed", input_response_bytes=input_bytes,
                         output_response_bytes=len(raw.encode()))
            application_after = self.application_snapshot()
            entry["application_after"] = self.snapshot("application-" + _sha(_json(application_after).encode()) + ".json", _json(application_after).encode())
            if application_before != self.application_before or application_after != self.application_before:
                raise ValueError("observed application-output/package drift during scoped adjudication; attribution unproven")
            self.fresh()
        except BaseException as error:
            entry.update(status="failed", error=f"{type(error).__name__}: {error}")
            entry["worker_receipts"] = [str(path.relative_to(self.ctx.workdir)) for path in sorted(
                set((self.ctx.workdir / ".refactory/workers").glob("*.json")) - before)
                if self._worker_identity(path) == execution_id]
            raise
        finally:
            self.flush()
        return identifier

    def validate_adjudication(self, value, request, inputs):
        keys = {"schema_version", "scope", "context_id", "logical_node_id", "role", "aggregation_id", "input_ids", "input_sha256", "retained_findings", "decisions"}
        if (not isinstance(value, dict) or set(value) != keys or type(value.get("schema_version")) is not int
                or value["schema_version"] != 1 or value["scope"] != "scoped_adjudication"
                or any(type(value[key]) is not type(request[key]) or value[key] != request[key]
                       for key in request if key != "schema_version")):
            raise ValueError("scoped adjudication identity or exact input IDs/digests mismatch")
        if value["retained_findings"] != inputs["retained_findings"]:
            raise ValueError("scoped adjudication dropped or changed adverse/unresolved findings")
        decisions = value["decisions"]
        if not isinstance(decisions, list):
            raise ValueError("scoped adjudication decisions must be typed")
        required = {(name, finding) for name, findings in inputs["positive_findings"].items() for finding in findings}
        seen, decision_ids = set(), set()
        obligations = {item["id"] for item in self.record["obligations"]}
        for decision in decisions:
            if (not isinstance(decision, dict) or set(decision) != {"decision_id", "conclusion", "obligation_ids", "sources"}
                    or not isinstance(decision["decision_id"], str) or not decision["decision_id"].strip()
                    or decision["decision_id"] in decision_ids or not isinstance(decision["conclusion"], str) or not decision["conclusion"].strip()
                    or not isinstance(decision["obligation_ids"], list)
                    or any(not isinstance(item, str) for item in decision["obligation_ids"])
                    or len(set(decision["obligation_ids"])) != len(decision["obligation_ids"])
                    or any(item not in obligations for item in decision["obligation_ids"])
                    or not isinstance(decision["sources"], list) or not decision["sources"]):
                raise ValueError("scoped adjudication has invalid scoped decision fields")
            decision_ids.add(decision["decision_id"])
            for source in decision["sources"]:
                if (not isinstance(source, dict) or set(source) != {"input_id", "finding_ids"}
                        or not isinstance(source["input_id"], str) or not isinstance(source["finding_ids"], list)
                        or not source["finding_ids"] or any(not isinstance(item, str) for item in source["finding_ids"])):
                    raise ValueError("scoped adjudication decision lacks exact positive source references")
                for finding in source["finding_ids"]:
                    pair = (source["input_id"], finding)
                    if pair not in required or pair in seen:
                        raise ValueError("scoped adjudication cites unknown/duplicate positive evidence")
                    seen.add(pair)
        if seen != required:
            raise ValueError("scoped adjudication omitted positive input evidence")

    def fit_final(self):
        self.record["frontier"] = [review["page_id"] for review in self.record["reviews"]]
        for round_number in range(self.policy.max_adjudication_rounds + 1):
            packet = final_packet(self.record, self.ctx.workdir)
            # Leave room for final instructions, ledger, execution contract and
            # system text; bind_final checks their actual complete byte count.
            if len(self.fixed.encode()) + len(_json(packet).encode()) + 12000 <= self.policy.max_prompt_bytes:
                self.flush()
                return packet
            if round_number == self.policy.max_adjudication_rounds or len(self.record["frontier"]) < 2:
                raise ValueError("complete final evidence cannot fit; noncompressible scope or adjudication bound reached")
            old = self.record["frontier"]
            groups, current = [], []
            # Group by exact encoded packet bytes rather than /4 token guesses.
            for identifier in old:
                candidate = current + [identifier]
                encoded = _json(adjudication_inputs(self.record, candidate, self.ctx.workdir)).encode()
                if len(self.fixed.encode()) + len(encoded) + 12000 > self.policy.max_prompt_bytes:
                    if len(current) < 2:
                        raise ValueError("complete prior adjudication output cannot fit with original authority under the configured ceiling")
                    groups.append(current)
                    current = [identifier]
                else:
                    current = candidate
            if current:
                groups.append(current)
            updated = []
            for group in groups:
                updated.append(self.adjudicate(group, round_number + 1) if len(group) >= 2 else group[0])
            if len(updated) >= len(old):
                raise ValueError("bounded adjudication frontier made no progress")
            self.record["frontier"] = updated
            self.flush()
        raise ValueError("bounded adjudication exhausted")


def prepare_context(ctx, *, node, instructions: str, documents: Sequence[Document], direct_prompt: str,
                    client=None, deadline=None, policy: ContextPolicy | None = None,
                    obligations: Sequence[dict] | None = None) -> str:
    """Prepare a complete typed bundle; never infer documents from prompt text."""
    policy = policy or getattr(ctx, "_create_fidelity_context_policy", None) or ContextPolicy()
    policy.validate()
    if len(direct_prompt.encode()) <= policy.max_prompt_bytes - 12000:
        return DirectPrompt(direct_prompt, policy=policy)
    explicitly_resolved = client is not None
    client = client or ctx.model
    if client is None or not callable(getattr(client, "with_identity", None)):
        raise ValueError("large Create context requires an original routed DSH model client")
    client = client.with_identity(node.id, node.role) if explicitly_resolved else client.for_node(node.id, role=node.role)
    backend = client.route().get("worker_backend")
    if node.role == "builder" and backend in {"codex", "claude_code"}:
        # Native builders already own their backend's full-context delivery.
        # Preserve that exact direct path without claiming the DSH bounded-read
        # protocol or imposing its ceiling on another supported runtime.
        return direct_prompt
    if backend != "dsh":
        raise ValueError("large Create evidence reads require original DSH ownership")
    obligations = list(obligations or [{"id": "original-role-stage-and-user-contract", "text": "Apply every original role/stage duty and complete authoritative user intent/acceptance contract."}])
    if (not obligations or any(not isinstance(x, dict) or not isinstance(x.get("id"), str) or not x["id"].strip()
                              or not isinstance(x.get("text"), str) or not x["text"].strip() for x in obligations)
            or len({x['id'] for x in obligations}) != len(obligations)):
        raise ValueError("context obligations require distinct typed IDs and full text")
    session = None
    try:
        session = _Session(ctx, node, instructions, documents, client, deadline or getattr(ctx, "_director_deadline", None), policy, obligations)
        # Reserve actual execution-contract/ledger overhead. bind/check also
        # rejects excess actual bytes after those runtime fields are supplied.
        allowance = policy.max_prompt_bytes - len(session.fixed.encode()) - 12000
        if allowance <= 0:
            raise ValueError("original role and complete authoritative obligations cannot fit configured ceiling")
        for document in session.record["documents"]:
            if document["reviewed_as"] != "complete byte ranges":
                continue
            body = ctx.path(document["snapshot"]).read_bytes()
            start = 0
            while start < len(body):
                end = min(len(body), start + allowance)
                while end > start:
                    try:
                        text = body[start:end].decode("utf-8")
                        break
                    except UnicodeDecodeError:
                        end -= 1
                if end == start:
                    raise ValueError("configured context page cannot hold one UTF-8 character")
                page_id = f"page-{len(session.record['pages']) + 1:04d}"
                page = {"page_id": page_id, "path": document["path"], "document_sha256": document["sha256"],
                        "document_snapshot": document["snapshot"], "start_byte": start, "end_byte": end,
                        "page_sha256": _sha(body[start:end]), **session.snapshot(page_id + ".txt", body[start:end])}
                session.record["pages"].append(page)
                session.flush()
                session.review(page, text)
                start = end
        session.fresh()
        # This invokes the original logical model for any required scoped
        # adjudications. The host never writes substitute evidence summaries.
        # All leaf outputs remain immutable and included in the audit digest.
        session.fit_final()
        session.fresh()
        basis = {key: session.record[key] for key in BASIS_KEYS}
        digest = _sha(_json(basis).encode())
        session.record["reconciliation_digest"] = digest
        packet = final_packet(session.record, ctx.workdir)
        session.record["final_packet"] = session.snapshot("final-packet.json", _json(packet).encode())
        final = (session.fixed + "\n\nCOMPLETE VERIFIED SAME-ROLE READ RECORDS:\n" + _json(packet)
                 + "\n\nNow perform ONLY the original final role task. Reconcile every original obligation and every recorded claim, including adverse/unresolved findings and conflicts; prior page reads do not themselves authorize PASS. "
                   "Your final conversational response MUST contain exactly this binding marker: " + MARKER + digest
                 + ". Keep declared file artifacts in their original schema; do not add unknown fields. "
                   "For a strict verdict JSON response put the exact marker inside rationale; for a strict quality JSON response put it inside summary. "
                   "Do not edit source evidence or repeat application tests solely because read passes were staged.")
        policy.check(final)
        session.record.update(status="prepared", final_prepared_prompt=session.snapshot("final-prepared.prompt.txt", final.encode()))
        session.flush()
        return PreparedPrompt(final, session=session)
    except BaseException as error:
        if session is not None:
            session.fail(error)
        raise


def bind_final(ctx, prompt: str, delivery: dict, *, system: str = "") -> None:
    if isinstance(prompt, DirectPrompt):
        prompt.policy.check(delivery["prompt"], system)
        return
    if not isinstance(prompt, PreparedPrompt):
        return
    session = prompt.session
    session.fresh()
    session.bound_client(session.node.id)
    session.policy.check(delivery["prompt"], system)
    session.record["final"] = {"logical_node_id": session.node.id, "role": session.node.role, "system": system,
                               "delivery_receipt": delivery["receipt"],
                               "supplied_prompt": session.snapshot("final-supplied.prompt.txt", delivery["prompt"].encode())}
    metadata = {"protocol": PROTOCOL, "manifest": prompt.manifest_path, "digest": prompt.digest}
    delivery["bounded_context"] = metadata
    from refactory_dsh.agent_delivery import _pending
    for path in (ctx.path(delivery["receipt"]), _pending(ctx.workdir, session.node.id)):
        current = _read_json(path)
        current["bounded_context"] = metadata
        _save(path, current)
    session.workers_before_final = set((ctx.workdir / ".refactory/workers").glob("*.json"))
    session.flush()


def validate_final(ctx, prompt: str, raw_response: str, delivery: dict | None = None) -> None:
    if not isinstance(prompt, PreparedPrompt):
        return
    session = prompt.session
    expected = MARKER + prompt.digest
    markers = re.findall(re.escape(MARKER) + r"[a-f0-9]{64}", raw_response)
    if markers != [expected]:
        raise ValueError("original final role did not reconcile the exact complete context digest")
    session.fresh()
    session.bound_client(session.node.id)
    basis = {key: session.record[key] for key in BASIS_KEYS}
    if _sha(_json(basis).encode()) != prompt.digest:
        raise ValueError("context read records changed before final reconciliation")
    final = session.record.get("final")
    if final is None:
        raise ValueError("context final dispatch has no real delivery binding")
    supplied = ctx.path(final["supplied_prompt"]["snapshot"]).read_text()
    final["worker_receipts"] = session.workers(session.workers_before_final, session.node.id, supplied, final["system"])
    final["raw_response"] = session.snapshot("final.response.txt", raw_response.encode())
    if delivery is not None:
        final["delivery_receipt"] = delivery["receipt"]
    final["uttered_reconciliation_marker"] = expected
    session.record.update(status="reconciled", finished=datetime.now(timezone.utc).isoformat())
    session.flush()


def close_context(prompt: str, error: BaseException | None = None) -> None:
    if isinstance(prompt, PreparedPrompt):
        if error is not None:
            prompt.session.fail(error)
        else:
            prompt.session.timer.cancel()
