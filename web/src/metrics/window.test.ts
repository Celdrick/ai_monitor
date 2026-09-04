import { describe, expect, it } from 'vitest'
import { windowForRange } from './window'

describe('windowForRange', () => {
  it('uses 1m up to 6h', () => {
    expect(windowForRange({ start: 0, end: 3600 })).toBe('1m')
    expect(windowForRange({ start: 0, end: 6 * 3600 })).toBe('1m')
  })

  it('uses 5m up to 7d', () => {
    expect(windowForRange({ start: 0, end: 6 * 3600 + 1 })).toBe('5m')
    expect(windowForRange({ start: 0, end: 24 * 3600 })).toBe('5m')
    expect(windowForRange({ start: 0, end: 7 * 86400 })).toBe('5m')
  })

  it('uses 30m beyond 7d', () => {
    expect(windowForRange({ start: 0, end: 7 * 86400 + 1 })).toBe('30m')
    expect(windowForRange({ start: 0, end: 30 * 86400 })).toBe('30m')
  })
})
