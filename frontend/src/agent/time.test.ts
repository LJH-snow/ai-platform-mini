import { describe, expect, it } from 'vitest'
import { formatAgentTimestamp } from './time.ts'

describe('formatAgentTimestamp', () => {
  it('converts UTC timestamps to Asia/Shanghai time', () => {
    expect(formatAgentTimestamp('2026-08-06T00:00:00.000Z')).toBe('2026-08-06 08:00:00（上海）')
  })

  it('keeps missing and invalid timestamps explicit', () => {
    expect(formatAgentTimestamp(null)).toBe('后端未提供')
    expect(formatAgentTimestamp(undefined)).toBe('后端未提供')
    expect(formatAgentTimestamp('not-a-timestamp')).toBe('not-a-timestamp')
  })
})
