import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { openLogTail, tailUrl, type LogLine, type TailStatus } from './logs'
import { authStorage } from './storage'

class FakeSocket {
  static instances: FakeSocket[] = []
  url: string
  closed = false
  onopen: ((ev: Event) => void) | null = null
  onmessage: ((ev: MessageEvent) => void) | null = null
  onerror: ((ev: Event) => void) | null = null
  onclose: ((ev: CloseEvent) => void) | null = null

  constructor(url: string) {
    this.url = url
    FakeSocket.instances.push(this)
  }

  close() {
    this.closed = true
    // browsers fire close asynchronously; emulate with a normal close code
    this.emitClose(1000)
  }

  emitOpen() {
    this.onopen?.(new Event('open'))
  }

  emitMessage(obj: unknown) {
    this.onmessage?.({ data: JSON.stringify(obj) } as MessageEvent)
  }

  emitClose(code: number) {
    this.onclose?.({ code } as CloseEvent)
  }
}

const createSocket = (url: string) => new FakeSocket(url) as unknown as WebSocket

describe('tailUrl', () => {
  it('derives ws scheme, repeats level and appends token', () => {
    const url = tailUrl({ host: 'h', service: 's', levels: ['error', 'warning'], q: 'a b' }, 'tok')
    expect(url.startsWith('ws://')).toBe(true)
    expect(url).toContain('/api/logs/tail?')
    expect(url).toContain('host=h')
    expect(url).toContain('service=s')
    expect(url).toContain('level=error&level=warning')
    expect(url).toContain('q=a+b')
    expect(url).toContain('token=tok')
  })

  it('omits level when all six are selected', () => {
    const url = tailUrl(
      { host: 'h', service: 's', levels: ['debug', 'info', 'warning', 'error', 'critical', 'unknown'] },
      null,
    )
    expect(url).not.toContain('level=')
    expect(url).not.toContain('token=')
  })
})

describe('openLogTail', () => {
  let lines: LogLine[]
  let statuses: TailStatus[]

  beforeEach(() => {
    vi.useFakeTimers()
    FakeSocket.instances = []
    lines = []
    statuses = []
    localStorage.clear()
    authStorage.setTokens('access-1')
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('forwards parsed lines and reports open/closed', () => {
    const close = openLogTail(
      { host: 'h', service: 's' },
      (l) => lines.push(l),
      (s) => statuses.push(s),
      { createSocket },
    )
    const sock = FakeSocket.instances[0]
    expect(sock.url).toContain('token=access-1')
    sock.emitOpen()
    sock.emitMessage({ ts: '2026-09-04T00:00:00Z', level: 'warning', line: 'hi' })
    sock.emitMessage('not an object')
    close()
    expect(lines).toEqual([{ ts: '2026-09-04T00:00:00Z', level: 'warning', line: 'hi' }])
    expect(statuses).toEqual(['open', 'closed'])
    expect(FakeSocket.instances).toHaveLength(1)
  })

  it('reconnects up to 5 times, 2s apart, then gives up', () => {
    openLogTail({ host: 'h', service: 's' }, () => {}, (s) => statuses.push(s), { createSocket })
    expect(FakeSocket.instances).toHaveLength(1)
    for (let i = 0; i < 5; i += 1) {
      FakeSocket.instances[i].emitClose(1006)
      expect(FakeSocket.instances).toHaveLength(i + 1)
      vi.advanceTimersByTime(1999)
      expect(FakeSocket.instances).toHaveLength(i + 1)
      vi.advanceTimersByTime(1)
      expect(FakeSocket.instances).toHaveLength(i + 2)
    }
    // sixth socket also fails: retries exhausted
    FakeSocket.instances[5].emitClose(1006)
    vi.advanceTimersByTime(10_000)
    expect(FakeSocket.instances).toHaveLength(6)
    expect(statuses.filter((s) => s === 'error')).toHaveLength(5)
    expect(statuses[statuses.length - 1]).toBe('closed')
  })

  it('resets the retry budget after a successful open', () => {
    openLogTail({ host: 'h', service: 's' }, () => {}, () => {}, { createSocket })
    for (let i = 0; i < 5; i += 1) {
      FakeSocket.instances[i].emitClose(1006)
      vi.advanceTimersByTime(2000)
    }
    FakeSocket.instances[5].emitOpen()
    FakeSocket.instances[5].emitClose(1006)
    vi.advanceTimersByTime(2000)
    expect(FakeSocket.instances).toHaveLength(7)
  })

  it('does not reconnect on 4401', () => {
    openLogTail({ host: 'h', service: 's' }, () => {}, (s) => statuses.push(s), { createSocket })
    FakeSocket.instances[0].emitClose(4401)
    vi.advanceTimersByTime(10_000)
    expect(FakeSocket.instances).toHaveLength(1)
    expect(statuses).toEqual(['error', 'closed'])
  })

  it('does not reconnect when closed by the caller while a retry is pending', () => {
    const close = openLogTail({ host: 'h', service: 's' }, () => {}, (s) => statuses.push(s), { createSocket })
    FakeSocket.instances[0].emitClose(1006)
    close()
    vi.advanceTimersByTime(10_000)
    expect(FakeSocket.instances).toHaveLength(1)
    expect(statuses).toEqual(['error', 'closed'])
  })
})
