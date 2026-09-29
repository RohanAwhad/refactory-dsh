/**
 * The terminal answerer for a `user` gate: ask, or delegate.
 *
 * These hold the transport's own contract. The spine scopes a recorded verdict to
 * one reviewed visit and replays it through its ledger bridge, so this transport
 * never replays history: each request is a fresh question. A non-terminal input
 * asks nobody and delegates so the spine refuses, and at a terminal the person's
 * choice is what is routed back for the spine to record.
 */
import { PassThrough } from 'node:stream'
import { Context } from '@deepseek-ai/cordis'
import UserQuestionService from '@deepseek-ai/dsh-user-questions'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { GRAPH_STARTUP_SERVICE, type GraphStartupValues } from '../src/startup.ts'
import { apply as applyAnswerer } from '../src/answerer.ts'

/** A Context with the user-questions service, a startup value, and the answerer. */
async function bench(): Promise<{ ctx: Context; dispose: () => Promise<void> }> {
  const ctx = new Context()
  await ctx.plugin(UserQuestionService)
  ctx.provide(GRAPH_STARTUP_SERVICE, { workdir: '/run', pythonBin: '/venv/bin' } as GraphStartupValues)
  applyAnswerer(ctx)
  return { ctx, dispose: async () => { await ctx.fiber.dispose() } }
}

const QUESTION = {
  id: 'approve',
  question: 'the person approves the draft',
  options: [{ label: 'proceed' }, { label: 'halt' }],
}

/** Replace process stdio for one test and restore it after. */
function fakeTerminal(): { stdin: PassThrough & { isTTY?: boolean }; written: string[]; restore: () => void } {
  const stdin = new PassThrough() as PassThrough & { isTTY?: boolean }
  stdin.isTTY = true
  const written: string[] = []
  const savedIn = process.stdin
  const savedOut = process.stdout
  Object.defineProperty(process, 'stdin', { value: stdin, configurable: true })
  Object.defineProperty(process, 'stdout', {
    value: { write: (chunk: string) => { written.push(chunk); return true } },
    configurable: true,
  })
  return {
    stdin,
    written,
    restore: () => {
      Object.defineProperty(process, 'stdin', { value: savedIn, configurable: true })
      Object.defineProperty(process, 'stdout', { value: savedOut, configurable: true })
    },
  }
}

afterEach(() => { vi.restoreAllMocks() })

describe('graph terminal answerer', () => {
  it('asks a fresh question each time instead of replaying an earlier answer', async () => {
    // The spine replays a recorded verdict for one reviewed visit through its own
    // ledger bridge; this transport keeps no history, so asking again prompts the
    // person again rather than answering from the previous choice.
    const terminal = fakeTerminal()
    try {
      const { ctx, dispose } = await bench()
      const first = ctx.userQuestions.ask({ questions: [QUESTION] })
      await new Promise(resolve => setTimeout(resolve, 20))
      terminal.stdin.write('1\n')
      expect((await first).answers).toEqual([{ id: 'approve', selected: ['proceed'] }])
      const second = ctx.userQuestions.ask({ questions: [QUESTION] })
      await new Promise(resolve => setTimeout(resolve, 20))
      terminal.stdin.write('2\n')
      expect((await second).answers).toEqual([{ id: 'approve', selected: ['halt'] }])
      expect(terminal.written.join('')).toContain('the person approves the draft')
      await dispose()
    } finally {
      terminal.restore()
    }
  })

  it('delegates when stdin is not a terminal, so the spine refuses', async () => {
    const { ctx, dispose } = await bench()
    await expect(ctx.userQuestions.ask({ questions: [QUESTION] }))
      .rejects.toThrow(/no user-questions answerer/)
    await dispose()
  })

  it('asks the terminal and routes the chosen option', async () => {
    const terminal = fakeTerminal()
    try {
      const { ctx, dispose } = await bench()
      const asking = ctx.userQuestions.ask({ questions: [QUESTION] })
      await new Promise(resolve => setTimeout(resolve, 20))
      terminal.stdin.write('2\n')
      const answer = await asking
      expect(answer.answers).toEqual([{ id: 'approve', selected: ['halt'] }])
      expect(terminal.written.join('')).toContain('the person approves the draft')
      await dispose()
    } finally {
      terminal.restore()
    }
  })

  it('accepts a label, and re-asks after input that is neither an index nor a label', async () => {
    const terminal = fakeTerminal()
    try {
      const { ctx, dispose } = await bench()
      const asking = ctx.userQuestions.ask({ questions: [QUESTION] })
      await new Promise(resolve => setTimeout(resolve, 20))
      terminal.stdin.write('nope\n')
      await new Promise(resolve => setTimeout(resolve, 20))
      terminal.stdin.write('proceed\n')
      const answer = await asking
      expect(answer.answers).toEqual([{ id: 'approve', selected: ['proceed'] }])
      expect(terminal.written.join('')).toContain('choose 1-2')
      await dispose()
    } finally {
      terminal.restore()
    }
  })

  it('delegates when the composition published no startup value', async () => {
    const ctx = new Context()
    await ctx.plugin(UserQuestionService)
    applyAnswerer(ctx)
    await expect(ctx.userQuestions.ask({ questions: [QUESTION] }))
      .rejects.toThrow(/no user-questions answerer/)
    await ctx.fiber.dispose()
  })

  it('takes a typed answer for a gate that offers no options, skipping blank lines', async () => {
    const terminal = fakeTerminal()
    try {
      const { ctx, dispose } = await bench()
      const asking = ctx.userQuestions.ask({
        questions: [{ id: 'note', question: 'anything to add?' }],
      })
      await new Promise(resolve => setTimeout(resolve, 20))
      terminal.stdin.write('\n')
      await new Promise(resolve => setTimeout(resolve, 20))
      terminal.stdin.write('looks good\n')
      const answer = await asking
      expect(answer.answers).toEqual([{ id: 'note', selected: ['looks good'] }])
      await dispose()
    } finally {
      terminal.restore()
    }
  })
})
