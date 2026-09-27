import { createElement } from 'react'
import type { ComponentType } from 'react'
import type { Context } from '@deepseek-ai/cordis'
import type { SnapshotStore } from '@deepseek-ai/dsh-client-store'
import type {
  AgentPresetLabelInjected, AgentPresetSeatInjected, AgentPresetSectionInjected,
} from '@deepseek-ai/dsh-client-ui-agent-preset/client'

const copy = {
  en: {
    description: 'Create, run, and improve Refactory workflows. CEO coordinates agents, follows the workflow graph, and brings questions and results back to you.',
    sectionIntro: 'Choose CEO for guided Refactory workflows, or Minimal for a lightweight agent with a persistent shell. Your choice applies to new conversations.',
    duplicateUnavailable: 'Refactory offers the CEO and Minimal presets.',
  },
  zh: {
    description: '创建、运行和改进 Refactory 工作流。CEO 协调智能体，按工作流图推进，并向你呈现问题和结果。',
    sectionIntro: '选择 CEO 引导 Refactory 工作流，或选择仅使用持久 Shell 的极简智能体。预设选择适用于新会话。',
    duplicateUnavailable: 'Refactory 提供 CEO 和极简预设。',
  },
}
type Locale = keyof typeof copy
const selectable = (id: string): boolean => id === 'ceo' || id === 'minimal'

/** Metadata belongs to the UI; the host roster and worker compositions stay intact. */
export function presetRows<T extends { id: string; description?: string }>(rows: readonly T[], locale: Locale, filter: boolean): T[] {
  return rows.filter(row => !filter || selectable(row.id)).map(row => row.id === 'ceo'
    ? { ...row, description: copy[locale].description } : row)
}

/** Stable snapshots are required by the renderer's external-store hooks. */
function projectStore<T>(source: SnapshotStore<T>, locale: () => Locale, project: (value: T, lang: Locale) => T): SnapshotStore<T> {
  let previous: T, language: Locale, snapshot: T
  return {
    subscribe: listener => source.subscribe(listener),
    update: mutate => source.update(mutate),
    set: value => source.set(value),
    getSnapshot: () => {
      const value = source.getSnapshot(), lang = locale()
      if (snapshot === undefined || value !== previous || lang !== language) {
        previous = value; language = lang; snapshot = project(value, lang)
      }
      return snapshot
    },
  }
}

/** Keep the native picker and its actual staged choice in agreement, including old defaults. */
export function presetSeat(face: AgentPresetSeatInjected, locale: () => Locale): AgentPresetSeatInjected {
  return { ...face,
    hooks: { ...face.hooks, agentPresetSeat: projectStore(face.hooks.agentPresetSeat, locale,
      (state, lang) => ({ ...state, options: presetRows(state.options, lang, true) })) },
    load: async () => {
      await face.load()
      const state = face.hooks.agentPresetSeat.getSnapshot()
      if (state.error || state.busy || selectable(state.current)) return
      const fallback = state.options.find(row => row.id === 'ceo') ?? state.options.find(row => row.id === 'minimal')
      if (fallback) await face.select(fallback.id)
    },
  }
}

export function presetSection(face: AgentPresetSectionInjected, locale: () => Locale): AgentPresetSectionInjected {
  return { ...face,
    hooks: { ...face.hooks, agentPresetSection: projectStore(face.hooks.agentPresetSection, locale,
      (state, lang) => ({ ...state, rows: presetRows(state.rows, lang, true), authorable: false })) },
  }
}

/** Public slot replacement, retaining upstream controllers, errors, view and default actions. */
export function installPresets(ctx: Context): void {
  const locale = (): Locale => ctx.locale.getLocale().active.startsWith('zh') ? 'zh' : 'en'
  type Slot = 'conversation.hero.agentPreset' | 'conversation.session.header.actions' | 'settings.section'
  type Entry = ReturnType<typeof ctx.slots.entries>[number]
  const install = (slot: Slot, id: string | undefined, transform: (face: never) => unknown): void => {
    ctx.slots.inject(slot, () => {
      let source: Entry | undefined, dispose: (() => void) | undefined, changing = false
      const own = new Set<unknown>()
      const refresh = (): void => {
        if (changing) return
        const original = ctx.slots.entries(slot).find(entry => entry.options.id === id && !own.has(entry.component))
        if (source === original) return
        changing = true
        dispose?.(); dispose = undefined; source = original
        if (original?.inject) {
          const inject = original.inject as () => never
          const face = transform(inject())
          const Native = original.component as ComponentType<Record<string, unknown>>
          const Surface = (props: Record<string, unknown>) => {
            if (slot !== 'settings.section') return createElement(Native, props)
            const t = props.t as (key: string, ...args: unknown[]) => string
            return createElement(Native, { ...props, t: (key: string, ...args: unknown[]) =>
              key === 'sectionIntro' || key === 'duplicateUnavailable' ? copy[locale()][key] : t(key, ...args) })
          }
          own.add(Surface)
          dispose = ctx.slots.register({ ...original.options, name: slot, locale: original.locale,
            priority: -10, inject: () => face } as never, Surface as never)
        }
        changing = false
      }
      const unsubscribe = ctx.slots.subscribe(slot, refresh)
      refresh()
      return () => { unsubscribe(); dispose?.() }
    })
  }
  install('conversation.hero.agentPreset', undefined, (face: AgentPresetSeatInjected) => presetSeat(face, locale))
  install('settings.section', 'agent-presets', (face: AgentPresetSectionInjected) => presetSection(face, locale))
  // Existing sessions still report their real preset, even when it is no longer offered for new chats.
  install('conversation.session.header.actions', 'agent-preset', (face: AgentPresetLabelInjected) => ({ ...face,
    hooks: { ...face.hooks, agentPresets: projectStore(face.hooks.agentPresets, locale,
      (state, lang) => ({ ...state, options: presetRows(state.options, lang, false) })) },
  }))
}
