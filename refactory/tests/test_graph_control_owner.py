"""`refactory graph-control` against a real spine owner (dm-244, dm-252, dm-227, dm-232).

Each run's owner is the TypeScript spine itself: `scripted-owner.mjs` mounts it on a
small graph whose nodes are shell commands appending to `dispatch.log`. A test starts the
first owner by hand, then drives the CLI as a person does; the CLI starts later owners
through recovery's supervisor and run-config's `owner_argv`. So what a control did is
read from what the owner dispatched and what the spine wrote, never from a mock.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from refactory_dsh import recovery
from refactory_dsh.run_state import is_alive

REPO = Path(__file__).resolve().parents[2]
SPINE = REPO / 'packages/workflow/workflow-spine'
OWNER = SPINE / 'tests/fixtures/scripted-owner.mjs'
NODE = shutil.which('node')
MODE = 'scripted/owner'
GRAPH_NAME = 'scripted-owner'
INVOCATION = 'inv-1'

pytestmark = pytest.mark.skipif(
    NODE is None or not (SPINE / 'node_modules/@deepseek-ai/cordis').exists(),
    reason='the spine owner needs Node and the installed workspace (pnpm install)')


def fn(node_id: str, run: Path, then: str = '') -> dict[str, Any]:
    """A command node that logs its own dispatch."""
    return {'_type': 'FnNode', 'id': node_id, 'command': f'echo {node_id} >> {run}/dispatch.log{then}',
            'reads': [], 'writes': []}


def user_gate(node_id: str) -> dict[str, Any]:
    return {'_type': 'GateNode', 'id': node_id, 'evaluator_type': 'user', 'gate_prompt': 'Go on?', 'reads': []}


def failing_gate(node_id: str, run: Path) -> dict[str, Any]:
    """A fn gate that logs its dispatch, and stops the run (exit 3) while `fail-<id>` exists."""
    return {'_type': 'GateNode', 'id': node_id, 'evaluator_type': 'fn', 'reads': [],
            'evaluator_command': (f'echo {node_id} >> {run}/dispatch.log; if [ -e {run}/fail-{node_id} ]; '
                                  f'then echo "--- refactory failure ---" >&2; exit 3; fi; echo proceed')}


def graph(start: str, nodes: list[dict[str, Any]], edges: list[tuple[str, str, str | None]]) -> dict[str, Any]:
    return {'name': GRAPH_NAME, 'start_node': start, 'nodes': {node['id']: node for node in nodes},
            'edges': [{'source': s, 'target': t, **({'condition': c} if c else {})} for s, t, c in edges]}


class Run:
    """One run directory whose owner is the scripted spine."""

    def __init__(self, tmp_path: Path, make_graph, *, answers: tuple[str, ...] = (),
                 hold_while_paused: bool = False, pinned: bool = True) -> None:
        self.root = (tmp_path / 'run').resolve()
        self.refactory = self.root / '.refactory'
        self.refactory.mkdir(parents=True)
        script = self.root / 'owner-script.json'
        script.write_text(json.dumps({'graph': make_graph(self.root), 'answers': list(answers),
                                      'hold_while_paused': hold_while_paused}))
        (self.refactory / 'invocation.json').write_text(json.dumps({'id': INVOCATION}))
        launcher = f'import os, sys; os.execv({NODE!r}, [{NODE!r}, {str(OWNER)!r}, *sys.argv[1:]])'
        config: dict[str, Any] = {'mode': MODE, 'project': str(self.root), 'owner_argv': ['-c', launcher]}
        if pinned:
            # What the run was prepared from: recovery refuses when it changed.
            config['configuration_fingerprints'] = {str(script): recovery._configuration_fingerprint(script)}
        (self.refactory / 'run-config.json').write_text(json.dumps(config))
        self.env = {**os.environ, 'REFACTORY_MAX_RESPAWNS': '0', 'PYTHONDONTWRITEBYTECODE': '1'}
        self.live: subprocess.Popen[str] | None = None

    # ── the owner ──
    def owner(self) -> None:
        """Run the run's first owner by hand, to where it stops."""
        done = subprocess.run([NODE, str(OWNER), '--workdir', str(self.root)], env=self.env, cwd=self.root,
                              capture_output=True, text=True, timeout=120)
        assert done.returncode == 0, done.stderr[-3000:]

    def start_live_owner(self) -> None:
        """Start an owner that stays alive while paused, as a live run's does."""
        self.live = subprocess.Popen([NODE, str(OWNER), '--workdir', str(self.root)], env=self.env,
                                     cwd=self.root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def wait_live_owner(self) -> None:
        assert self.live is not None
        _, stderr = self.live.communicate(timeout=120)
        assert self.live.returncode == 0, stderr[-3000:]

    def wait_for_supervisor(self, timeout: float = 120.0) -> None:
        """Wait until the owner recovery started has finished, so its effects are all on disk."""
        console = self.root / 'console.json'
        pid = json.loads(console.read_text()).get('pid') if console.exists() else None
        deadline = time.monotonic() + timeout
        while isinstance(pid, int) and is_alive(pid):
            assert time.monotonic() < deadline, 'the started owner did not finish'
            time.sleep(0.1)

    def stop_everything(self) -> None:
        if self.live is not None and self.live.poll() is None:
            self.live.kill()
            self.live.wait()
        pids = []
        console = self.root / 'console.json'
        if console.exists():
            pids.append(json.loads(console.read_text()).get('pid'))
        if (self.refactory / 'spine-checkpoint.json').exists():
            pids.append(self.checkpoint().get('owner_pid'))
        for pid in pids:
            if isinstance(pid, int) and pid != os.getpid() and is_alive(pid):
                try:
                    os.killpg(pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass

    # ── what a person does ──
    def queue(self, action: str, target: str = '') -> None:
        """Queue a control the way `graph-control` does, before the first owner exists."""
        identifier = f'{time.time_ns():020d}-{action}'
        directory = self.refactory / 'graph-controls'
        directory.mkdir(exist_ok=True)
        (directory / f'{identifier}.json').write_text(json.dumps({
            'id': identifier, 'action': action, 'target': target, 'text': '', 'revision': '',
            'run_id': str(self.root), 'invocation': INVOCATION, 'status': 'queued'}))

    def cli(self, action: str, *args: str) -> tuple[int, dict[str, Any]]:
        done = subprocess.run([sys.executable, '-m', 'refactory_dsh.cli', 'graph-control', action,
                               '--run', str(self.root), *args, '--json'],
                              env=self.env, cwd=self.root, capture_output=True, text=True, timeout=120)
        try:
            payload = json.loads(done.stdout)
        except json.JSONDecodeError:
            payload = {'stdout': done.stdout, 'stderr': done.stderr}
        return done.returncode, payload

    def stale_marker(self, node: str = 'verifier') -> None:
        """A pending delivery marker an earlier dispatch left (its outcome unknown)."""
        identifier = 'stale-delivery'
        pending = self.refactory / 'agent-delivery/pending'
        pending.mkdir(parents=True)
        (pending / f'{hashlib.sha256(node.encode()).hexdigest()}.json').write_text(
            json.dumps({'id': identifier, 'node': node}))
        receipt = self.refactory / 'agent-delivery' / identifier
        receipt.mkdir()
        (receipt / 'receipt.json').write_text(json.dumps({'node': node, 'status': 'pending'}))

    # ── what can be read ──
    def checkpoint(self) -> dict[str, Any]:
        return json.loads((self.refactory / 'spine-checkpoint.json').read_text())

    def status(self) -> dict[str, Any]:
        return json.loads((self.refactory / 'spine-status.json').read_text())

    def receipt(self) -> dict[str, Any]:
        return json.loads((self.refactory / 'graph-control-receipt.json').read_text())

    def lines(self, name: str) -> list[str]:
        path = self.root / name
        return path.read_text().split() if path.exists() else []

    def dispatched(self) -> list[str]:
        return self.lines('dispatch.log')

    def owners(self) -> int:
        return len(self.root.joinpath('owners.log').read_text().splitlines())

    def queued(self) -> list[str]:
        return sorted(p.name for p in (self.refactory / 'graph-controls').glob('*.json'))

    def controls_applied(self) -> list[dict[str, Any]]:
        """The spine's journal of controls it applied or refused."""
        events = [json.loads(line) for line in (self.refactory / 'events.jsonl').read_text().splitlines()
                  if line.strip()]
        return [event for event in events if event.get('kind') == 'control']

    def wait_until(self, condition, timeout: float = 60.0) -> None:
        deadline = time.monotonic() + timeout
        while not condition():
            assert time.monotonic() < deadline, 'timed out waiting for the owner'
            time.sleep(0.05)


@pytest.fixture
def runs(tmp_path):
    made: list[Run] = []

    def make(*args, **kwargs) -> Run:
        run = Run(tmp_path, *args, **kwargs)
        made.append(run)
        return run

    yield make
    for run in made:
        run.stop_everything()


def _paused_before_emit(run: Path) -> dict[str, Any]:
    return graph('gate_strategy', [fn('gate_strategy', run), fn('emit', run)], [('gate_strategy', 'emit', None)])


# ── dm-244: stop on a paused run with no owner ─────────────────────────────

def test_stop_on_a_paused_ownerless_run_is_applied_by_an_owner_it_starts(runs):
    """C1: the stop is queued, an owner is started, its drain halts the run before any
    dispatch. Python writes neither spine file: the halt is the started owner's."""
    run = runs(_paused_before_emit)
    run.queue('pause', 'emit')
    run.owner()
    assert run.status()['status'] == 'paused'
    assert run.dispatched() == ['gate_strategy']

    code, out = run.cli('stop')
    assert code == 0, out
    assert out['status'] == 'applied', out
    run.wait_for_supervisor()
    assert run.status()['status'] == 'halted'
    assert run.dispatched() == ['gate_strategy'], 'nothing may be dispatched after the stop'
    assert run.owners() == 2, 'the stop must be applied by the owner graph-control started'
    assert run.receipt()['action'] == 'stop' and run.receipt()['status'] == 'applied'
    assert run.queued() == []


def test_a_refused_stop_leaves_nothing_queued_and_a_later_resume_goes_on(runs):
    """P6: a stale pending marker makes recovery refuse the owner a stop needs. The stop
    must not stay queued, or the owner of the person's later resume would halt the run."""
    run = runs(_paused_before_emit)
    run.queue('pause', 'emit')
    run.owner()
    run.stale_marker()

    code, out = run.cli('stop')
    assert code == 2, out
    assert 'acknowledge' in out['error']
    assert run.queued() == []
    events = [json.loads(line) for line in (run.refactory / 'events.jsonl').read_text().splitlines()]
    assert [e['state'] for e in events if e.get('kind') == 'control_requested'] == ['queued', 'withdrawn']

    assert run.cli('acknowledge')[0] == 0
    code, out = run.cli('resume')
    assert code == 0, out
    run.wait_for_supervisor()
    assert run.status()['status'] == 'done'
    assert run.dispatched() == ['gate_strategy', 'emit']


def test_a_stop_refused_for_missing_configuration_leaves_nothing_queued(runs):
    """P7: a run with no configuration fingerprints cannot be recovered automatically, so
    stop refuses; any later owner of the run must not find the refused stop queued."""
    run = runs(_paused_before_emit, pinned=False)
    run.queue('pause', 'emit')
    run.owner()

    code, out = run.cli('stop')
    assert code == 2, out
    assert 'configuration fingerprints' in out['error']
    assert run.queued() == []

    # A later owner of the same boundary (prepared without the pin this run lacks).
    recovery.prepare(run.root, GRAPH_NAME, skip_configuration=True)
    run.owner()
    assert run.status()['status'] == 'done'
    assert run.dispatched() == ['gate_strategy', 'emit']


# ── dm-252: acknowledge, preview, rerun on a failed run ────────────────────

def _fails_at_gate_qa(run: Path) -> dict[str, Any]:
    return graph('emit', [fn('emit', run), user_gate('review'), failing_gate('gate_qa', run), fn('end', run)],
                 [('emit', 'review', None), ('review', 'gate_qa', 'proceed'), ('gate_qa', 'end', 'proceed')])


def test_a_failed_run_reruns_emit_after_acknowledge_without_redispatching_the_failure(runs):
    """C2: on a failed run with an unknown worker outcome, a rerun first refuses and
    leaves nothing queued; acknowledge, preview and rerun --acknowledge-failure then
    apply once. The owner that rerun starts applies it before dispatching anything:
    emit runs first and the failed gate_qa is never dispatched."""
    run = runs(_fails_at_gate_qa, answers=('proceed',))
    (run.root / 'fail-gate_qa').write_text('')
    run.owner()
    assert run.status()['status'] == 'failed'
    assert run.dispatched() == ['emit', 'gate_qa']
    run.stale_marker()

    code, preview = run.cli('preview', '--node', 'emit')
    assert code == 0 and preview['status'] == 'preview', preview
    assert preview['revision'] == run.checkpoint()['graph_revision']
    code, out = run.cli('rerun', '--node', 'emit', '--revision', preview['revision'], '--acknowledge-failure')
    assert code == 2 and 'acknowledge' in out['error'], out
    assert run.queued() == [], 'a rerun whose owner could not start must not stay queued'

    assert run.cli('acknowledge')[0] == 0
    code, again = run.cli('preview', '--node', 'emit')
    assert code == 0
    assert again['revision'] == preview['revision'], 'acknowledge must keep a preview current'
    (run.root / 'dispatch.log').unlink()
    code, out = run.cli('rerun', '--node', 'emit', '--revision', again['revision'], '--acknowledge-failure')
    assert code == 0 and out['status'] == 'queued', out
    run.wait_for_supervisor()

    assert run.dispatched() == ['emit'], 'the rerun applies before dispatch; gate_qa is never re-run first'
    assert run.receipt()['status'] == 'applied'
    assert run.receipt()['message'].startswith('Reopened emit')
    assert [(c['state']) for c in run.controls_applied()] == ['applied']
    assert run.status()['status'] == 'waiting_user'
    assert run.lines('questions.log') == ['review', 'review']


# ── dm-227: rerun behind a person's pause, with the owner alive ─────────────

def _strategy_then_gate(run: Path) -> dict[str, Any]:
    return graph('gate_strategy', [fn('gate_strategy', run), user_gate('review'), fn('emit', run)],
                 [('gate_strategy', 'review', None), ('review', 'emit', 'proceed')])


def test_a_rerun_behind_a_pause_holds_at_the_reopened_node_until_resumed(runs):
    """C3, on a live owner: pause before emit, then preview and rerun gate_strategy. The
    run is paused at gate_strategy with nothing dispatched; after resume gate_strategy
    runs and the run waits at the user gate after it."""
    run = runs(_strategy_then_gate, answers=('proceed',), hold_while_paused=True)
    run.queue('pause', 'emit')
    run.start_live_owner()
    run.wait_until(lambda: (run.refactory / 'spine-status.json').exists()
                   and run.status()['status'] == 'paused')
    assert run.dispatched() == ['gate_strategy']

    code, preview = run.cli('preview', '--node', 'gate_strategy')
    assert code == 0 and preview['status'] == 'preview', preview
    code, out = run.cli('rerun', '--node', 'gate_strategy', '--revision', preview['revision'])
    assert code == 0 and out['status'] == 'queued', out
    run.wait_until(lambda: run.queued() == [])
    assert run.receipt()['status'] == 'applied', run.receipt()
    assert run.status()['status'] == 'paused'
    assert run.status()['paused_before'] == ['gate_strategy']
    assert run.dispatched() == ['gate_strategy'], 'nothing is dispatched behind the pause'

    code, out = run.cli('resume')
    assert code == 0 and out['status'] == 'queued', out
    run.wait_live_owner()
    assert run.dispatched() == ['gate_strategy', 'gate_strategy']
    assert run.status()['status'] == 'waiting_user'
    assert run.lines('questions.log') == ['review', 'review']
    assert run.owners() == 1, 'a live owner applies the controls itself'


# ── dm-232: a timed-out optional verifier does not block a resume ──────────

def _review_fork(run: Path) -> dict[str, Any]:
    spec = run / 'verifier-spec.json'
    verifier = {'_type': 'AgentNode', 'id': 'verifier', 'role': 'code_reviewer', 'prompt_template': 'Verify.',
                'reads': [], 'writes': [],
                # As workers.lower_workflow dispatches an agent node: a worker process.
                'dispatch_command': f'{sys.executable} -m refactory_dsh.worker_node {spec}'}
    return graph('fork_qa', [
        {'_type': 'ForkNode', 'id': 'fork_qa', 'targets': ['health', 'verifier'],
         'optional_targets': ['verifier'], 'reads': [], 'writes': []},
        fn('health', run), verifier,
        {'_type': 'JoinNode', 'id': 'join_qa', 'sources': ['health'], 'reads': [], 'writes': []},
        fn('gate_check', run), fn('emit', run),
    ], [('fork_qa', 'join_qa', None), ('health', 'join_qa', None), ('verifier', 'join_qa', None),
        ('join_qa', 'gate_check', None), ('gate_check', 'emit', None)])


def test_a_timed_out_optional_verifier_does_not_stop_a_later_resume(runs):
    """C4: the verifier's session runs past its one-second wall, so the worker records the
    branch missing and settles the delivery marker it began. A pause, then a resume,
    goes on with no acknowledge and no file moved by hand."""
    run = runs(_review_fork)
    slow = run.root / 'slow-dsh'
    slow.write_text('#!/bin/sh\nexec sleep 60\n')
    slow.chmod(0o755)
    (run.root / 'verifier-spec.json').write_text(json.dumps({
        'schema_version': 1, 'node_id': 'verifier', 'optional': True, 'reads': [], 'writes': [],
        'produces': [], 'prompt': 'Verify.',
        'model_context': {'worker_backend': 'dsh', 'dsh_cli': str(slow), 'node_wall_s': 1, 'attempts': 1}}))
    run.queue('pause', 'emit')
    run.owner()

    assert run.status()['status'] == 'paused'
    assert 'timed out' in (run.root / 'reviews/verifier-missing.txt').read_text()
    assert list((run.refactory / 'agent-delivery/pending').glob('*.json')) == []
    assert sorted(run.dispatched()) == ['gate_check', 'health']

    code, out = run.cli('resume')
    assert code == 0, out
    run.wait_for_supervisor()
    assert run.status()['status'] == 'done'
    assert run.dispatched()[-1] == 'emit'
    assert not (run.refactory / 'set-aside').exists(), 'nothing was set aside by hand or by acknowledge'
