"""LiveMathematicianBench items: fetched at a pinned revision, never shipped.

The benchmark is a live one, released monthly so models cannot have trained on it, and
its dataset declares no licence. A checkout therefore carries only `manifest.json`: the
pinned Hugging Face revision, the sha256 of each monthly file, and the official
train / validation / test id lists SkillOpt publishes. The files themselves are read
from `REFACTORY_LIVEMATH_DATA` when it names a directory holding them, and otherwise
downloaded once into the user cache. A file whose checksum differs is refused by name,
because a different file is a different benchmark.

Items are normalised and their choices shuffled exactly as SkillOpt's LiveMath loader
does for evaluation (seed 42, keyed by item id), so an item here is the item SkillOpt
graded: same question, same choice order, same correct label.
"""

from __future__ import annotations

import functools
import hashlib
import json
import os
import random
import tempfile
import urllib.request
from pathlib import Path
from typing import Any

#: The manifest a checkout ships: source revision, checksums and the id split.
MANIFEST = Path(__file__).resolve().parent / "manifest.json"

#: A directory holding the four monthly files, in any layout, instead of the cache.
DATA_ENV = "REFACTORY_LIVEMATH_DATA"

#: Choice labels in the order SkillOpt assigns them after a shuffle.
LABELS = ("A", "B", "C", "D", "E", "F", "G")

#: The parts the manifest names, in the order the loader lists them.
PARTS = ("train", "validation", "test")


class LiveMathDataError(RuntimeError):
    """The benchmark files are missing, unreadable, or not the pinned ones."""


def manifest() -> dict[str, Any]:
    """The shipped manifest: where the data comes from and how it is split."""
    return json.loads(MANIFEST.read_text())


def cache_dir(revision: str) -> Path:
    """Where downloaded files are kept, one directory per pinned revision."""
    root = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(root) / "refactory" / "livemath" / revision


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _from_directory(directory: Path, name: str) -> Path | None:
    """The first file called `name` under `directory`, if there is one."""
    direct = directory / name
    if direct.is_file():
        return direct
    found = sorted(directory.rglob(name))
    return found[0] if found else None


