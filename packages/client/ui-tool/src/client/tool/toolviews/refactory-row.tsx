import type { Context } from '@deepseek-ai/cordis'
import { IconSparkle16 } from '@deepseek-ai/dsh-client-ui-primitives'
import type { PropsLocale } from '@deepseek-ai/dsh-client-ui-slots'
import type { ToolCallViewProps } from '../../contract/slots.ts'
import { toolRowModel } from '../models/tool-call-model.ts'
import { refactoryCardModel } from '../models/refactory-card-model.ts'
import { ToolRow } from '../components/ToolRow.tsx'
import { CONVERSATION_NS as NS } from '../../locale.ts'

type RefactoryRowProps = ToolCallViewProps & PropsLocale<'conversation'>

/**
 * One `refactory` call as the run it addressed: the action it ran, the run's
 * state and best score while collapsed, and the run's own facts — candidates and
 * their diffs, artifacts, the progress tail — when expanded.
 */
export function RefactoryRow({ toolName, block, inspect, t }: RefactoryRowProps) {
  const model = toolRowModel(toolName, block)
  const card = refactoryCardModel(block, t)
  if (card === null) {
    // The dispatch key is this tool's wire name, so this is unreachable through
    // a registered `refactory` call. It keeps the component honest for a caller
    // that renders it directly, rather than rendering a row with no title.
    return (
      <ToolRow
        t={t}
        variant={model.variant}
        toolName={toolName}
        icon={<IconSparkle16 size={14} />}
        title={t(model.titleKey)}
        summary={model.summary}
        output={model.output}
        errorSummary={model.errorSummary}
        state={model.state}
        inspect={inspect}
      />
    )
  }
  return (
    <ToolRow
      t={t}
      variant={model.variant}
      toolName={toolName}
      icon={<IconSparkle16 size={14} />}
      title={card.title}
      // A failed call's own failure line outranks the payload summary: a reader
      // needs to know the call failed before knowing what it was about.
      summary={model.state === 'error' ? model.summary : card.summary}
      output={card.body.join('\n')}
      errorSummary={model.errorSummary}
      state={model.state}
      inspect={inspect}
    />
  )
}

/** Registers the refactory conversation row under the tool's wire name. */
export const refactoryToolview = {
  name: 'refactory-toolview',
  inject: ['slots'],
  apply(ctx: Context): void {
    ctx.slots.inject('tool.call.toolview', function* () {
      yield ctx.slots.register({ name: 'tool.call.toolview', key: 'refactory', locale: NS }, RefactoryRow)
    })
  },
}
