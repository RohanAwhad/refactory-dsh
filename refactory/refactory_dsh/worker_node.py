"""Graph-owned agent dispatch bridge used by CLI and native GUI single runs."""
from __future__ import annotations
import json
import os
from pathlib import Path
import sys
from typing import Any


def _resolve_pin_python(workdir: Path) -> str | None:
    """Read the driver's ``pin_checkout`` from run-config.json, return pin Python or ``None``."""
    config_path = workdir / '.refactory' / 'run-config.json'
    if not config_path.exists():
        return None
    try:
        config = json.loads(config_path.read_text())
        pin_co = config.get('driver', {}).get('pin_checkout')
        if pin_co:
            return str(Path(pin_co) / '.venv' / 'bin' / 'python')
    except Exception:
        pass
    return None


def _check_driver_mid_run(workdir: Path) -> str | None:
    """Return an error message if the driver moved and no pin exists, else ``None``.

    Called at worker dispatch time (brief R5): a process started mid-run after
    the driver moved either uses the pin or refuses with a message naming the
    run, the recorded commit and the command that pins it.
    """
    config_path = workdir / '.refactory' / 'run-config.json'
    if not config_path.exists():
        return None
    try:
        config = json.loads(config_path.read_text())
        driver = config.get('driver')
        if not driver:
            return None
        if driver.get('pin_checkout'):
            return None  # already pinned; handled by _resolve_pin_python
        commit = driver.get('commit')
        checkout = driver.get('checkout')
        if not commit or not checkout:
            return None
        import subprocess as _sp
        result = _sp.run(
            ['git', '-C', checkout, 'rev-parse', 'HEAD'],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip() != commit:
            return (
                f'the driver moved since run {workdir} launched '
                f'(recorded {commit[:12]}, now {result.stdout.strip()[:12]}). '
                f'Pin with: refactory graph-control resume --run {workdir}'
            )
    except Exception:
        pass
    return None


def execute(spec: dict[str, Any], workdir: Path) -> dict[str, Any]:
    # When pinned, override model_context so workers use the pin's paths
    # for nested dispatch and DSH CLI resolution.
    _config_path = workdir / '.refactory' / 'run-config.json'
    if _config_path.exists() and spec.get('model_context'):
        try:
            _cfg = json.loads(_config_path.read_text())
            _pin_co = _cfg.get('driver', {}).get('pin_checkout')
            if _pin_co:
                spec = {**spec, 'model_context': {
                    **spec['model_context'],
                    'runtime_root': _pin_co,
                    'python_bin': str(Path(_pin_co) / '.venv' / 'bin'),
                }}
        except Exception:
            pass
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
        workdir = Path.cwd()
        # ── mid-run driver check (brief R5) ──
        # If the run is pinned, re-exec under the pin's Python so the
        # worker loads the pinned code.  If not pinned but the driver
        # moved, refuse with a diagnostic message.
        _pin_py = _resolve_pin_python(workdir)
        if _pin_py:
            # Compare sys.prefix (the venv root) with the pin's venv, not
            # os.path.realpath: uv venvs symlink to the shared interpreter,
            # so realpath resolves both to the same binary and the switch
            # never fires.  Re-exec with ``-m refactory_dsh.worker_node``
            # so the pin's package is loaded, not sys.argv[0] (which under
            # ``-m`` is the driver's worker_node.py file path).
            _pin_venv = str(Path(_pin_py).parent.parent)
            if sys.prefix != _pin_venv:
                if os.path.isfile(_pin_py):
                    os.execv(_pin_py, [_pin_py, '-m', 'refactory_dsh.worker_node', *sys.argv[1:]])
                print(
                    f'worker: pinned Python {_pin_py} not found; '
                    f'run `refactory pin list` to inspect',
                    file=sys.stderr,
                )
                return 1
        else:
            _drift_msg = _check_driver_mid_run(workdir)
            if _drift_msg:
                print(f'worker: {_drift_msg}', file=sys.stderr)
                return 1
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
