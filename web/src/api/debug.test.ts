import { describe, expect, it } from 'vitest'
import {
  actionDisabled,
  actionDisabledReason,
  clampDuration,
  DURATION_DEFAULT,
  DURATION_MAX,
  DURATION_MIN,
  isPerfettoFilename,
  isValidDuration,
  profileSessionOpen,
  type DebugCapabilities,
} from './debug'

const ALL_CAPS: DebugCapabilities = { profile: true, pyspy: true, nsys: true, msprof: true }
const NO_TOOLS: DebugCapabilities = { profile: true, pyspy: false, nsys: false, msprof: false }

describe('duration', () => {
  it('accepts integers in 5–60 inclusive', () => {
    expect(isValidDuration(DURATION_MIN)).toBe(true)
    expect(isValidDuration(15)).toBe(true)
    expect(isValidDuration(DURATION_MAX)).toBe(true)
    expect(isValidDuration(4)).toBe(false)
    expect(isValidDuration(61)).toBe(false)
    expect(isValidDuration(15.5)).toBe(false)
    expect(isValidDuration(NaN)).toBe(false)
  })

  it('clamps to 5–60 and falls back on non-finite', () => {
    expect(clampDuration(1)).toBe(DURATION_MIN)
    expect(clampDuration(99)).toBe(DURATION_MAX)
    expect(clampDuration(20.4)).toBe(20)
    expect(clampDuration(Number.NaN)).toBe(DURATION_DEFAULT)
  })
})

describe('profileSessionOpen', () => {
  it('is closed with no profile tasks', () => {
    expect(profileSessionOpen([])).toBe(false)
    expect(profileSessionOpen([{ type: 'pyspy_dump', status: 'succeeded' }])).toBe(false)
  })

  it('opens after a successful or in-flight start', () => {
    expect(profileSessionOpen([{ type: 'profile_start', status: 'succeeded' }])).toBe(true)
    expect(profileSessionOpen([{ type: 'profile_start', status: 'running' }])).toBe(true)
  })

  it('closes after a successful stop or a failed start', () => {
    expect(
      profileSessionOpen([
        { type: 'profile_stop', status: 'succeeded' },
        { type: 'profile_start', status: 'succeeded' },
      ]),
    ).toBe(false)
    expect(profileSessionOpen([{ type: 'profile_start', status: 'failed' }])).toBe(false)
  })
})

describe('actionDisabled', () => {
  it('disables missing tools and enables installed ones', () => {
    expect(actionDisabled('pyspy_dump', NO_TOOLS, [], 'running')).toBe(true)
    expect(actionDisabled('nsys', NO_TOOLS, [], 'running')).toBe(true)
    expect(actionDisabled('msprof', NO_TOOLS, [], 'running')).toBe(true)
    expect(actionDisabled('pyspy_dump', ALL_CAPS, [], 'running')).toBe(false)
    expect(actionDisabledReason('pyspy_record', NO_TOOLS, [], 'running')).toMatch(/py-spy/)
  })

  it('disables start after profile begins and enables stop', () => {
    const tasks = [{ type: 'profile_start', status: 'succeeded' }]
    expect(actionDisabled('profile_start', ALL_CAPS, tasks, 'running')).toBe(true)
    expect(actionDisabled('profile_stop', ALL_CAPS, tasks, 'running')).toBe(false)
    expect(actionDisabled('profile_stop', ALL_CAPS, [], 'running')).toBe(true)
  })

  it('disables all sampling while a task is pending or running', () => {
    const tasks = [{ type: 'pyspy_record', status: 'running' }]
    expect(actionDisabled('pyspy_dump', ALL_CAPS, tasks, 'running')).toBe(true)
    expect(actionDisabled('nsys', ALL_CAPS, tasks, 'running')).toBe(true)
    expect(actionDisabled('profile_start', ALL_CAPS, tasks, 'running')).toBe(true)
    expect(actionDisabledReason('msprof', ALL_CAPS, tasks, 'running')).toMatch(/进行中/)
  })

  it('disables when the service is stopped', () => {
    expect(actionDisabled('pyspy_dump', ALL_CAPS, [], 'stopped')).toBe(true)
    expect(actionDisabledReason('profile_start', ALL_CAPS, [], 'stopped')).toMatch(/未在运行/)
  })

  it('disables until capabilities load', () => {
    expect(actionDisabled('profile_start', undefined, [], 'running')).toBe(true)
  })
})

describe('isPerfettoFilename', () => {
  it('matches chrome / torch traces', () => {
    expect(isPerfettoFilename('trace.json')).toBe(true)
    expect(isPerfettoFilename('foo.pt.trace.json')).toBe(true)
    expect(isPerfettoFilename('trace.json.gz')).toBe(true)
    expect(isPerfettoFilename('dump.txt')).toBe(false)
    expect(isPerfettoFilename('session.nsys-rep')).toBe(false)
  })
})
