"""Start a fresh graph without treating earlier outputs as current results.

Explicit redo has its own invalidation contract. Preparing/opening a graph is a
new invocation: archive the old run's owned outputs and runtime facts, retain
external inputs and the steering ledger, and publish the new lowering together.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from refactory_dsh import lowering
from refactory_dsh.nn.executor import ITEMS_OVERRIDE
from refactory_dsh.run_artifacts import archive_root, overlaps, owned_outputs
from refactory_dsh.steering import LEDGER

INVOCATION = Path('.refactory/invocation.json')


def _inputs(workdir: Path, declarations: dict[str, Any]) -> list[Path]:
    nodes = [node for node in declarations.values() if isinstance(node, dict)]

    def relative(path: Path) -> Path:
        if path.is_absolute() and path.is_relative_to(workdir):
            return path.relative_to(workdir)
        return path

    def canonical(path: Path) -> Path:
        return relative((workdir / path).resolve())

    writes = [canonical(Path(name)) for node in nodes for name in node.get('writes', [])]
    inputs: list[Path] = []
    for node in nodes:
        for name in node.get('reads', []):
            path = Path(name)
            resolved = canonical(path)
            if not any(overlaps(resolved, output) for output in writes):
                # Preserve both an alias and its target. A lexical-only match can
                # archive a directory still read through an in-run symlink or .. .
                inputs.extend([relative(path), resolved])
    # A runtime source override is an input even when an older graph owned the
    # directory it now points at. Never archive that input out from under the run.
    sources = [node.get('data', {}).get('source_path') for node in nodes]
    override = workdir / ITEMS_OVERRIDE
    if override.exists():
        payload = json.loads(override.read_text())
        if not isinstance(payload, dict):
            raise ValueError(f'{ITEMS_OVERRIDE} must contain an object')
        sources.extend(entry.get('source_path') for entry in payload.values() if isinstance(entry, dict))
    for source in sources:
        if not source:
            continue
        path = Path(source)
        resolved = path.resolve() if path.is_absolute() else (workdir / path).resolve()
        if resolved.is_relative_to(workdir):
            inputs.extend([relative(path), resolved.relative_to(workdir)])
    inputs.extend([Path(ITEMS_OVERRIDE), Path(LEDGER)])
    return inputs


def prepare(
    state: dict[str, Any], workdir: Path, *, name: str, transform: Callable,
) -> Path:
    """Stage a valid lowering, archive prior ownership, then install or roll back."""
    workdir = Path(workdir).resolve()
    runtime = workdir / '.refactory'
    if runtime.is_symlink() or not runtime.resolve().is_relative_to(workdir):
        raise ValueError('cannot prepare a run with .refactory outside its work directory')
    runtime.mkdir(parents=True, exist_ok=True)
    declaration_path = workdir / lowering.DECLARATIONS
    prior: dict[str, Any] = {}
    if declaration_path.exists():
        prior = json.loads(declaration_path.read_text())
        if not isinstance(prior, dict):
            raise ValueError('existing run declarations must contain an object; use a new work directory')
    elif (workdir / 'facts.json').exists() or (runtime / 'spine-status.json').exists():
        raise ValueError('existing run has no ownership declarations; use a new work directory')
    writes = [name for node in prior.values() if isinstance(node, dict) for name in node.get('writes', [])]
    targets, retained = owned_outputs(workdir, writes, _inputs(workdir, lowering.node_declarations(state)))
    metadata = [Path('facts.json'), Path('graphs'), Path('.memory'), Path(lowering.DECLARATIONS),
                Path('.refactory/spine-status.json'), Path('.refactory/agent-delivery'), INVOCATION]
    for relative in metadata:
        path = workdir / relative
        if not path.exists() and not path.is_symlink():
            continue
        if path.is_symlink() or not path.resolve().is_relative_to(workdir):
            raise ValueError(f'cannot archive runtime path {str(relative)!r} outside the run')
        targets.append(relative)
    # A runtime directory can subsume an output already selected beneath it.
    # Move each tree only once, shallowest first.
    selected: list[Path] = []
    for relative in sorted(set(targets), key=lambda path: (len(path.parts), str(path))):
        if not any(parent in relative.parents for parent in selected):
            selected.append(relative)
    targets = selected
    identifier = uuid.uuid4().hex
    archive = archive_root(workdir, 'invocations') / identifier
    record = {'id': identifier, 'mode': name, 'started': datetime.now(timezone.utc).isoformat(),
              'previous': str(archive.relative_to(workdir)) if targets else None,
              'archived': [str(path) for path in targets], 'retained_inputs': retained}
    # Nothing from the old invocation moves until the complete new lowering exists.
    with tempfile.TemporaryDirectory(prefix='prepare-', dir=runtime) as temporary:
        stage = Path(temporary)
        lowering.write_mode_graph(state, stage / 'graphs', name=name, transform=transform)
        lowering.write_declarations(state, stage)
        (stage / INVOCATION).write_text(json.dumps(record, indent=2) + '\n')
        moved: list[Path] = []
        installed: list[Path] = []
        try:
            if targets:
                archive.mkdir(parents=True)
                (archive / 'archive.json').write_text(json.dumps(record, indent=2) + '\n')
            for relative in targets:
                destination = archive / 'artifacts' / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                (workdir / relative).rename(destination)
                moved.append(relative)
            for relative in [Path('graphs'), Path(lowering.DECLARATIONS), INVOCATION]:
                (stage / relative).rename(workdir / relative)
                installed.append(relative)
        except Exception:
            for relative in reversed(installed):
                path = workdir / relative
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()
            for relative in reversed(moved):
                (archive / 'artifacts' / relative).rename(workdir / relative)
            raise
    return workdir / 'graphs'
