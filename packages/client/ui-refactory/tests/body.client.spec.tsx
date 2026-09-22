// @vitest-environment jsdom
/**
 * The picker's body: what each read draws, and what each gesture sends.
 *
 * Props arrive the way the renderer composes them (a real store instance, a real
 * face over a scripted Remote), so a spec drives the component the way a person
 * does — choosing from a select, pressing a button — and reads the store and the
 * scripted Remote afterwards.
 */
import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, fireEvent, waitFor } from '@testing-library/react'
import { makeTranslate } from '@deepseek-ai/dsh-client-test-runtime'
import { failureLine } from '../src/client/RefactoryBody.tsx'
import { en, zh } from '../src/client/locales.ts'
import { AUTHORING, DISABLED, FRAME, MODE, NO_STRATEGIES, PACKAGE, RECEIPT, STRATEGIES, ok } from './fake-console.client.ts'
import { TAB, mountBody } from './mount.client.tsx'

const CHOICES = { modes: [MODE, AUTHORING, DISABLED], strategies: STRATEGIES, packages: [PACKAGE] }

afterEach(cleanup)

/** The one element a spec addresses by its data attribute. */
function at(view: { container: HTMLElement }, attribute: string, value?: string): HTMLElement {
  const selector = value === undefined ? `[${attribute}]` : `[${attribute}="${value}"]`
  const found = view.container.querySelector(selector)
  if (found === null) throw new Error(`no element for ${selector}`)
  return found as HTMLElement
}

/** Absent, as opposed to present and empty. */
function missing(view: { container: HTMLElement }, attribute: string, value?: string): boolean {
  const selector = value === undefined ? `[${attribute}]` : `[${attribute}="${value}"]`
  return view.container.querySelector(selector) === null
}

describe('failureLine', () => {
  it('names the three refusals the Host names precisely, in the reader\'s language', () => {
    const t = makeTranslate(zh)
    expect(failureLine(t, { code: 'refactory/unknown-mode', message: 'raw' })).toBe('这个模式不在目录里，请重新读取列表再选。')
    expect(failureLine(t, { code: 'refactory/authoring-mode', message: 'raw' })).toBe('这是创作模式，不是可以搜索的运行；请用它的创作动作。')
    expect(failureLine(t, { code: 'refactory/package-disabled', message: 'raw' })).toBe('这个模式的包已停用，请先启用它。')
  })

  it('shows the Host\'s own message for everything else', () => {
    const t = makeTranslate(zh)
    expect(failureLine(t, { code: 'refactory/console-failed', message: 'the console exited 2' }))
      .toBe('操作失败：the console exited 2')
  })
})

describe('the picker before it has read anything', () => {
  it('reads the choices on mount, which is the only read the body starts itself', async () => {
    const { console, instance } = mountBody({ bare: true })
    await waitFor(() => { expect(console.modes).toHaveBeenCalledTimes(1) })
    expect(console.strategies).toHaveBeenCalledTimes(1)
    expect(console.packages).toHaveBeenCalledTimes(1)
    expect(instance.getSnapshot().byTab[TAB]?.choices).toEqual({
      kind: 'ready',
      value: { modes: [], strategies: NO_STRATEGIES, packages: [] },
    })
  })

  it('says it is reading them', () => {
    const { view } = mountBody({ choices: { kind: 'loading' } })
    expect(at(view, 'data-refactory-state').getAttribute('data-refactory-state')).toBe('loading')
    expect(view.getByText(zh['choices.loading'])).toBeDefined()
  })

  it('says why the choices could not be read', () => {
    const { view } = mountBody({ choices: { kind: 'failed', message: 'the console exited 2' } })
    expect(at(view, 'data-refactory-state').getAttribute('data-refactory-state')).toBe('failed')
    expect(view.getByText('操作失败：the console exited 2')).toBeDefined()
  })
})

