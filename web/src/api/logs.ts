import { api } from './client'
import { authStorage } from './storage'

export type LogLevel = 'debug' | 'info' | 'warning' | 'error' | 'critical' | 'unknown'

export const LOG_LEVELS: LogLevel[] = ['debug', 'info', 'warning', 'error', 'critical', 'unknown']

export interface LogLine {
  /** RFC3339Nano timestamp as returned by the server. */
  ts: string
  level: LogLevel
  line: string
}

export interface LogQueryParams {
  host: string
  service: string
  levels?: LogLevel[]
  q?: string
  /** Unix seconds (fractions allowed). */
  start: number
  /** Unix seconds (fractions allowed). */
  end: number
  limit?: number
  direction?: 'backward' | 'forward'
}

export interface LogQueryResult {
  lines: LogLine[]
  has_more: boolean
}

export interface LogTailParams {
  host: string
  service: string
  levels?: LogLevel[]
  q?: string
}

export type TailStatus = 'open' | 'closed' | 'error'

/** Close code sent by the server when the token is missing/invalid/expired. */
export const TAIL_UNAUTHORIZED_CODE = 4401

function baseParams(p: { host: string; service: string; levels?: LogLevel[]; q?: string }): URLSearchParams {
  const usp = new URLSearchParams()
  usp.set('host', p.host)
  usp.set('service', p.service)
  // `level` is repeatable and, when all 6 levels are selected, equivalent to no filter.
  const levels = p.levels ?? []
  if (levels.length > 0 && levels.length < LOG_LEVELS.length) {
    for (const l of levels) usp.append('level', l)
  }
  if (p.q && p.q.trim()) usp.set('q', p.q.trim())
  return usp
}

export async function queryLogs(params: LogQueryParams): Promise<LogQueryResult> {
  const usp = baseParams(params)
  usp.set('start', String(params.start))
  usp.set('end', String(params.end))
  usp.set('limit', String(params.limit ?? 500))
  usp.set('direction', params.direction ?? 'backward')
  const res = await api.get<LogQueryResult>('/logs/query', { params: usp })
  return res.data
}

export function tailUrl(params: LogTailParams, token: string | null): string {
  const usp = baseParams(params)
  if (token) usp.set('token', token)
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${proto}//${window.location.host}/api/logs/tail?${usp.toString()}`
}

export interface OpenLogTailOptions {
  /** Maximum consecutive reconnect attempts after an unexpected close (default 5). */
  maxRetries?: number
  /** Delay between reconnect attempts in ms (default 2000). */
  retryDelayMs?: number
  /** Socket factory, injectable for tests. */
  createSocket?: (url: string) => WebSocket
}

/**
 * Open a WebSocket log tail. Returns a close function.
 *
 * Reconnects automatically (up to `maxRetries`, `retryDelayMs` apart) when the
 * socket closes unexpectedly. No reconnect on caller close or on 4401.
 *
 * Status callbacks: `open` on each successful connect, `error` when the
 * connection dropped (a reconnect is pending unless it was a 4401), `closed`
 * when the tail is definitively over (caller closed, 4401, or retries exhausted).
 */
export function openLogTail(
  params: LogTailParams,
  onLine: (line: LogLine) => void,
  onStatus: (status: TailStatus) => void,
  opts: OpenLogTailOptions = {},
): () => void {
  const maxRetries = opts.maxRetries ?? 5
  const retryDelayMs = opts.retryDelayMs ?? 2000
  const createSocket = opts.createSocket ?? ((url: string) => new WebSocket(url))

  let closedByCaller = false
  let attempts = 0
  let ws: WebSocket | null = null
  let timer: ReturnType<typeof setTimeout> | null = null

  const connect = () => {
    const url = tailUrl(params, authStorage.getAccessToken())
    const socket = createSocket(url)
    ws = socket

    socket.onopen = () => {
      if (socket !== ws) return
      attempts = 0
      onStatus('open')
    }
    socket.onmessage = (ev: MessageEvent) => {
      if (socket !== ws) return
      let parsed: unknown
      try {
        parsed = JSON.parse(typeof ev.data === 'string' ? ev.data : String(ev.data))
      } catch {
        return
      }
      if (parsed && typeof parsed === 'object' && 'line' in parsed) {
        const p = parsed as Partial<LogLine>
        onLine({
          ts: String(p.ts ?? ''),
          level: (p.level ?? 'unknown') as LogLevel,
          line: String(p.line ?? ''),
        })
      }
    }
    socket.onerror = () => {
      // status is reported from onclose, which always follows onerror
    }
    socket.onclose = (ev: CloseEvent) => {
      if (socket !== ws) return
      ws = null
      if (closedByCaller) {
        onStatus('closed')
        return
      }
      if (ev.code === TAIL_UNAUTHORIZED_CODE) {
        // auth failure: report the error, then end definitively (no reconnect)
        onStatus('error')
        onStatus('closed')
        return
      }
      if (attempts >= maxRetries) {
        onStatus('closed')
        return
      }
      attempts += 1
      onStatus('error')
      timer = setTimeout(() => {
        timer = null
        if (!closedByCaller) connect()
      }, retryDelayMs)
    }
  }

  connect()

  return () => {
    if (closedByCaller) return
    closedByCaller = true
    if (timer) {
      clearTimeout(timer)
      timer = null
    }
    if (ws) {
      ws.close()
    } else {
      onStatus('closed')
    }
  }
}
