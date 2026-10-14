"""Graph-owned agent dispatch bridge used by CLI and native GUI single runs."""
from __future__ import annotations
import json
import os
from pathlib import Path
import sys
from typing import Any


def execute(spec: dict[str, Any], workdir: Path) -> dict[str, Any]:
    from refactory_dsh.agent_delivery import begin, finish, _path
    from refactory_dsh.model import DshModelClient
    if spec.get('schema_version') != 1:
        raise ValueError('unsupported worker spec version')
    for name in spec['reads']:
        if not _path(workdir, name).exists():
            raise ValueError(f'required worker input is missing: {name}')
    from refactory_dsh.run import NODE_ENV, _knobs, _read_facts
    manifest = json.loads(os.environ.get(NODE_ENV, '{}'))
    prompt_file = manifest.get('prompt_file')
    prompt = spec['prompt']
    if prompt_file:
        prompt_path = Path(prompt_file)
        if prompt_path.exists():
            prompt = prompt_path.read_text(encoding='utf-8')

    node = spec['node_id']
    from refactory_dsh.nn.executor import RunContext, render
    context = RunContext(workdir, knobs=_knobs(), facts=_read_facts(workdir))
    client = DshModelClient(**spec['model_context']).for_node(node)
    delivery = begin(workdir, node, render(prompt, context), spec['writes'],
                     reads=spec['reads'], model_context=client.context())
    output = client.complete(delivery['prompt'], workdir=workdir)
    receipt = finish(workdir, node, final_response=output, capture_final=client.route()["worker_backend"] == "dsh")
    for check in spec.get('post_checks', []):
        artifact = _path(workdir, check['path'])
        if check.get('must_exist', True) and not artifact.exists():
            raise ValueError(f'worker artifact check failed: {check["path"]} missing')
        if artifact.is_file():
            if artifact.stat().st_size < check.get('min_size', 0):
                raise ValueError(f'worker artifact check failed: {check["path"]} too small')
            for text in check.get('must_contain', []):
                if text not in artifact.read_text():
                    raise ValueError(f'worker artifact check failed: {check["path"]} missing required text')
    from refactory_dsh.run import declarations, FACTS
    produces = spec.get('produces')
    if produces is None:
        produces = declarations(workdir, node)['produces']
    if produces:
        value = output
        if spec['writes']:
            try:
                value = _path(workdir, spec['writes'][0]).read_text()
            except (UnicodeDecodeError, IsADirectoryError):
                pass
        facts = _read_facts(workdir)
        facts[produces[0]] = value
        (workdir / FACTS).write_text(json.dumps(facts, indent=2) + '\n')
    return receipt


def main() -> int:
    try:
        path = Path(sys.argv[1])
        serialized = path.read_text()
        if path.parent.name == 'worker-specs':
            import hashlib
            if hashlib.sha256(serialized.encode()).hexdigest() != path.stem:
                raise ValueError('worker spec was modified after graph preparation; refusing execution')
        spec = json.loads(serialized)
        receipt = execute(spec, Path.cwd())
        print(json.dumps({'node': receipt['node'], 'status': receipt['status']}))
        return 0
    except Exception as error:
        print(f'worker: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