describe('the picker with the described choices', () => {
  it('offers every described mode, none chosen, and no run being read', () => {
    const { view } = mountBody({ choices: { kind: 'ready', value: CHOICES } })
    expect(at(view, 'data-refactory-state').getAttribute('data-refactory-state')).toBe('ready')
    const select = at(view, 'data-refactory-mode') as HTMLSelectElement
    expect(select.value).toBe('')
    expect([...select.options].map(option => option.value)).toEqual(['', 'p/adaptive', 'default_pack/create', 'off/adaptive'])
    expect(missing(view, 'data-refactory-start')).toBe(true)
    expect(view.getByText(zh['status.none'])).toBeDefined()
    expect(at(view, 'data-refactory-enabled', 'true').textContent).toContain('p')
    expect(at(view, 'data-refactory-package-mode', 'adaptive')).toBeDefined()
  })

  it('chooses a mode, shows what it is, and offers to start it', () => {
    const { view, instance } = mountBody({ choices: { kind: 'ready', value: CHOICES } })
    fireEvent.change(at(view, 'data-refactory-mode'), { target: { value: MODE.id } })
    expect(instance.getSnapshot().byTab[TAB]?.chosen).toBe(MODE.id)
    expect(at(view, 'data-refactory-mode-note').textContent).toBe(MODE.description)
    expect(missing(view, 'data-refactory-start')).toBe(false)
    expect(missing(view, 'data-refactory-authoring')).toBe(true)
    expect(missing(view, 'data-refactory-disabled')).toBe(true)
  })

  it('choosing nothing again drops the run button', () => {
    const { view, instance } = mountBody({ choices: { kind: 'ready', value: CHOICES }, chosen: MODE.id })
    fireEvent.change(at(view, 'data-refactory-mode'), { target: { value: '' } })
    expect(instance.getSnapshot().byTab[TAB]?.chosen).toBeNull()
    expect(missing(view, 'data-refactory-start')).toBe(true)
  })

  it('says which action runs an authoring mode', () => {
    const { view } = mountBody({ choices: { kind: 'ready', value: CHOICES }, chosen: AUTHORING.id })
    expect(at(view, 'data-refactory-mode-note').getAttribute('data-refactory-mode-note')).toBe('create')
    expect(at(view, 'data-refactory-authoring').textContent).toContain('create')
  })

  it('says when the chosen mode\'s package is disabled', () => {
    const { view } = mountBody({ choices: { kind: 'ready', value: CHOICES }, chosen: DISABLED.id })
    expect(at(view, 'data-refactory-disabled').textContent).toBe(zh['mode.disabled'])
  })

  it('falls back to the default line for a seam whose chosen name is no longer offered', () => {
    const { view } = mountBody({
      choices: { kind: 'ready', value: CHOICES },
      settings: { optimizer: 'gone' },
    })
    expect(at(view, 'data-refactory-setting-note', 'optimizer').textContent).toBe(zh['setting.default'])
  })

  it('starts the chosen mode with the settings that were chosen, and only those', async () => {
    const { view, console, instance } = mountBody({ choices: { kind: 'ready', value: CHOICES }, chosen: MODE.id })
    console.start.mockImplementation(async () => ok(RECEIPT) as never)
    console.status.mockImplementation(async () => ok(FRAME) as never)
    fireEvent.change(at(view, 'data-refactory-setting', 'optimizer'), { target: { value: 'random' } })
    expect(instance.getSnapshot().byTab[TAB]?.settings.optimizer).toBe('random')
    expect(at(view, 'data-refactory-setting-note', 'optimizer').textContent).toBe('sample uniformly')
    fireEvent.click(at(view, 'data-refactory-start'))
    await waitFor(() => { expect(console.start).toHaveBeenCalledTimes(1) })
    expect(console.start).toHaveBeenCalledWith({ mode: MODE.id, settings: { optimizer: 'random' } }, expect.anything())
    await waitFor(() => { expect(console.status).toHaveBeenCalledTimes(1) })
  })

  it('says a seam is on its default, and names the outer loops it offers', () => {
    const { view, instance } = mountBody({ choices: { kind: 'ready', value: CHOICES } })
    expect(at(view, 'data-refactory-setting-note', 'strategy').textContent).toBe(zh['setting.default'])
    expect(at(view, 'data-refactory-setting-note', 'evaluator').textContent).toBe(zh['setting.default'])
    const strategy = at(view, 'data-refactory-setting', 'strategy') as HTMLSelectElement
    expect([...strategy.options].map(option => option.value)).toEqual(['', 'beam'])
    fireEvent.change(strategy, { target: { value: 'beam' } })
    expect(instance.getSnapshot().byTab[TAB]?.settings.strategy).toBe('beam')
    expect(at(view, 'data-refactory-setting-note', 'strategy').textContent).toBe('keep the best few')
    fireEvent.change(at(view, 'data-refactory-setting', 'evaluator'), { target: { value: 'score' } })
    expect(instance.getSnapshot().byTab[TAB]?.settings.evaluator).toBe('score')
    expect(at(view, 'data-refactory-setting-note', 'evaluator').textContent).toBe("the mode's own score")
    const evaluator = at(view, 'data-refactory-setting', 'evaluator') as HTMLSelectElement
    expect([...evaluator.options].map(option => option.value)).toEqual(['', 'score'])
  })

  it('reads the choices again when the reader asks it to', async () => {
    const { view, console } = mountBody({ choices: { kind: 'ready', value: CHOICES } })
    fireEvent.click(at(view, 'data-refactory-reload'))
    await waitFor(() => { expect(console.modes).toHaveBeenCalledTimes(1) })
  })

  it('shows a refusal above the picker, in terms of what the reader did', () => {
    const { view } = mountBody({
      choices: { kind: 'ready', value: CHOICES },
      notice: { code: 'refactory/authoring-mode', message: 'raw host text' },
    })
    const notice = at(view, 'data-refactory-notice')
    expect(notice.getAttribute('data-refactory-notice')).toBe('refactory/authoring-mode')
    expect(notice.textContent).toBe(zh['error.authoringMode'])
  })
})

