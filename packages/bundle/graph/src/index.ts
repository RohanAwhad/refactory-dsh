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

/**
 * The best score the mode's ops recorded, or `null` when none recorded one.
 *
 * `null` rather than `0`: an authoring mode has no `score()`, and a run whose state
 * file was never written measured nothing. Reporting either as `0` says "this scored
 * zero", which is a different fact from "this was not measured" and the one a reader
 * would act on.
 */
function bestScore(workdir: string, mode: string): number | null {
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
  return null
}

/** What the spine last recorded about the cursor, or `undefined` when it recorded nothing. */
function spineStatus(workdir: string): { status: string; nodes?: string[]; error?: string } | undefined {
  const path = join(workdir, '.refactory', 'spine-status.json')
  if (!existsSync(path)) return undefined
  try {
    return JSON.parse(readFileSync(path, 'utf8')) as { status: string; nodes?: string[]; error?: string }
  } catch {
    // A status file that is not JSON is a status this runner cannot read; the run's
    // own report stands rather than being replaced by a parse error.
    return undefined
  }
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
  const provider = startup.provider || selection.provider
  const model = startup.model || selection.model
  const { agent } = await agents.create({
    sessionId: brandString<SessionId>(`session-${randomUUID()}`),
    meta: { cwd: startup.workdir },
    agentOptions: { provider, model },
    setup: (agentCtx) => {
      // The selection the middleware rewrites each request from has to be the
      // effective one: installing the settings default here pinned every run to it
      // and discarded `--provider`/`--model` without saying so.
      installModelSelection(agentCtx, { current: { provider, model }, assembled: undefined })
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

  // Quiescence is not completion. The cursor's own status on disk is what says
  // whether the graph finished, and a run that stopped with nodes still due (the CEO
  // backgrounded a subagent and ended its turn, or a gate could not be answered) must
  // not report a score as if it had run: "the graph finished" and "the graph was
  // abandoned" have to be different outcomes to whoever started it.
  const status = spineStatus(startup.workdir)
  if (status !== undefined && status.status !== 'done') {
    const due = status.nodes === undefined || status.nodes.length === 0
      ? ''
      : `: ${status.nodes.join(', ')} still due`
    io.stderr.write(
      `dsh: the graph did not finish (${status.status}${due})`
      + `${status.error === undefined ? '' : ` — ${status.error}`}\n`,
    )
    io.exit(1)
    return
  }
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
