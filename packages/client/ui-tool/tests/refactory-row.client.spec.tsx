// @vitest-environment jsdom
/** refactory atomic Tool presentation: the run view a person reads in the conversation. */
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { ToolResultNode } from '@deepseek-ai/dsh-client-ui-chat/client'
import { makeTranslate } from '@deepseek-ai/dsh-client-test-runtime'
import { zh as commonZh } from '@deepseek-ai/dsh-client-locale/src/locales/zh.ts'
import { zh } from '@deepseek-ai/dsh-client-ui-conversation/src/client/locales.ts'
import { RefactoryRow } from '../src/client/tool/toolviews/refactory-row.tsx'
import {
  refactoryAction,
  refactoryCardModel,
  refactoryMeta,
} from '../src/client/tool/models/refactory-card-model.ts'

type RefactoryRowProps = Parameters<typeof RefactoryRow>[0]

const t: RefactoryRowProps['t'] = makeTranslate(zh, commonZh)

afterEach(cleanup)

/**
 * Build the settled result node the Host writes for one call: the model-facing
 * summary text in `content`, and the console payload in `meta` — the pair
 * `output.render` and `output.presentationMeta` produce.
 */
function resultNode(
  args: Record<string, unknown>,
  action: string,
  data: unknown,
  over?: Partial<ToolResultNode>,
): ToolResultNode {
  return {
    kind: 'tool-result', seq: 10, time: 2_000, callTime: 1_000, callId: 'c1',
    call: { name: 'refactory', argsRaw: JSON.stringify(args) },
    content: [{ type: 'text', text: `refactory ${action} summary` }],
    isError: false, subCalls: [], meta: { action, data }, ...over,
  }
}

function rowProps(block: unknown): RefactoryRowProps {
  return {
    callId: 'c1', toolName: 'refactory', block,
    openFile: vi.fn(),
    sessionId: 's1',
    useSessions: () => undefined,
    t,
  } as unknown as RefactoryRowProps
}

const MODES = [
  { id: 'circle_packing/adaptive', startable: true, parameters: { grid: {}, order: {} } },
  { id: 'annealing_pack/anneal', startable: false, parameters: {} },
]

const STATUS = {
  run_id: 'run-1',
  mode: 'circle_packing/adaptive',
  state: 'running',
  best_score: 1.5,
  counters: { epoch: 2, generation: 3, evaluations: 7, steps: 9 },
  events: [
    { step: 'forward', candidate: 'abc', score: 1.5 },
    { step: 'loss', best: 1.5 },
  ],
}

describe('refactoryAction / refactoryMeta', () => {
  it('reads the action from the call arguments', () => {
    expect(refactoryAction(resultNode({ action: 'status' }, 'status', STATUS))).toBe('status')
  })

  it('refuses arguments that are not this tool\'s', () => {
    const other: ToolResultNode = {
      ...resultNode({ action: 'status' }, 'status', STATUS),
      call: { name: 'bash', argsRaw: JSON.stringify({ action: 'status' }) },
    }
    expect(refactoryAction(other)).toBeNull()
  })

  it('ignores an action it does not have', () => {
    expect(refactoryAction(resultNode({ action: 'explode' }, 'status', STATUS))).toBeNull()
  })

  it('returns null metadata on a running call, malformed metadata, and unknown actions', () => {
    const running = { callId: 'c1', name: 'refactory', argsRaw: JSON.stringify({ action: 'status' }), turn: 1, step: 1, time: 1, subCalls: [] }
    expect(refactoryMeta(running as never)).toBeNull()
    expect(refactoryMeta(resultNode({ action: 'status' }, 'status', STATUS, { meta: 'nope' }))).toBeNull()
    expect(refactoryMeta(resultNode({ action: 'status' }, 'explode', STATUS))).toBeNull()
  })
})

