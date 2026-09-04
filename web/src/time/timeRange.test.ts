import { describe, expect, it } from 'vitest'
import { presetToRange, stepForRange } from './timeRange'

describe('stepForRange', () => {
  it('uses the 15s floor for short ranges', () => {
    expect(stepForRange({ start: 0, end: 3600 })).toBe('15s')
  })

  it('rounds up to a multiple of 15 for long ranges', () => {
    // 604800 / 300 = 2016 -> 2025
    expect(stepForRange({ start: 0, end: 604800 })).toBe('2025s')
  })

  it('never goes below 15s', () => {
    expect(stepForRange({ start: 100, end: 100 })).toBe('15s')
  })
})

describe('presetToRange', () => {
  it('computes 1h range from now in ms', () => {
    expect(presetToRange('1h', 1_000_000_000_000)).toEqual({
      start: 999_996_400,
      end: 1_000_000_000,
    })
  })

  it('computes 7d range', () => {
    const r = presetToRange('7d', 1_000_000_000_000)
    expect(r.end - r.start).toBe(7 * 86400)
  })
})
