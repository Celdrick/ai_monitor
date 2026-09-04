import { Input, Table, Typography, type TableProps } from 'antd'
import { useMemo, useState, type CSSProperties } from 'react'

interface EnvRow {
  key: string
  value: string
}

export interface EnvTableProps {
  env: Record<string, string> | undefined
  loading?: boolean
}

const MONO: CSSProperties = {
  fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace',
  fontSize: 12,
  wordBreak: 'break-all',
}

/** Key/value table for (already redacted) environment variables with a search box. */
export function EnvTable({ env, loading }: EnvTableProps) {
  const [search, setSearch] = useState('')

  const rows = useMemo<EnvRow[]>(() => {
    const all = Object.entries(env ?? {})
      .map(([key, value]) => ({ key, value }))
      .sort((a, b) => a.key.localeCompare(b.key))
    const q = search.trim().toLowerCase()
    if (!q) return all
    return all.filter((r) => r.key.toLowerCase().includes(q) || r.value.toLowerCase().includes(q))
  }, [env, search])

  const columns: TableProps<EnvRow>['columns'] = [
    {
      title: '变量',
      dataIndex: 'key',
      key: 'key',
      width: '32%',
      render: (v: string) => <Typography.Text style={MONO}>{v}</Typography.Text>,
    },
    {
      title: '值',
      dataIndex: 'value',
      key: 'value',
      render: (v: string) =>
        v === '***' ? (
          <Typography.Text type="secondary" style={MONO}>
            ***
          </Typography.Text>
        ) : (
          <Typography.Text style={MONO} copyable={{ text: v }}>
            {v}
          </Typography.Text>
        ),
    },
  ]

  const total = Object.keys(env ?? {}).length

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8, gap: 8 }}>
        <Typography.Text type="secondary">
          {search ? `${rows.length} / ${total}` : total} 个变量（敏感值已脱敏）
        </Typography.Text>
        <Input.Search
          allowClear
          placeholder="搜索变量名 / 值"
          style={{ width: 260 }}
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </div>
      <Table<EnvRow>
        size="small"
        rowKey="key"
        loading={loading}
        columns={columns}
        dataSource={rows}
        pagination={{ pageSize: 50, hideOnSinglePage: true, size: 'small' }}
      />
    </div>
  )
}

export default EnvTable