describe('the run the picker is reading', () => {
  it('says it is reading while the first frame is on its way', () => {
    const { view } = mountBody({ choices: { kind: 'ready', value: CHOICES }, watching: '/runs/live' })
    expect(at(view, 'data-refactory-status', 'reading').textContent).toBe(zh['status.reading'])
  })

  it('says why a status could not be read', () => {
    const { view } = mountBody({
      choices: { kind: 'ready', value: CHOICES },
      watching: '/runs/live',
      frame: { kind: 'failed', message: '/runs/live is not a run directory' },
    })
    expect(at(view, 'data-refactory-status', 'failed').textContent).toContain('/runs/live is not a run directory')
  })

  it('reports the run, its score, and how much it has evaluated', () => {
    const { view } = mountBody({
      choices: { kind: 'ready', value: CHOICES },
      watching: '/runs/live',
      frame: { kind: 'ready', value: FRAME },
    })
    expect(at(view, 'data-refactory-run').textContent).toBe('/runs/live')
    expect(at(view, 'data-refactory-status', 'running')).toBeDefined()
    expect(view.getByText(`${zh['status.state']}: running`)).toBeDefined()
    expect(at(view, 'data-refactory-score').textContent).toBe('最好成绩 1.25')
    expect(at(view, 'data-refactory-evaluations').textContent).toBe('已评估 2 次')
    expect(missing(view, 'data-refactory-stop')).toBe(false)
  })

  it('says a run has not scored rather than showing a zero', () => {
    const { view } = mountBody({
      choices: { kind: 'ready', value: CHOICES },
      watching: '/runs/live',
      frame: { kind: 'ready', value: { ...FRAME, best_score: null, finished: true } },
    })
    expect(at(view, 'data-refactory-score').textContent).toBe(zh['status.noScore'])
    expect(missing(view, 'data-refactory-stop')).toBe(true)
  })

  it('reads the next frame from where the last one reached, and asks the run to stop', async () => {
    const { view, console } = mountBody({
      choices: { kind: 'ready', value: CHOICES },
      watching: '/runs/live',
      frame: { kind: 'ready', value: FRAME },
    })
    console.status.mockImplementation(async () => ok(FRAME) as never)
    console.stop.mockImplementation(async () => ok({ stopped: true }) as never)
    fireEvent.click(at(view, 'data-refactory-refresh'))
    await waitFor(() => { expect(console.status).toHaveBeenCalledWith('/runs/live', FRAME.cursor, expect.anything()) })
    fireEvent.click(at(view, 'data-refactory-stop'))
    await waitFor(() => { expect(console.stop).toHaveBeenCalledWith('/runs/live', expect.anything()) })
  })
})

