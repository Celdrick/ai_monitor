import { DownOutlined, ReloadOutlined } from '@ant-design/icons'
import { useQuery } from '@tanstack/react-query'
import { Alert, Button, Empty, Input, Select, Space, Spin, Switch, Tag, Typography } from 'antd'
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type CSSProperties } from 'react'
import { errorMessage } from '../api/client'
import { LOG_LEVELS, openLogTail, queryLogs, type LogLevel, type LogLine, type TailStatus } from '../api/logs'
import { appendCapped, formatLogTime, levelColor, levelLabel, lineKey, prependUnique, tsToSeconds } from '../logs/logLines'
import { useTimeRange } from '../time/TimeRangeContext'
import { formatDateTime } from '../utils/format'

const PAGE_LIMIT = 500
const FLUSH_MS = 100
const BOTTOM_EPSILON = 8

const MONO_FONT = 'ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace'

const LEVEL_OPTIONS = LOG_LEVELS.map((l) => ({ label: l, value: l }))

export interface LogViewerProps {
  host: string
  service: string
  height?: number
}

const rowStyle: CSSProperties = {
  display: 'flex',
  gap: 8,
  padding: '0 4px',
  lineHeight: 1.6,
}

function LogRow({ line }: { line: LogLine }) {
  const color = levelColor(line.level)
  return (
    <div style={{ ...rowStyle, color }}>
      <span style={{ flex: '0 0 auto', color: '#8c8c8c' }}>{formatLogTime(line.ts)}</span>
      <span style={{ flex: '0 0 44px', fontWeight: 600 }}>{levelLabel(line.level)}</span>
      <span style={{ flex: 1, whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>{line.line}</span>
    </div>
  )
}

interface OlderState {
  pages: LogLine[]
  hasMore: boolean | null
  loading: boolean
  error: unknown
}

const OLDER_INITIAL: OlderState = { pages: [], hasMore: null, loading: false, error: null }

export function LogViewer({ host, service, height = 520 }: LogViewerProps) {
  const { range, refreshKey } = useTimeRange()

  const [live, setLive] = useState(false)
  const [levels, setLevels] = useState<LogLevel[]>(LOG_LEVELS)
  const [qInput, setQInput] = useState('')
  const [q, setQ] = useState('')
  const [autoScroll, setAutoScroll] = useState(true)

  // live tail state
  const [liveLines, setLiveLines] = useState<LogLine[]>([])
  const [tailStatus, setTailStatus] = useState<TailStatus | null>(null)
  const [reconnectKey, setReconnectKey] = useState(0)

  // history paging state
  const [older, setOlder] = useState<OlderState>(OLDER_INITIAL)

  const levelsKey = useMemo(() => [...levels].sort().join(','), [levels])

  // ---- history query (disabled while live) ----
  const hist = useQuery({
    queryKey: ['logs', host, service, levelsKey, q, range.start, range.end, refreshKey],
    queryFn: () =>
      queryLogs({
        host,
        service,
        levels,
        q,
        start: range.start,
        end: range.end,
        limit: PAGE_LIMIT,
        direction: 'backward',
      }),
    enabled: !live && host.length > 0 && service.length > 0,
    refetchInterval: false,
  })

  // a fresh base page resets any "load earlier" pages
  useEffect(() => {
    setOlder(OLDER_INITIAL)
  }, [hist.data])

  // ---- live tail ----
  useEffect(() => {
    if (!live || !host || !service) return
    setLiveLines([])
    setTailStatus(null)
    setAutoScroll(true)

    const pending: LogLine[] = []
    let flushTimer: ReturnType<typeof setTimeout> | null = null
    const flush = () => {
      flushTimer = null
      if (pending.length === 0) return
      const batch = pending.splice(0, pending.length)
      setLiveLines((prev) => appendCapped(prev, batch))
    }
    const close = openLogTail(
      { host, service, levels: levelsKey.split(',') as LogLevel[], q },
      (line) => {
        pending.push(line)
        if (flushTimer === null) flushTimer = setTimeout(flush, FLUSH_MS)
      },
      setTailStatus,
    )
    return () => {
      close()
      if (flushTimer !== null) clearTimeout(flushTimer)
    }
  }, [live, host, service, levelsKey, q, reconnectKey])

  const baseLines = hist.data?.lines ?? []
  const lines = useMemo<LogLine[]>(
    () => (live ? liveLines : older.pages.length ? older.pages.concat(baseLines) : baseLines),
    [live, liveLines, older.pages, baseLines],
  )
  const hasMore = live ? false : (older.hasMore ?? hist.data?.has_more ?? false)

  // ---- scrolling ----
  const boxRef = useRef<HTMLDivElement>(null)
  const preserveHeightRef = useRef<number | null>(null)

  useLayoutEffect(() => {
    const el = boxRef.current
    if (!el) return
    if (preserveHeightRef.current !== null) {
      const prev = preserveHeightRef.current
      preserveHeightRef.current = null
      el.scrollTop += el.scrollHeight - prev
      return
    }
    if (autoScroll) el.scrollTop = el.scrollHeight
  }, [lines, autoScroll])

  const onScroll = useCallback(() => {
    const el = boxRef.current
    if (!el) return
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight <= BOTTOM_EPSILON
    setAutoScroll(atBottom)
  }, [])

  const scrollToBottom = useCallback(() => {
    setAutoScroll(true)
    const el = boxRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [])

  // ---- load earlier ----
  const loadOlder = useCallback(async () => {
    const earliest = lines[0]
    if (!earliest || older.loading) return
    const end = tsToSeconds(earliest.ts)
    if (!Number.isFinite(end) || end <= range.start) {
      setOlder((s) => ({ ...s, hasMore: false }))
      return
    }
    setOlder((s) => ({ ...s, loading: true, error: null }))
    try {
      const res = await queryLogs({
        host,
        service,
        levels,
        q,
        start: range.start,
        end,
        limit: PAGE_LIMIT,
        direction: 'backward',
      })
      const merged = prependUnique(lines, res.lines)
      const added = merged.length - lines.length
      preserveHeightRef.current = boxRef.current?.scrollHeight ?? null
      setAutoScroll(false)
      setOlder((s) => ({
        pages: merged.slice(0, s.pages.length + added),
        hasMore: res.has_more && added > 0,
        loading: false,
        error: null,
      }))
    } catch (err) {
      setOlder((s) => ({ ...s, loading: false, error: err }))
    }
  }, [lines, older.loading, range.start, host, service, levels, q])

  // ---- render ----
  const statusTag = (() => {
    if (!live) return null
    switch (tailStatus) {
      case 'open':
        return <Tag color="green">已连接</Tag>
      case 'error':
        return <Tag color="orange">重连中</Tag>
      case 'closed':
        return <Tag>已断开</Tag>
      default:
        return <Tag color="blue">连接中</Tag>
    }
  })()

  const error = live ? null : (hist.error ?? older.error)

  return (
    <div>
      <Space wrap style={{ marginBottom: 8, width: '100%', justifyContent: 'space-between' }}>
        <Space wrap>
          <Switch checked={live} onChange={setLive} checkedChildren="实时" unCheckedChildren="实时" />
          <Select<LogLevel[]>
            mode="multiple"
            allowClear
            maxTagCount="responsive"
            style={{ minWidth: 260 }}
            placeholder="级别"
            options={LEVEL_OPTIONS}
            value={levels}
            onChange={(v) => setLevels(v.length ? v : LOG_LEVELS)}
          />
          <Input.Search
            allowClear
            placeholder="关键词"
            style={{ width: 240 }}
            value={qInput}
            onChange={(e) => setQInput(e.target.value)}
            onSearch={(v) => setQ(v.trim())}
          />
          {!live ? (
            <Button icon={<ReloadOutlined />} onClick={() => hist.refetch()} loading={hist.isFetching} />
          ) : null}
        </Space>
        <Space>
          {statusTag}
          <Typography.Text type="secondary">
            {live
              ? `${lines.length} 行（实时，最多保留 2000 行）`
              : `${lines.length} 行 · ${formatDateTime(new Date(range.start * 1000).toISOString())} ~ ${formatDateTime(
                  new Date(range.end * 1000).toISOString(),
                )}`}
          </Typography.Text>
        </Space>
      </Space>

      {error ? (
        <Alert type="error" showIcon message="日志查询失败" description={errorMessage(error)} style={{ marginBottom: 8 }} />
      ) : null}
      {live && tailStatus === 'closed' ? (
        <Alert
          type="warning"
          showIcon
          message="实时连接已关闭（重连失败或登录已过期）"
          action={
            <Button size="small" onClick={() => setReconnectKey((k) => k + 1)}>
              重新连接
            </Button>
          }
          style={{ marginBottom: 8 }}
        />
      ) : null}

      <div style={{ position: 'relative' }}>
        <div
          ref={boxRef}
          onScroll={onScroll}
          style={{
            height,
            overflow: 'auto',
            border: '1px solid #f0f0f0',
            borderRadius: 6,
            background: '#fafafa',
            padding: 8,
            fontFamily: MONO_FONT,
            fontSize: 12,
          }}
        >
          {!live ? (
            <div style={{ textAlign: 'center', marginBottom: 8 }}>
              {hasMore ? (
                <Button size="small" loading={older.loading} onClick={loadOlder}>
                  加载更早
                </Button>
              ) : lines.length > 0 ? (
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  已到时间范围起点
                </Typography.Text>
              ) : null}
            </div>
          ) : null}

          {lines.length === 0 ? (
            <div style={{ height: height - 48, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              {!live && hist.isLoading ? (
                <Spin />
              ) : (
                <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={live ? '等待新日志…' : '暂无日志'} />
              )}
            </div>
          ) : (
            lines.map((l, i) => <LogRow key={`${i}-${lineKey(l)}`} line={l} />)
          )}
        </div>

        {!autoScroll && lines.length > 0 ? (
          <Button
            size="small"
            type="primary"
            icon={<DownOutlined />}
            onClick={scrollToBottom}
            style={{ position: 'absolute', right: 24, bottom: 16, boxShadow: '0 2px 8px rgba(0,0,0,0.15)' }}
          >
            回到底部
          </Button>
        ) : null}
      </div>
    </div>
  )
}

export default LogViewer