def _download(url: str, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as handle:
        partial = Path(handle.name)
        with urllib.request.urlopen(url, timeout=120) as response:  # noqa: S310 - pinned https URL
            handle.write(response.read())
    partial.replace(target)


def raw_files() -> list[Path]:
    """The four monthly files, each checked against the manifest's sha256.

    `REFACTORY_LIVEMATH_DATA` wins when it is set, so a machine that already holds the
    files (SkillOpt keeps them under `data/livemath_raw/`) is not made to download them.
    """
    source = manifest()["source"]
    override = os.environ.get(DATA_ENV, "").strip()
    paths: list[Path] = []
    for relative, expected in source["files"].items():
        name = Path(relative).name
        if override:
            path = _from_directory(Path(override).expanduser(), name)
            if path is None:
                raise LiveMathDataError(f"{DATA_ENV}={override} holds no {name}")
        else:
            path = cache_dir(source["revision"]) / name
            if not path.exists():
                url = f"{source['url']}/resolve/{source['revision']}/{relative}"
                try:
                    _download(url, path)
                except OSError as exc:
                    raise LiveMathDataError(
                        f"could not download {url}: {exc}; set {DATA_ENV} to a directory "
                        "that already holds the files"
                    ) from exc
        actual = _sha256(path)
        if actual != expected:
            raise LiveMathDataError(
                f"{path} has sha256 {actual}, not the pinned {expected}; it is not the "
                f"revision {source['revision']} file"
            )
        paths.append(path)
    return paths


# ── normalisation, as SkillOpt's livemathematicianbench dataloader does it ───

def _normalize_label(text: Any) -> str:
    return str(text).strip().upper().rstrip(".):")


def _coerce_choices(raw: Any) -> list[dict[str, str]]:
    if isinstance(raw, list):
        choices = []
        for index, entry in enumerate(raw):
            if isinstance(entry, dict):
                label = str(entry.get("label") or LABELS[index]).strip()
                text = str(entry.get("text") or entry.get("content") or "").strip()
            else:
                label, text = LABELS[index], str(entry).strip()
            if text:
                choices.append({"label": label, "text": text})
        return choices
    if isinstance(raw, dict):
        return [
            {"label": str(label).strip(), "text": str(raw[label]).strip()}
            for label in sorted(raw)
            if str(raw[label]).strip()
        ]
    return []


def _normalize(entry: dict[str, Any], row: int) -> dict[str, Any]:
    mcq = entry.get("mcq") if isinstance(entry.get("mcq"), dict) else {}
    question = str(mcq.get("question") or entry.get("question") or "").strip()
    choices = _coerce_choices(mcq.get("choices") or entry.get("choices") or [])
    correct = mcq.get("correct_choice") or entry.get("correct_choice") or {}
    if isinstance(correct, dict):
        label = _normalize_label(correct.get("label", ""))
        text = str(correct.get("text") or "").strip()
    else:
        label, text = _normalize_label(correct), ""
    by_label = {_normalize_label(choice["label"]): choice["text"] for choice in choices}
    if label and not text:
        text = by_label.get(label, "")
    if label and text and label not in by_label:
        choices.append({"label": label, "text": text})
        choices.sort(key=lambda c: LABELS.index(c["label"]) if c["label"] in LABELS else len(LABELS))
    month = str(entry.get("month") or "").strip()
    number = entry.get("no", row + 1)
    return {
        "id": f"{month}:{number}" if month else str(number),
        "question": question,
        "choices": choices,
        "correct_choice": {"label": label, "text": text},
    }


def _shuffle(item: dict[str, Any], seed: int) -> dict[str, Any]:
    """SkillOpt's per-item choice shuffle: relabel A.. after a seeded permutation."""
    choices = [dict(choice) for choice in item["choices"]]
    digest = hashlib.sha256(f"{seed}:{item['id']}".encode("utf-8")).hexdigest()
    random.Random(int(digest[:16], 16)).shuffle(choices)
    original = _normalize_label(item["correct_choice"]["label"])
    relabelled, correct = [], dict(item["correct_choice"])
    for index, choice in enumerate(choices):
        relabelled.append({"label": LABELS[index], "text": choice["text"]})
        if _normalize_label(choice["label"]) == original:
            correct = {"label": LABELS[index], "text": choice["text"]}
    return {**item, "choices": relabelled, "correct_choice": correct}


@functools.lru_cache(maxsize=1)
def items() -> tuple[dict[str, Any], ...]:
    """Every item the manifest lists, in manifest order, with its part and its answer.

    The answer is in here, so nothing that reaches a pass may be built from these
    dicts wholesale: `LiveMathTask` copies only the question, choices and part.
    """
    pool: dict[str, dict[str, Any]] = {}
    for path in raw_files():
        rows = json.loads(path.read_text(encoding="utf-8"))
        for row, entry in enumerate(rows):
            item = _normalize(entry, row)
            if item["question"] and item["choices"] and item["correct_choice"]["label"]:
                pool[item["id"]] = item
    spec = manifest()
    seed = int(spec["choice_shuffle_seed"])
    listed: list[dict[str, Any]] = []
    for part in PARTS:
        for item_id in spec["split"]["parts"][part]:
            if item_id not in pool:
                raise LiveMathDataError(f"the manifest names {item_id}, which the data lacks")
            listed.append({**_shuffle(pool[item_id], seed), "part": part})
    return tuple(listed)


def answer_key() -> dict[str, dict[str, Any]]:
    """The correct choice and the choices of every item, by id: for grading only."""
    return {
        item["id"]: {"correct_choice": item["correct_choice"], "choices": item["choices"]}
        for item in items()
    }
