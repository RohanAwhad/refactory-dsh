/**
 * The person's answer at a `user` gate, on the terminal, for the graph profile.
 *
 * The spine asks the runtime for a `user` gate's verdict rather than trusting the
 * driving agent's word — a model that can route a gate without asking can invent the
 * answer. This is the refactory-side answerer for that request: it shows the gate at
 * the terminal and reads the person's choice. The workflow spine records and scopes
 * the answer through the ledger bridge shared with GUI questions.
 *
 * **It is inert without a person.** A captured or piped stdin is not a person, so the
 * answerer delegates (`next()`) rather than answering, and the spine refuses the gate.
 * This transport never replays history: each request is a fresh question.
 * @module @deepseek-ai/dsh-graph/src/answerer
 */

import { createInterface } from 'node:readline'
import type { Context } from '@deepseek-ai/cordis'
import type { AskUserQuestionAnswer, AskUserQuestionRequest } from '@deepseek-ai/dsh-user-questions'
import { GRAPH_STARTUP_SERVICE } from './startup.ts'
import type { GraphStartupValues } from './startup.ts'

/** Stable Cordis plugin name. */
export const name = 'graph-answerer'

/** The service a `user` gate is asked through. */
export const inject = ['userQuestions']

/** Read a multi-line note from the terminal, terminated by a line containing only `.`. */
async function readNote(rl: ReturnType<typeof createInterface>): Promise<string> {
  process.stdout.write('Add a note (end with a line containing only ".", or press Enter to skip):\n')
  const lines: string[] = []
  for (;;) {
    const line = await new Promise<string>((resolve) => { rl.question('', resolve) })
    // First line empty → skip note entirely
    if (lines.length === 0 && line === '') return ''
    // Lone dot terminates the note
    if (line === '.') return lines.join('\n')
    lines.push(line)
  }
}

/** Ask one question on the terminal and return the chosen option label, plus an optional note. */
async function askTerminal(question: { id: string; question: string; options?: Array<{ label: string }> }): Promise<{ selected: string; custom: string }> {
  const options = question.options ?? []
  process.stdout.write(`\n[gate ${question.id}] ${question.question}\n`)
  options.forEach((option, index) => {
    process.stdout.write(`  ${index + 1}. ${option.label}\n`)
  })
  const rl = createInterface({ input: process.stdin, output: process.stdout })
  try {
    let selected: string
    for (;;) {
      const line = (await new Promise<string>((resolve) => { rl.question('> ', resolve) })).trim()
      if (line === '') continue
      const index = Number(line)
      const byIndex = Number.isInteger(index) && index >= 1 && index <= options.length
        ? options[index - 1]
        : undefined
      if (byIndex !== undefined) { selected = byIndex.label; break }
      const matched = options.find(option => option.label === line)
      if (matched !== undefined) { selected = matched.label; break }
      if (options.length === 0) { selected = line; break }
      process.stdout.write(`choose 1-${options.length}, or one of: ${options.map(o => o.label).join(', ')}\n`)
    }
    // After choosing the verdict, offer a multi-line note.
    const custom = await readNote(rl)
    return { selected, custom }
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
    // Delegate unless this is a live chat session with a real terminal.
    // Headless mode (startup.chat === false) must never prompt, even at a TTY:
    // running `refactory ceo ... --headless` in tmux would otherwise prompt
    // instead of refusing.
    if (startup === undefined || !startup.chat || !process.stdin.isTTY) return next()
    const answers = []
    for (const question of request.questions) {
      const { selected, custom } = await askTerminal(question)
      answers.push({ id: question.id, selected: [selected], ...(custom ? { custom } : {}) })
    }
    return { answers }
  })
}
