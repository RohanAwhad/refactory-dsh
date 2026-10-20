// @vitest-environment jsdom
/**
 * The target picker: the registered projects appear in the GUI, and picking one
 * sets the `target` knob in the start request (#351).
 *
 * The picker shows only for modes that declare `target` (design, design_fast).
 * Modes that do not declare it (create, edit) do not show the picker.
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent } from '@testing-library/react'
import {
  AUTHORING, DESIGN, MODE, PACKAGE, PROJECT, STRATEGIES,
} from './fake-console.client.ts'
import { TAB, mountBody } from './mount.client.tsx'

const CHOICES = {
  project: '/repo',
  modes: [DESIGN, MODE, AUTHORING],
  strategies: STRATEGIES,
  packages: [PACKAGE],
  projects: [PROJECT],
}

afterEach(() => { cleanup(); vi.restoreAllMocks() })

function at(view: { container: HTMLElement }, attribute: string, value?: string): HTMLElement | null {
  const selector = value === undefined ? `[${attribute}]` : `[${attribute}="${value}"]`
  return view.container.querySelector(selector) as HTMLElement | null
}

describe('the target picker', () => {
  it('lists the registered project in the target select for a design mode', () => {
    const { view } = mountBody({
      choices: { kind: 'ready', value: CHOICES },
      chosen: DESIGN.id,
    })
    const select = at(view, 'data-refactory-target') as HTMLSelectElement
    expect(select).not.toBeNull()
    // The project option is present.
    const options = Array.from(select.options).map(o => o.textContent)
    expect(options.some(text => text!.includes('weather-cli'))).toBe(true)
  })

  it('picking a project sets the target knob in the start request', () => {
    const { view, instance } = mountBody({
      choices: { kind: 'ready', value: CHOICES },
      chosen: DESIGN.id,
    })
    const select = at(view, 'data-refactory-target') as HTMLSelectElement
    fireEvent.change(select, { target: { value: '/projects/weather-cli' } })
    const state = instance.getSnapshot().byTab[TAB]
    expect(state?.knobs?.target).toBe('/projects/weather-cli')
  })

  it('does not show the target picker for a create mode', () => {
    const { view } = mountBody({
      choices: { kind: 'ready', value: CHOICES },
      chosen: AUTHORING.id,
    })
    const select = at(view, 'data-refactory-target')
    expect(select).toBeNull()
  })

  it('preserves the target knob when a mode is chosen', () => {
    const { view, instance } = mountBody({
      choices: { kind: 'ready', value: CHOICES },
    })
    // First pick a design mode and set target.
    instance.actions.chose(TAB, DESIGN.id)
    instance.actions.knob(TAB, 'target', '/projects/weather-cli')
    // Now pick a different mode: target must survive.
    instance.actions.chose(TAB, MODE.id)
    const state = instance.getSnapshot().byTab[TAB]
    expect(state?.knobs?.target).toBe('/projects/weather-cli')
  })
})
