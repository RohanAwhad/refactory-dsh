// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, waitFor, within } from '@testing-library/react'
import { SteeringLedger } from '../src/client/SteeringLedger.tsx'
import { en } from '../src/client/locales.ts'
import { makeTranslate } from './translate.client.ts'
import { mountBody, TAB } from './mount.client.tsx'
import { FRAME, MODE, PACKAGE, STEERING, STRATEGIES, ok, failure } from './fake-console.client.ts'
import { mockDialog } from './dialog.client.ts'

const t = makeTranslate(en)
const history = { ...STEERING, entries: [
  { at: '2026-01-01T00:00:00+00:00', kind: 'Initial Idea', text: 'Original brief\n\nKeep these lines.\n  - Indented detail\n<script>literal text</script>' },
  ...Array.from({ length: 35 }, (_, index) => ({ at: `2026-01-01T00:${String(index + 1).padStart(2, '0')}:00+00:00`, kind: 'correction', text: `Earlier note ${index + 1}` })),
  { at: '2026-01-01T01:00:00+00:00', kind: 'verdict', text: 'gate: review\nverdict: proceed\nscope: past-review' },
  { at: '2026-01-01T01:01:00+00:00', kind: 'verdict-use', text: 'gate: review\nresult: used' },
] }
let restoreDialog = () => {}
beforeEach(() => { restoreDialog = mockDialog() })
afterEach(() => { cleanup(); vi.restoreAllMocks(); restoreDialog() })

it('opens every past entry in ledger order with complete multiline text and recorded timestamps', () => {
  const refresh = vi.fn()
  const view = render(<SteeringLedger steering={{ kind: 'ready', value: history }} refresh={refresh} t={t} />)
  expect(view.queryByRole('dialog')).toBeNull()
  fireEvent.click(view.getByRole('button', { name: /View full ledger/ }))
  const dialog = view.getByRole('dialog', { name: 'Steering ledger' })
  const rows = [...dialog.querySelectorAll('[data-refactory-entry]')]
  expect(rows).toHaveLength(history.entries.length)
  expect(rows.map(row => row.querySelector('p')?.textContent)).toEqual(history.entries.map(entry => entry.text))
  expect(rows.map(row => row.querySelector('time')?.dateTime)).toEqual(history.entries.map(entry => entry.at))
  expect(dialog.querySelector('script')).toBeNull()
  expect(refresh).not.toHaveBeenCalled()
})

it('searches old text, kinds and timestamps and returns to the complete history when cleared', () => {
  const view = render(<SteeringLedger steering={{ kind: 'ready', value: history }} refresh={() => {}} t={t} />)
  fireEvent.click(view.getByRole('button', { name: /View full ledger/ }))
  const search = view.getByRole('searchbox')
  fireEvent.change(search, { target: { value: 'ORIGINAL BRIEF' } })
  expect(view.getAllByRole('listitem')).toHaveLength(1)
  fireEvent.change(search, { target: { value: 'verdict-use' } })
  expect(view.getAllByRole('listitem')).toHaveLength(1)
  fireEvent.change(search, { target: { value: 'T00:02:' } })
  expect(view.getAllByRole('listitem')).toHaveLength(1)
  fireEvent.change(search, { target: { value: 'missing phrase' } })
  expect(view.getByText(en['ledger.noMatches'])).toBeDefined()
  fireEvent.change(search, { target: { value: '' } })
  expect(view.getAllByRole('listitem')).toHaveLength(history.entries.length)
})

it('shows a first-read failure without claiming the ledger is empty', () => {
  const view = render(<SteeringLedger steering={{ kind: 'failed', message: 'Unavailable' }} refresh={() => {}} t={t} />)
  fireEvent.click(view.getByRole('button', { name: /View full ledger/ }))
  expect(view.getByRole('alert').textContent).toContain('Unavailable')
  expect(within(view.getByRole('dialog')).queryByText(en['steering.empty'])).toBeNull()
})

it('keeps the dialog and past text through refresh, failure and a live append, without moving focus', () => {
  const refresh = vi.fn()
  const view = render(<SteeringLedger steering={{ kind: 'ready', value: history }} refresh={refresh} t={t} />)
  fireEvent.click(view.getByRole('button', { name: /View full ledger/ }))
  const dialog = view.getByRole('dialog'), search = view.getByRole('searchbox')
  search.focus()
  fireEvent.click(within(dialog).getByRole('button', { name: 'Refresh ledger' }))
  expect(refresh).toHaveBeenCalledOnce()
  view.rerender(<SteeringLedger steering={{ kind: 'loading' }} refresh={refresh} t={t} />)
  expect(view.getByRole('dialog')).toBe(dialog)
  expect(view.getAllByRole('listitem')).toHaveLength(history.entries.length)
  view.rerender(<SteeringLedger steering={{ kind: 'failed', message: 'Offline' }} refresh={refresh} t={t} />)
  expect(view.getByRole('alert').textContent).toContain('last successfully read history')
  expect(view.getAllByRole('listitem')).toHaveLength(history.entries.length)
  const updated = { ...history, entries: [...history.entries, { at: '2026-01-02T00:00:00Z', kind: 'constraint', text: 'Latest instruction' }] }
  view.rerender(<SteeringLedger steering={{ kind: 'ready', value: updated }} refresh={refresh} t={t} />)
  expect(view.getAllByRole('listitem')).toHaveLength(updated.entries.length)
  expect(document.activeElement).toBe(search)
  expect(view.queryByRole('alert')).toBeNull()
})

