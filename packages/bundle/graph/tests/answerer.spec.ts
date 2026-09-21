/**
 * The terminal answerer for a `user` gate: replay, refuse, or ask.
 *
 * These hold the three things the spine relies on: a verdict the ledger already
 * holds is replayed without prompting, a non-terminal input asks nobody and
 * delegates so the spine refuses, and at a terminal the person's choice is what is
 * routed and what is written down.
 */
import { PassThrough } from 'node:stream'
import { spawnSync } from 'node:child_process'
import { Context } from '@deepseek-ai/cordis'
import UserQuestionService from '@deepseek-ai/dsh-user-questions'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { GRAPH_STARTUP_SERVICE, type GraphStartupValues } from '../src/startup.ts'
import { apply as applyAnswerer } from '../src/answerer.ts'

vi.mock('node:child_process', () => ({ spawnSync: vi.fn() }))

const spawn = vi.mocked(spawnSync)

/** A ledger read (`steer --run`) returning the verdicts given. */
function ledger(verdicts: Record<string, string>): void {
  spawn.mockImplementation(() => ({ status: 0, stdout: JSON.stringify({ verdicts }) }) as never)
}

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

beforeEach(() => { spawn.mockReset() })
afterEach(() => { vi.restoreAllMocks() })

describe('graph terminal answerer', () => {
  it('replays a verdict the run ledger already holds, without prompting', async () => {
    ledger({ approve: 'proceed' })
    const { ctx, dispose } = await bench()
    const answer = await ctx.userQuestions.ask({ questions: [QUESTION] })
    expect(answer.answers).toEqual([{ id: 'approve', selected: ['proceed'] }])
    // Only the ledger read ran; nothing was written back.
    expect(spawn).toHaveBeenCalledTimes(1)
    await dispose()
  })

  it('delegates when stdin is not a terminal, so the spine refuses', async () => {
    ledger({})
    const { ctx, dispose } = await bench()
    await expect(ctx.userQuestions.ask({ questions: [QUESTION] }))
      .rejects.toThrow(/no user-questions answerer/)
    await dispose()
  })

  it('asks the terminal, routes the chosen option and records it', async () => {
    ledger({})
    const terminal = fakeTerminal()
    try {
      const { ctx, dispose } = await bench()
      const asking = ctx.userQuestions.ask({ questions: [QUESTION] })
      await new Promise(resolve => setTimeout(resolve, 20))
      terminal.stdin.write('2\n')
      const answer = await asking
      expect(answer.answers).toEqual([{ id: 'approve', selected: ['halt'] }])
      expect(terminal.written.join('')).toContain('the person approves the draft')
      expect(spawn.mock.calls.some(call => (call[1] as string[]).includes('approve=halt'))).toBe(true)
      await dispose()
    } finally {
      terminal.restore()
    }
  })

  it('accepts a label, and re-asks after input that is neither an index nor a label', async () => {
    ledger({})
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
    ledger({})
    const ctx = new Context()
    await ctx.plugin(UserQuestionService)
    applyAnswerer(ctx)
    await expect(ctx.userQuestions.ask({ questions: [QUESTION] }))
      .rejects.toThrow(/no user-questions answerer/)
    await ctx.fiber.dispose()
  })

  it('refuses to route a verdict the ledger could not be read for', async () => {
    // A failed read is `{}`, not an answer: the gate is still refused.
    spawn.mockImplementation(() => ({ status: 1, stdout: null }) as never)
    const { ctx, dispose } = await bench()
    await expect(ctx.userQuestions.ask({ questions: [QUESTION] }))
      .rejects.toThrow(/no user-questions answerer/)
    await dispose()
  })

  it('treats an unreadable ledger payload and an empty verdict as nothing recorded', async () => {
    for (const stdout of ['not json', '{}', JSON.stringify({ verdicts: { approve: '' } })]) {
      spawn.mockImplementation(() => ({ status: 0, stdout }) as never)
      const { ctx, dispose } = await bench()
      await expect(ctx.userQuestions.ask({ questions: [QUESTION] }))
        .rejects.toThrow(/no user-questions answerer/)
      await dispose()
    }
  })

  it('takes a typed answer for a gate that offers no options, skipping blank lines', async () => {
    ledger({})
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
