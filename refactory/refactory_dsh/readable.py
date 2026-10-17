"""Render the console's JSON payloads as readable text for terminal users.

One rendering, not two: these functions produce the ``text`` field the console
adds to every status, steer and graph-control payload, and both the CLI and
tool-refactory print that text instead of keeping a second formatter.
"""

from __future__ import annotations

from typing import Any


def format_status(payload: dict[str, Any]) -> str:
    """Readable status block for a design or optimizer run."""
    lines: list[str] = []
    authoring = payload.get('authoring') if isinstance(payload.get('authoring'), dict) else None

    if authoring is not None:
        name = authoring.get('name') or payload.get('mode') or ''
        lines.append(f"Run {payload.get('run_id', '')}  {name}")

        # Position: running nodes with kinds, or wait reason, or idle.
        activity = payload.get('activity') or {}
        running_nodes = activity.get('running_nodes') or []
        state = payload.get('state', '')
        if state == 'waiting_user' and activity.get('wait_reason'):
            lines.append(f"Position: {activity['wait_reason']}")
        elif running_nodes:
            parts = []
            for entry in running_nodes:
                if isinstance(entry, dict):
                    node_id = entry.get('id', '')
                    kind = entry.get('kind', '')
                    parts.append(f"{node_id} ({kind})" if kind else str(node_id))
                else:
                    parts.append(str(entry))
            lines.append(f"Position: {', '.join(parts)}")
        else:
            lines.append("Position: idle")

        completed = authoring.get('completed') or []
        lines.append(f"Steps done: {len(completed)}")

        step_tokens = authoring.get('step_tokens')
        if isinstance(step_tokens, dict) and step_tokens:
            entries = [f"{k}: {v:,}" for k, v in step_tokens.items()
                       if isinstance(v, (int, float))]
            if entries:
                lines.append(f"Tokens per step: {', '.join(entries)}")

        total_tokens = authoring.get('total_tokens')
        if isinstance(total_tokens, (int, float)):
            lines.append(f"Total tokens: {total_tokens:,}")

        owner_alive = payload.get('owner_alive')
        if owner_alive is True:
            lines.append("Owner: alive")
        elif owner_alive is False:
            lines.append("Owner: not alive")

        lines.append(f"State: {state}")

        error = authoring.get('error')
        if error:
            lines.append(f"Error: {error}")

        control = (activity.get('control') or {}) if isinstance(activity.get('control'), dict) else {}
        if control.get('status') == 'failed':
            action = control.get('action', '')
            message = control.get('message', '')
            lines.append(f"Last failed control: {action} — {message}")

        if state in ('waiting_user', 'paused', 'died'):
            root = payload.get('root', payload.get('run_id', ''))
            lines.append(f"Next: refactory graph-control resume --run {root}")
    else:
        # Optimizer run.
        lines.append(f"Run {payload.get('run_id', '')}  {payload.get('mode', '')}")
        best = payload.get('best_score')
        score_text = f"best score {best}" if isinstance(best, (int, float)) else "no score yet"
        lines.append(f"State: {payload.get('state', '')}; {score_text}.")
        counters = payload.get('counters') or {}
        epoch = counters.get('epoch', 0)
        generation = counters.get('generation', 0)
        evaluations = counters.get('evaluations', 0)
        lines.append(f"Epoch {epoch}, generation {generation}, {evaluations} evaluation(s).")

    return '\n'.join(lines)


def format_steer(payload: dict[str, Any]) -> str:
    """Readable steer output showing only what this call recorded."""
    lines: list[str] = []
    recorded_verdicts = payload.get('recorded_verdicts') or []
    recorded_note = payload.get('recorded_note')
    has_verdict = bool(recorded_verdicts)
    has_note = recorded_note is not None

    if has_verdict:
        for pair in recorded_verdicts:
            lines.append(f"Recorded verdict {pair}.")
        root = payload.get('run', '')
        lines.append("Continue the run:")
        lines.append(f"  refactory graph-control resume --run {root}")

    if has_note:
        lines.append("Note recorded.")

    if not has_verdict and not has_note:
        entries = payload.get('entries') or []
        count = len(entries)
        word = 'entry' if count == 1 else 'entries'
        lines.append(f"Ledger: {count} {word}.")

    return '\n'.join(lines)


def format_graph_control(payload: dict[str, Any]) -> str:
    """Readable graph-control output: what was queued or applied, and why if it failed."""
    lines: list[str] = []
    status = payload.get('status', '')
    action = payload.get('action', '')
    run_id = payload.get('run_id', '')

    if status == 'queued':
        lines.append(f"Queued {action} for {run_id}.")
    elif status == 'preview':
        nodes = payload.get('invalidated') or []
        count = len(nodes)
        names = ', '.join(str(n) for n in nodes)
        lines.append(f"Preview: {count} node(s) would be invalidated: {names}.")
    elif status == 'deferred':
        nodes = payload.get('invalidated') or []
        count = len(nodes)
        names = ', '.join(str(n) for n in nodes)
        lines.append(f"Preview (deferred): {count} node(s) would be invalidated: {names}.")
    elif status == 'failed':
        message = payload.get('message', '')
        lines.append(f"{action} failed: {message}.")

    last_failed = payload.get('last_failed_receipt')
    if isinstance(last_failed, dict):
        fa = last_failed.get('action', '')
        fm = last_failed.get('message', '')
        lines.append(f"Last failed control: {fa} — {fm}.")

    if action == 'resume' and payload.get('owner'):
        lines.append(f"Started a new owner for {run_id}.")

    return '\n'.join(lines)