describe('refactoryCardModel', () => {
  it('is null for a call this view does not own', () => {
    const other: ToolResultNode = {
      ...resultNode({ action: 'status' }, 'status', STATUS),
      call: { name: 'bash', argsRaw: '{}' },
    }
    expect(refactoryCardModel(other, t)).toBeNull()
  })

  it('names the run, its state and its best score for a status call', () => {
    const card = refactoryCardModel(resultNode({ action: 'status', run: 'run-1' }, 'status', STATUS), t)!
    expect(card.title).toBe('refactory 运行 status')
    expect(card.summary).toBe('状态: 运行中')
    expect(card.body.join('\n')).toContain('最佳 1.5')
    expect(card.body.join('\n')).toContain('第 2 轮 · 第 3 代 · 7 次评估')
    expect(card.body.join('\n')).toContain('forward · abc · 最佳 1.5')
  })

  it('says a run has not scored rather than showing a zero', () => {
    const card = refactoryCardModel(
      resultNode({ action: 'status' }, 'status', { ...STATUS, best_score: null }), t,
    )!
    expect(card.body.join('\n')).toContain('尚无分数')
  })

  it('marks which modes can actually be started', () => {
    const card = refactoryCardModel(resultNode({ action: 'modes' }, 'modes', MODES), t)!
    expect(card.body[0]).toBe('circle_packing/adaptive · 2 个可调参数')
    expect(card.body[1]).toContain('不可启动')
  })

  it('lists runs with their score and whether they are still going', () => {
    const card = refactoryCardModel(resultNode(
      { action: 'runs' },
      'runs',
      [
        { run_id: 'a', mode: 'm/n', best_score: 1.5, finished: true },
        { run_id: 'b', mode: 'm/n', best_score: null, finished: false },
      ],
    ), t)!
    expect(card.body).toEqual([
      'a · m/n · 最佳 1.5 · 已完成',
      'b · m/n · 尚无分数 · 进行中',
    ])
  })

  it('reports a run that was started by naming its id', () => {
    const card = refactoryCardModel(
      resultNode({ action: 'start' }, 'start', { run_id: 'run-9', mode: 'm/n' }), t,
    )!
    expect(card.body).toEqual(['模式: m/n', '运行: run-9'])
  })

  it('marks which artifacts exist and leaves the rest unmarked', () => {
    const card = refactoryCardModel(resultNode({ action: 'artifacts' }, 'artifacts', {
      artifacts: [
        { name: 'report', media_type: 'text/markdown', exists: true },
        { name: 'strategy', media_type: 'text/plain', exists: false },
      ],
    }), t)!
    expect(card.body).toEqual(['✓ report (text/markdown)', '· strategy (text/plain)'])
  })

  it('shows each candidate with what it changed against its parent', () => {
    const card = refactoryCardModel(resultNode({ action: 'leaderboard' }, 'leaderboard', [
      { id: 'abc', score: 1.5, parent: 'seed', diff: { knobs: { grid: { before: 18, after: 24 } }, nodes: { added: [], removed: [] }, edges: { added: [], removed: [] } } },
    ]), t)!
    expect(card.body[0]).toBe('abc · 最佳 1.5')
    expect(card.body[1]).toBe('  grid: 18 → 24')
  })

  it('names added nodes and edge counts in a candidate diff', () => {
    const card = refactoryCardModel(resultNode({ action: 'leaderboard' }, 'leaderboard', [
      {
        id: 'abc',
        score: 1,
        diff: {
          knobs: {},
          nodes: { added: ['critic'], removed: ['old'], changed: [] },
          edges: { added: [['a', 'b', '']], removed: [] },
        },
      },
    ]), t)!
    expect(card.body).toContain('  + nodes: critic')
    expect(card.body).toContain('  - nodes: old')
    expect(card.body).toContain('  + edges: 1')
  })

  it('names the seed when a candidate has no parent', () => {
    const card = refactoryCardModel(
      resultNode({ action: 'candidate', candidate: 'abc' }, 'candidate', {
        candidate: 'abc', parent: null, seed: true, rendered: '参数:\n- grid: 18 → 24',
      }), t,
    )!
    expect(card.body[0]).toBe('abc ← 初始状态')
    expect(card.body[1]).toBe('参数:')
  })

  it('says a stop request was accepted, and why one was not', () => {
    expect(refactoryCardModel(resultNode({ action: 'stop' }, 'stop', { stopped: true }), t)!.body)
      .toEqual(['已请求停止'])
    expect(refactoryCardModel(resultNode({ action: 'stop' }, 'stop', { stopped: false, reason: 'not running' }), t)!.body)
      .toEqual(['原因: not running'])
  })

  it('falls back to the summary text when the payload is absent', () => {
    const card = refactoryCardModel(
      resultNode({ action: 'status' }, 'status', STATUS, { meta: undefined }), t,
    )!
    expect(card.summary).toBe('refactory status summary')
  })

  it('falls back to the call args when there is no result yet', () => {
    const running = {
      callId: 'c1', name: 'refactory', argsRaw: JSON.stringify({ action: 'modes' }),
      turn: 1, step: 1, time: 1, subCalls: [],
    }
    const card = refactoryCardModel(running, t)!
    expect(card.title).toBe('refactory 运行 modes')
    expect(card.body).toEqual([])
  })

  it('renders the ledger, its verdicts and its problems for a steer call', () => {
    const card = refactoryCardModel(resultNode({ action: 'steer', run: 'r1' }, 'steer', {
      exists: true,
      entries: [
        { at: 't1', kind: 'constraint', text: 'no numpy' },
        { at: 't2', kind: 'verdict', text: 'gate: approve' },
      ],
      verdicts: { approve: 'proceed' },
      problems: ['the ledger origin is empty'],
    }), t)!
    expect(card.body).toContain('[constraint] no numpy')
    expect(card.body).toContain('approve=proceed')
    expect(card.body).toContain('the ledger origin is empty')
  })

  it('names the winner, and the package an adoption wrote', () => {
    expect(refactoryCardModel(
      resultNode({ action: 'best', run: 'r1' }, 'best', { candidate: 'abc', score: 2 }), t,
    )!.body).toEqual(['abc · 最佳 2'])

    const adopted = refactoryCardModel(resultNode({ action: 'adopt', run: 'r1' }, 'adopt', {
      mode: 'new/adaptive', adopted_from: 'circle_packing/adaptive', score: 2,
    }), t)!
    expect(adopted.body[0]).toBe('new/adaptive ← circle_packing/adaptive')
  })

  it('renders a forwarded action by its own output, and a failure as one', () => {
    const listed = refactoryCardModel(resultNode({ action: 'registry' }, 'registry', {
      ok: true, exit_code: 0, result: { output: 'circle_packing/\nreview_pack/' },
    }), t)!
    expect(listed.body).toEqual(['已完成', 'circle_packing/', 'review_pack/'])

    const failed = refactoryCardModel(resultNode({ action: 'create' }, 'create', {
      ok: false, exit_code: 2, result: { package: 'new_pkg' },
    }), t)!
    expect(failed.body[0]).toBe('已结束')
    expect(failed.body[1]).toContain('new_pkg')
  })

  it('lists the named level-2 seams with what each one is', () => {
    const card = refactoryCardModel(resultNode({ action: 'strategies' }, 'strategies', {
      outer_loops: [{ name: 'reflective', description: 'reflective evolution' }],
      optimizers: [{ name: 'random', description: 'sample the catalogue' }],
      evaluators: [{ name: 'walker', description: 'run in this process' }],
    }), t)!
    expect(card.body).toContain('reflective: reflective evolution')
    expect(card.body).toContain('random: sample the catalogue')
    expect(card.body).toContain('walker: run in this process')
  })
})

describe('RefactoryRow', () => {
  it('renders the run view and expands to the payload body', () => {
    const { container } = render(<RefactoryRow {...rowProps(resultNode({ action: 'status', run: 'run-1' }, 'status', STATUS))} />)
    expect(screen.getByText('refactory 运行 status')).toBeTruthy()
    expect(screen.getByText('状态: 运行中')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { expanded: false }))
    expect(container.textContent).toContain('最佳 1.5')
  })

  it('keeps a failure visible as the row summary', () => {
    const failed = resultNode({ action: 'status' }, 'status', STATUS, {
      isError: true, error: { name: 'Error', code: 'tool_error' },
    })
    const { container } = render(<RefactoryRow {...rowProps(failed)} />)
    expect(container.querySelector('[data-state="error"]')).not.toBeNull()
  })

  it('falls back to the generic row for a call it does not own', () => {
    const other: ToolResultNode = {
      ...resultNode({ action: 'status' }, 'status', STATUS),
      call: { name: 'bash', argsRaw: '{"command":"ls"}' },
    }
    const { container } = render(<RefactoryRow {...rowProps(other)} />)
    expect(container.querySelector('[data-state="ok"]')).not.toBeNull()
    expect(container.textContent).toContain('工具调用')
  })
})
