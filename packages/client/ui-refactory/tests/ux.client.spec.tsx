// @vitest-environment jsdom
import { useState } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { BriefEditor } from '../src/client/BriefEditor.tsx'
import { FieldLabel } from '../src/client/FieldHelp.tsx'
import { en } from '../src/client/locales.ts'
import { makeTranslate } from './translate.client.ts'
import { mountBody, TAB } from './mount.client.tsx'
import { AUTHORING, FRAME, MODE, PACKAGE, RECEIPT, STRATEGIES, ok } from './fake-console.client.ts'

const dialogMethods = Object.fromEntries(['showModal', 'close'].map(name => [name, Object.getOwnPropertyDescriptor(HTMLDialogElement.prototype, name)]))
const choices = { project: '/repo', modes: [MODE, AUTHORING], packages: [PACKAGE], strategies: STRATEGIES }
afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  for (const [name, descriptor] of Object.entries(dialogMethods)) {
    if (descriptor) Object.defineProperty(HTMLDialogElement.prototype, name, descriptor)
    else Reflect.deleteProperty(HTMLDialogElement.prototype, name)
  }
})

it('separates launch actions without losing inputs, search settings or watched run', async () => {
  const { view, instance, console } = mountBody({ choices: { kind: 'ready', value: choices }, chosen: MODE.id,
    watching: '/runs/keep', dictionary: en })
  const runTab = view.getByRole('tab', { name: 'Run', selected: true })
  const optimizeTab = view.getByRole('tab', { name: 'Optimize' })
  expect(view.queryByRole('button', { name: 'Start optimization' })).toBeNull()
  expect(view.getByRole('button', { name: 'Run once' })).toBeDefined()
  const rounds = view.getByLabelText('rounds')
  fireEvent.change(rounds, { target: { value: '7' } })
  fireEvent.keyDown(runTab, { key: 'ArrowRight' })
  expect(optimizeTab.getAttribute('aria-selected')).toBe('true')
  expect(instance.getSnapshot().byTab[TAB]?.setupIntent).toBe('optimize')
  expect(document.activeElement).toBe(optimizeTab)
  expect(view.queryByRole('button', { name: 'Run once' })).toBeNull()
  const limit = view.getByLabelText(en['budget.max_evaluations'])
  fireEvent.change(limit, { target: { value: '3' } })
  fireEvent.click(runTab)
  expect(view.queryByRole('button', { name: 'Start optimization' })).toBeNull()
  fireEvent.click(optimizeTab)
  expect(view.getByLabelText('rounds')).toBe(rounds)
  expect(view.getByLabelText(en['budget.max_evaluations'])).toBe(limit)
  expect((limit as HTMLInputElement).value).toBe('3')
  expect(instance.getSnapshot().byTab[TAB]?.watching).toBe('/runs/keep')
  expect(console.start).not.toHaveBeenCalled()
  console.start.mockImplementation(async () => ok(RECEIPT))
  console.status.mockImplementation(async () => ok(FRAME))
  fireEvent.click(view.getByRole('button', { name: 'Start optimization' }))
  await waitFor(() => { expect(console.start).toHaveBeenCalledOnce() })
  expect(console.start.mock.calls[0]?.[0]).toMatchObject({ mode: MODE.id, settings: { max_evaluations: 3 } })
  expect(console.start.mock.calls[0]?.[0].knobs).toContain('rounds=7')
})

it('explains why an authoring mode cannot be optimized', () => {
  const { view } = mountBody({ choices: { kind: 'ready', value: choices }, chosen: AUTHORING.id, dictionary: en })
  fireEvent.click(view.getByRole('tab', { name: 'Optimize' }))
  expect(view.getByText(en['setup.scoredOnly'])).toBeDefined()
  expect((view.getByRole('button', { name: 'Start optimization' }) as HTMLButtonElement).disabled).toBe(true)
})

it('makes field help available on focus, click and hover and dismisses it on Escape', () => {
  render(<><FieldLabel htmlFor="name" help="The saved recipe name.">Name</FieldLabel><input id="name" /></>)
  expect(screen.getByLabelText('Name')).toBeDefined()
  const help = screen.getByRole('button', { name: 'Name — help' })
  fireEvent.focus(help)
  expect(screen.getByRole('tooltip').textContent).toBe('The saved recipe name.')
  fireEvent.keyDown(document, { key: 'Escape' })
  expect(screen.queryByRole('tooltip')).toBeNull()
  fireEvent.click(help)
  expect(screen.getByRole('tooltip')).toBeDefined()
  fireEvent.blur(help)
  fireEvent.mouseEnter(help)
  expect(screen.getByRole('tooltip')).toBeDefined()
  fireEvent.scroll(window)
  expect(screen.queryByRole('tooltip')).toBeNull()
})

describe('brief draft editor', () => {
  function setup() {
    // jsdom does not implement the native dialog's top layer; native GUI checks cover focus trapping.
    Object.defineProperty(HTMLDialogElement.prototype, 'showModal', { configurable: true, value: function (this: HTMLDialogElement) { this.open = true } })
    Object.defineProperty(HTMLDialogElement.prototype, 'close', { configurable: true, value: function (this: HTMLDialogElement) { this.open = false } })
    const saved = vi.fn()
    function Harness() {
      const [value, setValue] = useState('Original brief')
      return <BriefEditor id="brief" value={value} description="Describe the goal." t={makeTranslate(en)}
        onChange={value => { saved(value); setValue(value) }} />
    }
    const view = render(<Harness />)
    fireEvent.click(view.getByRole('button', { name: /Original brief/ }))
    return { view, saved }
  }

  it('applies multiline text and opens the saved text again', () => {
    const { view, saved } = setup()
    const editor = view.getByRole('textbox', { name: 'Edit brief' })
    fireEvent.change(editor, { target: { value: 'Goal\n\nConstraints and outputs' } })
    expect(saved).not.toHaveBeenCalled()
    fireEvent.click(view.getByRole('button', { name: 'Apply brief' }))
    expect(saved).toHaveBeenCalledExactlyOnceWith('Goal\n\nConstraints and outputs')
    const trigger = view.getByRole('button', { name: /Goal Constraints and outputs/ })
    expect(document.activeElement).toBe(trigger)
    fireEvent.click(trigger)
    expect((view.getByRole('textbox', { name: 'Edit brief' }) as HTMLTextAreaElement).value).toBe('Goal\n\nConstraints and outputs')
  })

  it.each(['Cancel', 'Escape'])('discards an unapplied draft with %s', action => {
    const { view, saved } = setup()
    fireEvent.change(view.getByRole('textbox'), { target: { value: 'Discard me' } })
    if (action === 'Cancel') fireEvent.click(view.getByRole('button', { name: 'Cancel' }))
    else fireEvent(view.getByRole('dialog'), new Event('cancel', { bubbles: false, cancelable: true }))
    expect(saved).not.toHaveBeenCalled()
    fireEvent.click(view.getByRole('button', { name: /Original brief/ }))
    expect((view.getByRole('textbox') as HTMLTextAreaElement).value).toBe('Original brief')
  })

  it('supports the explicit apply shortcut', () => {
    const { view, saved } = setup()
    const editor = view.getByRole('textbox')
    fireEvent.change(editor, { target: { value: 'Applied with keyboard' } })
    fireEvent.keyDown(editor, { key: 'Enter', ctrlKey: true })
    expect(saved).toHaveBeenCalledExactlyOnceWith('Applied with keyboard')
  })
})
