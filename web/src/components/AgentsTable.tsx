import { Table, Tag, Tooltip, type TableProps } from 'antd'
import { useNavigate } from 'react-router-dom'
import type { AgentInfo, AgentStatus } from '../api/agents'
import { formatDateTime, formatRelative } from '../utils/format'

const STATUS_COLOR: Record<AgentStatus, string> = {
  online: 'green',
  offline: 'red',
  never: 'default',
}

const STATUS_LABEL: Record<AgentStatus, string> = {
  online: '在线',
  offline: '离线',
  never: '未上报',
}

export function StatusTag({ status }: { status: AgentStatus }) {
  return <Tag color={STATUS_COLOR[status] ?? 'default'}>{STATUS_LABEL[status] ?? status}</Tag>
}

export interface AgentsTableProps {
  data: AgentInfo[] | undefined
  loading?: boolean
  size?: TableProps<AgentInfo>['size']
}

export function AgentsTable({ data, loading, size = 'middle' }: AgentsTableProps) {
  const navigate = useNavigate()

  const columns: TableProps<AgentInfo>['columns'] = [
    {
      title: '主机',
      dataIndex: 'host',
      key: 'host',
      sorter: (a, b) => a.host.localeCompare(b.host),
    },
    {
      title: 'IP',
      dataIndex: 'ip',
      key: 'ip',
      render: (v: string | null) => v ?? '-',
    },
    {
      title: '硬件',
      dataIndex: 'hardware_vendor',
      key: 'hardware_vendor',
      render: (v: string | null) => v ?? '-',
    },
    {
      title: '卡数',
      dataIndex: 'device_count',
      key: 'device_count',
      align: 'right',
      sorter: (a, b) => a.device_count - b.device_count,
    },
    {
      title: 'Agent 版本',
      dataIndex: 'version',
      key: 'version',
      render: (v: string | null) => v ?? '-',
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      filters: [
        { text: '在线', value: 'online' },
        { text: '离线', value: 'offline' },
        { text: '未上报', value: 'never' },
      ],
      onFilter: (value, record) => record.status === value,
      render: (s: AgentStatus) => <StatusTag status={s} />,
    },
    {
      title: '最后心跳',
      dataIndex: 'last_seen_at',
      key: 'last_seen_at',
      sorter: (a, b) => (a.last_seen_at ?? '').localeCompare(b.last_seen_at ?? ''),
      render: (v: string | null) => (
        <Tooltip title={formatDateTime(v)}>
          <span>{formatRelative(v)}</span>
        </Tooltip>
      ),
    },
  ]

  return (
    <Table<AgentInfo>
      rowKey="id"
      size={size}
      loading={loading}
      columns={columns}
      dataSource={data ?? []}
      pagination={{ pageSize: 20, hideOnSinglePage: true }}
      rowClassName={() => 'clickable-row'}
      onRow={(record) => ({
        onClick: () => navigate(`/hosts/${encodeURIComponent(record.host)}`),
      })}
    />
  )
}

export default AgentsTable