describe('the packages the picker manages', () => {
  it('offers to disable an enabled package and to enable a disabled one', async () => {
    const { view, console } = mountBody({
      choices: { kind: 'ready', value: { ...CHOICES, packages: [PACKAGE, { ...PACKAGE, name: 'off', enabled: false }] } },
    })
    console.setPackageEnabled.mockImplementation(async () => ok([]) as never)
    expect(at(view, 'data-refactory-toggle', 'p').textContent).toBe(zh['packages.disable'])
    expect(at(view, 'data-refactory-toggle', 'off').textContent).toBe(zh['packages.enable'])
    fireEvent.click(at(view, 'data-refactory-toggle', 'off'))
    await waitFor(() => { expect(console.setPackageEnabled).toHaveBeenCalledWith('off', true, expect.anything()) })
  })

  it('offers no toggle for a package that reported no state', () => {
    const { view } = mountBody({
      choices: { kind: 'ready', value: { ...CHOICES, packages: [{ ...PACKAGE, enabled: null, modes: [] }] } },
    })
    expect(missing(view, 'data-refactory-toggle')).toBe(true)
    expect(at(view, 'data-refactory-enabled', 'unknown').textContent).toContain(zh['packages.unknown'])
    expect(view.getByText('0 个模式')).toBeDefined()
  })

  it('waits out a package change, keeping its buttons unpressable', () => {
    const { view } = mountBody({
      choices: { kind: 'ready', value: CHOICES },
      busy: true,
    })
    expect((at(view, 'data-refactory-remove', 'p') as HTMLButtonElement).disabled).toBe(true)
    expect((at(view, 'data-refactory-import') as HTMLButtonElement).disabled).toBe(true)
  })

  it('removes a package', async () => {
    const { view, console } = mountBody({ choices: { kind: 'ready', value: CHOICES } })
    console.removePackage.mockImplementation(async () => ok([]) as never)
    fireEvent.click(at(view, 'data-refactory-remove', 'p'))
    await waitFor(() => { expect(console.removePackage).toHaveBeenCalledWith('p', expect.anything()) })
  })

  it('imports from a directory or git URL, clearing the fields it sent', async () => {
    const { view, console } = mountBody({ choices: { kind: 'ready', value: CHOICES } })
    console.importPackage.mockImplementation(async () => ok([PACKAGE]) as never)
    const source = at(view, 'data-refactory-import-source') as HTMLInputElement
    const name = at(view, 'data-refactory-import-name') as HTMLInputElement
    expect((at(view, 'data-refactory-import') as HTMLButtonElement).disabled).toBe(true)
    fireEvent.change(source, { target: { value: '/src/pack' } })
    fireEvent.change(name, { target: { value: 'renamed' } })
    expect((at(view, 'data-refactory-import') as HTMLButtonElement).disabled).toBe(false)
    fireEvent.submit(at(view, 'data-refactory-import').closest('form') ?? view.container)
    await waitFor(() => { expect(console.importPackage).toHaveBeenCalledWith('/src/pack', 'renamed', expect.anything()) })
    expect(source.value).toBe('')
    expect(name.value).toBe('')
  })

  it('draws every string from the dictionary the renderer bound', () => {
    const english = mountBody({ choices: { kind: 'ready', value: CHOICES }, dictionary: en })
    expect(english.view.getByText(en['run.heading'])).toBeDefined()
    expect(english.view.getByText(en['packages.heading'])).toBeDefined()
    // Three seams, so the default is named once per select and once per note.
    expect(english.view.getAllByText(en['setting.default'])).toHaveLength(6)
    expect(english.view.queryByText(zh['setting.default'])).toBeNull()
  })
})
