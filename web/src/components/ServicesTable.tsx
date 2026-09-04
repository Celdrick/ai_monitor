import { Table, Tag, Tooltip, type TableProps } from 'antd'
import { Link, useNavigate } from 'react-router-dom'
import type { ServiceInfo, ServiceSource, ServiceStatus } from '../api/services'
import { loadKey, useServiceLoad, type ServiceLoad } from '../hooks/useServices'
import { formatDateTime, formatRelative } from '../utils/format'

const STATUS_COLOR: Record<ServiceStatus, string> = {
  running: 'green',
  degraded: 'orange',
  stopped: 'default',
  unknown: 'red',
}

export const SERVICE_STATUS_LABEL: Record<ServiceStatus, string> = {
  running: '运行中',
  degraded: '降级',
  stopped: '已停止',
  unknown: '未知',
}

const SOURCE_LABEL: Record<ServiceSource, string> = {
  docker: 'Docker',
  process: '进程',
  manual: '手动',
}

export function ServiceStatusTag({ status }: { status: ServiceStatus }) {
  return <Tag color={STATUS_COLOR[status] ?? 'default'}>{SERVICE_STATUS_LABEL[status] ?? status}</Tag>
}

export function SourceTag({ source }: { source: ServiceSource }) {
  return <Tag>{SOURCE_LABEL[source] ?? source}</Tag>
}

export interface ServicesTableProps {
  data: ServiceInfo[] | undefined
  loading?: boolean
  /** Hide version / port / source / last-seen columns (overview & host detail). */
  compact?: boolean
  /** Hide the host column (host detail page). */
  hideHost?: boolean
  /** Show status column filters (default true). */
  statusFilters?: boolean
  /** Precomputed running/waiting maps; when omitted the table queries them itself. */
  load?: ServiceLoad
  size?: TableProps<ServiceInfo>['size']
  pagination?: TableProps<ServiceInfo>['pagination']
}

function formatLoad(s: ServiceInfo, map: Map<string, number>): string {
  if (s.status === 'stopped') return '-'
  const v = map.get(loadKey(s.host, s.name))
  return v === undefined ? '-' : String(Math.round(v))
}

export function ServicesTable({
  data,
  loading,
  compact = false,
  hideHost = false,
  statusFilters = true,
  load: loadProp,
  size = 'middle',
  pagination = { pageSize: 20, hideOnSinglePage: true },
}: ServicesTableProps) {
  const navigate = useNavigate()
  const ownLoad = useServiceLoad(loadProp === undefined)
  const load = loadProp ?? ownLoad

  const columns: TableProps<ServiceInfo>['columns'] = [
    {
      title: '服务名',
      dataIndex: 'name',
      key: 'name',
      sorter: (a, b) => a.name.localeCompare(b.name),
      render: (v: string) => <strong>{v}</strong>,
    },
    {
      title: '机器',
      dataIndex: 'host',
      key: 'host',
      hidden: hideHost,
      sorter: (a, b) => a.host.localeCompare(b.host),
      render: (v: string) => (
        <Link to={`/hosts/${encodeURIComponent(v)}`} onClick={(e) => e.stopPropagation()}>
          {v}
        </Link>
      ),
    },
    {
      title: '模型',
      dataIndex: 'model',
      key: 'model',
      ellipsis: true,
      render: (v: string | null) => v ?? '-',
    },
    {
      title: 'vLLM 版本',
      dataIndex: 'vllm_version',
      key: 'vllm_version',
      hidden: compact,
      render: (v: string | null) => v ?? '-',
    },
    {
      title: '端口',
      dataIndex: 'port',
      key: 'port',
      hidden: compact,
      align: 'right',
      width: 90,
    },
    {
      title: '来源',
      dataIndex: 'source',
      key: 'source',
      hidden: compact,
      width: 90,
      render: (s: ServiceSource) => <SourceTag source={s} />,
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 100,
      filters: statusFilters
        ? (Object.keys(SERVICE_STATUS_LABEL) as ServiceStatus[]).map((s) => ({
            text: SERVICE_STATUS_LABEL[s],
            value: s,
          }))
        : undefined,
      onFilter: statusFilters ? (value, record) => record.status === value : undefined,
      render: (s: ServiceStatus) => <ServiceStatusTag status={s} />,
    },
    {
      title: 'running',
      key: 'running',
      align: 'right',
      width: 90,
      render: (_, r) => formatLoad(r, load.running),
    },
    {
      title: 'waiting',
      key: 'waiting',
      align: 'right',
      width: 90,
      render: (_, r) => formatLoad(r, load.waiting),
    },
    {
      title: '最后心跳',
      dataIndex: 'last_seen_at',
      key: 'last_seen_at',
      hidden: compact,
      sorter: (a, b) => (a.last_seen_at ?? '').localeCompare(b.last_seen_at ?? ''),
      render: (v: string | null) => (
        <Tooltip title={formatDateTime(v)}>
          <span>{formatRelative(v)}</span>
        </Tooltip>
      ),
    },
  ]

  return (
    <Table<ServiceInfo>
      rowKey="id"
      size={size}
      loading={loading}
      columns={columns}
      dataSource={data ?? []}
      pagination={pagination}
      rowClassName={() => 'clickable-row'}
      onRow={(record) => ({
        onClick: () => navigate(`/services/${record.id}`),
      })}
    />
  )
}

export default ServicesTable
