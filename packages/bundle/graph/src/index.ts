/**
 * The interactive graph runner: boots one CEO Agent over the spine, drives it
 * to quiescence (the CEO walks `wf_next → wf_done/wf_verdict`, spawning
 * subagents for content nodes and letting the spine auto-run deterministic
 * nodes), flushes its Session, and reports the best score plus the session log.
 * @module @deepseek-ai/dsh-graph
 */

import { randomUUID } from 'node:crypto'
import { existsSync, readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'
import type { Context } from '@deepseek-ai/cordis'
import { brandString } from '@deepseek-ai/dsh-brand'
import { installModelSelection } from '@deepseek-ai/dsh-agent'
import type { ModelSelectionRef } from '@deepseek-ai/dsh-agent'
import { createUserMessage } from '@deepseek-ai/dsh-llm'
import { SessionSeq } from '@deepseek-ai/dsh-session'
import type { Session, SessionId, SessionLogOffset } from '@deepseek-ai/dsh-session'
import type {} from '@deepseek-ai/cordis-plugin-loader'
import { GRAPH_STARTUP_SERVICE, STATE_FILES } from './startup.ts'
import type { GraphStartupValues } from './startup.ts'

/** Stable Cordis plugin name. */
export const name = 'graph-runner'

/** Core services required before the CEO turn can start. */
export const inject = ['agentDefaultModel', 'agents', 'sessions', GRAPH_STARTUP_SERVICE]

/** The best score recorded by the mode's ops, or 0 when nothing was accepted. */
function bestScore(workdir: string, mode: string): number {
  const name = STATE_FILES[mode]
  const candidates = name ? [join(workdir, name)] : []
  for (const file of readdirSync(workdir)) {
    if (file.endsWith('.json')) candidates.push(join(workdir, file))
  }
  for (const path of candidates) {
    if (!existsSync(path)) continue
    try {
      const data = JSON.parse(readFileSync(path, 'utf8')) as { best_score?: unknown }
      if (typeof data.best_score === 'number') return data.best_score
    } catch { /* not JSON yet */ }
  }
  return 0
}

/** The final assistant text of the turn. */
function finalText(session: Session, firstSeq: SessionLogOffset): string {
  let text = ''
  for (let seq = firstSeq; seq < session.seq; seq++) {
    const event = session.eventAt(SessionSeq(seq))
    if (event?.type === 'assistant/message') {
      const joined = event.data.message.content
        .filter(block => block.type === 'text')
        .map(block => block.text)
        .join('')
      if (joined !== '') text = joined
    }
  }
  return text
}

interface Io {
  stdout: { write(chunk: string): unknown }
  stderr: { write(chunk: string): unknown }
  exit(code: number): void
}

interface DefaultModelLike {
  currentSelection(): { provider: string; model: string }
}

async function run(ctx: Context, io: Io): Promise<void> {
  const startup = ctx.get(GRAPH_STARTUP_SERVICE) as GraphStartupValues | undefined
  await ctx.get('loader')?.await()
  const agents = ctx.get('agents')
  const defaultModel = ctx.get('agentDefaultModel') as DefaultModelLike | undefined
  const sessions = ctx.get('sessions')
  if (startup === undefined || agents === undefined || defaultModel === undefined || sessions === undefined) return

  const selection = defaultModel.currentSelection()
  const provider = startup.provider ?? selection.provider
  const model = startup.model ?? selection.model
  const { agent } = await agents.create({
    sessionId: brandString<SessionId>(`session-${randomUUID()}`),
    meta: { cwd: startup.workdir },
    agentOptions: { provider, model },
    setup: (agentCtx) => {
      const selected: ModelSelectionRef = { current: selection, assembled: undefined }
      installModelSelection(agentCtx, selected)
    },
  })
  await agent.whenIdle()
  const firstSeq = agent.session.seq
  try {
    agent.followup(createUserMessage({
      content: [{ type: 'text', text: startup.task }],
      source: { kind: 'user' },
    }))
    await agent.whenIdle()
  } catch (error: unknown) {
    io.stderr.write(`dsh: ${error instanceof Error ? error.message : String(error)}\n`)
  }
  await sessions.flush(agent.session)

  const result = {
    mode: startup.mode,
    task: startup.taskName,
    budget: startup.budget,
    best_score: bestScore(startup.workdir, startup.mode),
    session_id: agent.session.id,
    workdir: startup.workdir,
    final: finalText(agent.session, firstSeq).slice(0, 400),
  }
  io.stdout.write(JSON.stringify(result, null, 2) + '\n')
  io.exit(0)
}

/**
 * Mount the interactive runner. The spine row (in the patch) hands this app the
 * same graph + env it runs the CEO against, through {@link GRAPH_STARTUP_SERVICE}.
 * @param ctx - plugin context carrying core services and the launcher exit request.
 */
export function apply(ctx: Context): void {
  const exit = ctx.get('appExit')
  if (exit === undefined) {
    throw new Error('graph-runner: the launcher must provide ctx.appExit before the tree mounts')
  }
  const io = { stdout: process.stdout, stderr: process.stderr, exit }
  void run(ctx, io).catch((error: unknown) => {
    io.stderr.write(`dsh: ${error instanceof Error ? error.message : String(error)}\n`)
    io.exit(1)
  })
}
