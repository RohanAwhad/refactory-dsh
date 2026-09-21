/**
 * The person's answer at a `user` gate, on the terminal, for the graph profile.
 *
 * The spine asks the runtime for a `user` gate's verdict rather than trusting the
 * driving agent's word — a model that can route a gate without asking can invent the
 * answer. This is the refactory-side answerer for that request: it shows the gate at
 * the terminal, reads the person's choice, and writes it into the run's intent ledger
 * so `console steer --run <workdir>` shows it and a later run replays it.
 *
 * **It is inert without a person.** A captured or piped stdin is not a person, so the
 * answerer delegates (`next()`) rather than answering, and the spine refuses the gate.
 * A verdict already in the ledger is replayed without prompting: a resumed run is the
 * same run, not a second question.
 * @module @deepseek-ai/dsh-graph/src/answerer
 */

import { spawnSync } from 'node:child_process'
import { createInterface } from 'node:readline'
import { join } from 'node:path'
import type { Context } from '@deepseek-ai/cordis'
import type { AskUserQuestionAnswer, AskUserQuestionRequest } from '@deepseek-ai/dsh-user-questions'
import { GRAPH_STARTUP_SERVICE } from './startup.ts'
import type { GraphStartupValues } from './startup.ts'

/** Stable Cordis plugin name. */
export const name = 'graph-answerer'

/** The service a `user` gate is asked through. */
export const inject = ['userQuestions']

/** The verdicts the run's ledger already records, or `{}` when there are none. */
function recordedVerdicts(startup: GraphStartupValues): Record<string, string> {
  const python = join(startup.pythonBin, 'python')
  const result = spawnSync(
    python,
    ['-m', 'refactory_dsh.console', 'steer', '--run', startup.workdir],
    { encoding: 'utf8' },
  )
  if (result.status !== 0) return {}
  try {
    const payload = JSON.parse(result.stdout) as { verdicts?: Record<string, string> }
    return payload.verdicts ?? {}
  } catch {
    return {}
  }
}

/** Append the verdict to the ledger through the Python that owns its format. */
function recordVerdict(startup: GraphStartupValues, gate: string, verdict: string): void {
  const python = join(startup.pythonBin, 'python')
  spawnSync(
    python,
    ['-m', 'refactory_dsh.console', 'steer', '--run', startup.workdir, '--verdict', `${gate}=${verdict}`],
    { encoding: 'utf8' },
  )
}

/** Ask one question on the terminal and return the chosen option label. */
async function askTerminal(question: { id: string; question: string; options?: Array<{ label: string }> }): Promise<string> {
  const options = question.options ?? []
  process.stdout.write(`\n[gate ${question.id}] ${question.question}\n`)
  options.forEach((option, index) => {
    process.stdout.write(`  ${index + 1}. ${option.label}\n`)
  })
  const rl = createInterface({ input: process.stdin, output: process.stdout })
  try {
    for (;;) {
      const line = (await new Promise<string>((resolve) => { rl.question('> ', resolve) })).trim()
      if (line === '') continue
      const index = Number(line)
      const byIndex = Number.isInteger(index) && index >= 1 && index <= options.length
        ? options[index - 1]
        : undefined
      if (byIndex !== undefined) return byIndex.label
      const matched = options.find(option => option.label === line)
      if (matched !== undefined) return matched.label
      if (options.length === 0) return line
      process.stdout.write(`choose 1-${options.length}, or one of: ${options.map(o => o.label).join(', ')}\n`)
    }
  } finally {
    rl.close()
  }
}

/**
 * Mount the terminal answerer. Registered whenever the composition has the
 * user-questions service; it decides per request whether it can answer.
 * @param ctx - the graph profile's context.
 */
export function apply(ctx: Context): void {
  const startup = ctx.get(GRAPH_STARTUP_SERVICE) as GraphStartupValues | undefined

  ctx.on('user-questions/request', async (
    request: AskUserQuestionRequest,
    next: () => Promise<AskUserQuestionAnswer>,
  ): Promise<AskUserQuestionAnswer> => {
    if (startup === undefined) return next()
    const recorded = recordedVerdicts(startup)
    const answers = []
    for (const question of request.questions) {
      const replayed = recorded[question.id]
      if (replayed !== undefined && replayed !== '') {
        answers.push({ id: question.id, selected: [replayed] })
        continue
      }
      if (!process.stdin.isTTY) {
        // Nobody can be asked here. Delegating lets the spine refuse the gate with a
        // message that names it, rather than inventing a verdict.
        return next()
      }
      const chosen = await askTerminal(question)
      recordVerdict(startup, question.id, chosen)
      answers.push({ id: question.id, selected: [chosen] })
    }
    return { answers }
  })
}
