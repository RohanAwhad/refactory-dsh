/**
 * The type's registry definition: the kind this package claims, its stable id,
 * and the one guide entry the picker offers.
 */
import { describe, expect, it } from 'vitest'
import { makeTranslate } from './translate.client.ts'
import { REFACTORY_ID, REFACTORY_KIND, refactoryDefinition } from '../src/client/definition.ts'
import { zh } from '../src/client/locales.ts'

describe('refactoryDefinition', () => {
  it('claims the refactory kind under this implementation\'s stable id', () => {
    const definition = refactoryDefinition(makeTranslate(zh))
    expect(definition.id).toBe(REFACTORY_ID)
    expect(definition.kind).toBe(REFACTORY_KIND)
    expect(definition.priority).toBe('builtin')
  })

  it('names itself and its guide entry from the current dictionary', () => {
    const definition = refactoryDefinition(makeTranslate(zh))
    expect(definition.title('sidebar://refactory')).toBe('运行模式')
    expect(definition.guide?.map(entry => [entry.order, entry.title(), entry.description?.()]))
      .toEqual([[10, '运行模式', '挑选一个模式开始运行，管理模式包，并查看运行进度。']])
  })

})