it.each(['button', 'Escape'])('closes with %s, restores trigger focus and resets search on reopening', method => {
  const view = render(<SteeringLedger steering={{ kind: 'ready', value: history }} refresh={() => {}} t={t} />)
  const trigger = view.getByRole('button', { name: /View full ledger/ })
  fireEvent.click(trigger)
  fireEvent.change(view.getByRole('searchbox'), { target: { value: 'Earlier note' } })
  if (method === 'button') fireEvent.click(view.getByRole('button', { name: 'Close ledger' }))
  else fireEvent(view.getByRole('dialog'), new Event('cancel', { cancelable: true }))
  expect(document.activeElement).toBe(trigger)
  expect(view.queryByRole('dialog')).toBeNull()
  fireEvent.click(trigger)
  expect((view.getByRole('searchbox') as HTMLInputElement).value).toBe('')
  expect(view.getAllByRole('listitem')).toHaveLength(history.entries.length)
})

const choices = { project: '/repo', modes: [MODE], packages: [PACKAGE], strategies: STRATEGIES, projects: [] }
it('preserves an unsaved note on failure, blocks double submission and clears only a confirmed draft', async () => {
  const { view, console } = mountBody({ choices: { kind: 'ready', value: choices }, watching: '/runs/live',
    frame: { kind: 'ready', value: FRAME }, steering: { kind: 'ready', value: STEERING }, dictionary: en })
  const note = view.getByLabelText('What you want to say') as HTMLTextAreaElement
  const text = 'Keep the old brief.\n\nAdd this correction.'
  fireEvent.change(note, { target: { value: text } })
  let fail!: (value: ReturnType<typeof failure>) => void
  console.steer.mockImplementationOnce(() => new Promise(resolve => { fail = resolve }))
  fireEvent.click(view.getByRole('button', { name: 'Add a note' }))
  expect((view.getByRole('button', { name: 'Recording…' }) as HTMLButtonElement).disabled).toBe(true)
  expect(note.value).toBe(text)
  await act(async () => { fail(failure('refactory/console-failed', 'Disk unavailable')) })
  expect(note.value).toBe(text)
  const saved = { ...STEERING, entries: [...STEERING.entries, { at: '2026-01-02', kind: 'info', text }] }
  console.steer.mockImplementationOnce(async () => ok(saved))
  fireEvent.click(view.getByRole('button', { name: 'Add a note' }))
  await waitFor(() => { expect(note.value).toBe('') })
  fireEvent.click(view.getByRole('button', { name: /View full ledger/ }))
  expect(within(view.getByRole('dialog')).getAllByRole('listitem')).toHaveLength(3)
  expect(within(view.getByRole('dialog')).getAllByRole('listitem')[2]?.textContent).toContain(text)
})

it('drops the open history when switching watched runs and never carries old text into the new ledger', () => {
  const { view, instance } = mountBody({ choices: { kind: 'ready', value: choices }, watching: '/runs/live',
    steering: { kind: 'ready', value: history }, dictionary: en })
  fireEvent.click(view.getByRole('button', { name: /View full ledger/ }))
  act(() => { instance.actions.watching(TAB, '/runs/other') })
  expect(view.queryByRole('dialog')).toBeNull()
  fireEvent.click(view.getByRole('button', { name: /View full ledger/ }))
  expect(view.getByRole('dialog').textContent).not.toContain('Original brief')
  expect(within(view.getByRole('dialog')).queryAllByRole('listitem')).toHaveLength(0)
})

it('does not clear a newer draft when the previous note finishes saving', async () => {
  const { view, console } = mountBody({ choices: { kind: 'ready', value: choices }, watching: '/runs/live',
    frame: { kind: 'ready', value: FRAME }, steering: { kind: 'ready', value: STEERING }, dictionary: en })
  const note = view.getByLabelText('What you want to say') as HTMLTextAreaElement
  let confirm!: (value: ReturnType<typeof ok>) => void
  console.steer.mockImplementationOnce(() => new Promise(resolve => { confirm = resolve }))
  fireEvent.change(note, { target: { value: 'First note' } })
  fireEvent.click(view.getByRole('button', { name: 'Add a note' }))
  fireEvent.change(note, { target: { value: 'Next draft' } })
  await act(async () => { confirm(ok({ ...STEERING, entries: [...STEERING.entries, { at: '2026-01-02', kind: 'info', text: 'First note' }] })) })
  expect(note.value).toBe('Next draft')
})
