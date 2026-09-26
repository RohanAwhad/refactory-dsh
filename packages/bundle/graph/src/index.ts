/**
 * The interactive graph runner: boots one CEO Agent over the spine, drives it
 * to quiescence (the CEO walks `wf_next → wf_done/wf_verdict`, spawning
 * subagents for content nodes and letting the spine auto-run deterministic
 * nodes), flushes its Session, and reports the best score plus the session log.
 * @module @deepseek-ai/dsh-graph
 */

import { randomUUID } from 'node:crypto'
import { existsSync, readFileSync, readdirSync, renameSync } from 'node:fs'
import { join } from 'node:path'
import { createInterface } from 'node:readline'
import type { Context } from '@deepseek-ai/cordis'
import { brandString } from '@deepseek-ai/dsh-brand'
import { installModelSelection } from '@deepseek-ai/dsh-agent'
import type { Agent } from '@deepseek-ai/dsh-agent'
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

/** The latest turn's actual failure; a later successful retry clears an earlier one. */
function turnError(session: Session, firstSeq: SessionLogOffset): string | undefined {
  for (let seq = session.seq - 1; seq >= firstSeq; seq--) {
    const event = session.eventAt(SessionSeq(seq))
    if (event?.type !== 'turn/end') continue
    const reason = event.data.reason
    if (reason.kind === 'completed') return undefined
    if (reason.kind === 'error') return `${reason.error.code}: ${reason.error.message}`
    return `the model turn ended (${reason.kind})`
  }
  return 'the model turn recorded no completion'
}

/** The terminal the run writes to and reads from; the launcher supplies the process's. */
export interface Io {
  stdout: { write(chunk: string): unknown }
  stderr: { write(chunk: string): unknown }
  /** The person's terminal. A gate reads it too; see {@link chat}. */
  stdin: NodeJS.ReadableStream & { readableEnded?: boolean }
  exit(code: number): void
}

interface DefaultModelLike {
  currentSelection(): { provider: string; model: string }
}

/** One line the person typed, or `undefined` at end of input. */
function readLine(stdin: Io['stdin'], out: Io['stdout']): Promise<string | undefined> {
  // End of input is remembered by the stream itself: a second read after the first
  // exhausted it must not wait for a line that can never come. (A `readline` started
  // on an already-ended stream does not always see the `end` that already fired.)
  if (stdin.readableEnded === true) return Promise.resolve(undefined)
  if (process.stdin.isTTY) out.write('> ')
  const rl = createInterface({ input: stdin, output: process.stdin.isTTY ? process.stdout : undefined })
  return new Promise((resolve) => {
    let settled = false
    const done = (line: string | undefined): void => {
      if (settled) return
      settled = true
      rl.close()
      resolve(line)
    }
    rl.once('close', () => done(undefined))
    rl.question('', line => done(line))
  })
}

/**
 * The live session: print each turn, then let the person say something back.
 *
 * The first turn is the driving task the runner already sent. After it reaches
 * quiescence this prints the turn's assistant text and offers the terminal, so the
 * CEO's report of a node, a question or a summary appears while the graph is still
 * running rather than only in the final JSON. A line typed here is a follow-up user
 * message: the CEO can act on it with `wf_edit` + `wf_redo`, skip a node, or route a
 * gate. `/quit`, an empty line and end of input all end the conversation.
 *
 * It reads the terminal only while the agent is idle, and closes its reader before
 * handing the turn back, so a `user` gate's own prompt owns stdin while the answerer
 * asks — the two never read at the same time.
 */
async function chat(agent: Agent, startup: GraphStartupValues, io: Io, firstSeq: SessionLogOffset): Promise<void> {
  let seqBefore = firstSeq
  for (;;) {
    const turn = finalText(agent.session, seqBefore)
    if (turn !== '') io.stdout.write(`${turn}\n`)
    const failure = turnError(agent.session, seqBefore)
    if (failure !== undefined) io.stderr.write(`dsh: ${failure}\n`)
    // Quiescence is not completion: the cursor's own status on disk is what says the
    // graph finished, and there is nothing left to steer once it has.
    if (failure === undefined && spineStatus(startup.workdir)?.status === 'done') return
    const line = await readLine(io.stdin, io.stdout)
    if (line === undefined) return
    const typed = line.trim()
    if (typed === '' || typed === '/quit') return
    seqBefore = agent.session.seq
    agent.followup(createUserMessage({
      content: [{ type: 'text', text: typed }],
      source: { kind: 'user' },
    }))
    await agent.whenIdle()
  }
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
  let caughtError: string | undefined
  try {
    // A reused directory may carry another invocation's done marker. Keep it as
    // history, but require this CEO's workflow calls to write fresh status.
    const priorStatus = join(startup.workdir, '.refactory', 'spine-status.json')
    if (existsSync(priorStatus)) {
      renameSync(priorStatus, join(startup.workdir, '.refactory', `spine-status.previous-${agent.session.id}.json`))
    }
    agent.followup(createUserMessage({
      content: [{ type: 'text', text: startup.task }],
      source: { kind: 'user' },
    }))
    await agent.whenIdle()
    // The first turn is the driving task. In chat mode the turn's own text is printed
    // now, while the graph is still running, and the person may answer it.
    if (startup.chat) await chat(agent, startup, io, firstSeq)
  } catch (error: unknown) {
    caughtError = error instanceof Error ? error.message : String(error)
  }
  await sessions.flush(agent.session)

  const status = spineStatus(startup.workdir)
  const due = status?.nodes === undefined || status.nodes.length === 0
    ? ''
    : `: ${status.nodes.join(', ')} still due`
  const graphError = status === undefined
    ? 'the graph did not start: no readable workflow status was recorded'
    : status.status === 'done'
      ? undefined
      : `the graph did not finish (${status.status}${due})${status.error === undefined ? '' : ` — ${status.error}`}`
  const failure = caughtError ?? turnError(agent.session, firstSeq) ?? graphError
  const result = {
    ok: failure === undefined,
    status: status?.status ?? 'not-started',
    ...(failure === undefined ? {} : { error: failure }),
    mode: startup.mode,
    task: startup.taskName,
    budget: startup.budget,
    best_score: bestScore(startup.workdir, startup.mode),
    session_id: agent.session.id,
    workdir: startup.workdir,
    final: finalText(agent.session, firstSeq).slice(0, 400),
  }
  io.stdout.write(JSON.stringify(result, null, 2) + '\n')

  // A failed final turn (even beside an old done file), absent status, or an
  // unfinished cursor cannot certify completion. Live chat may retry before here.
  if (failure !== undefined) io.stderr.write(`dsh: ${failure}\n`)
  io.exit(failure === undefined ? 0 : 1)
}

/**
 * Mount the interactive runner. The spine row (in the patch) hands this app the
 * same graph + env it runs the CEO against, through {@link GRAPH_STARTUP_SERVICE}.
 * @param ctx - plugin context carrying core services and the launcher exit request.
 * @param io - the terminal, for tests; the process's own streams when omitted.
 */
export function apply(ctx: Context, io?: Io): void {
  const exit = ctx.get('appExit')
  if (exit === undefined) {
    throw new Error('graph-runner: the launcher must provide ctx.appExit before the tree mounts')
  }
  const streams: Io = io ?? {
    stdout: process.stdout,
    stderr: process.stderr,
    stdin: process.stdin,
    exit,
  }
  void run(ctx, streams).catch((error: unknown) => {
    streams.stderr.write(`dsh: ${error instanceof Error ? error.message : String(error)}\n`)
    streams.exit(1)
  })
}
