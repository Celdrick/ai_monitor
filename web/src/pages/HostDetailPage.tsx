import { ArrowLeftOutlined } from '@ant-design/icons'
import { Alert, Button, Card, Col, Descriptions, Row, Space, Table, Typography, type TableProps } from 'antd'
import { useCallback, useMemo } from 'react'
import { Link, useParams } from 'react-router-dom'
import { sampleValue, type InstantResult, type MetricLabels } from '../api/metrics'
import { StatusTag } from '../components/AgentsTable'
import { ServicesTable } from '../components/ServicesTable'
import { TimeSeriesChart } from '../components/TimeSeriesChart'
import { useAgents, useInstant, useRange } from '../hooks/useMetrics'
import { useAgentServices } from '../hooks/useServices'
import { formatDateTime, formatGiB, formatRelative } from '../utils/format'

interface DeviceRow {
  key: string
  index: number
  model: string
  vendor: string
  memUsed?: number
  memTotal?: number
}

interface ProcessRow {
  key: string
  index: number
  pid: string
  memBytes: number
}

interface DiskRow {
  key: string
  mount: string
  used?: number
  total?: number
}

function indexOf(m: MetricLabels): number {
  const n = Number(m.index)
  return Number.isFinite(n) ? n : -1
}

function buildDeviceRows(
  devices: InstantResult | undefined,
  memUsed: InstantResult | undefined,
): DeviceRow[] {
  if (!devices) return []
  const usedByIndex = new Map<string, number>()
  memUsed?.result.forEach((s) => usedByIndex.set(s.metric.index ?? '', sampleValue(s)))
  return devices.result
    .map((s) => ({
      key: s.metric.index ?? '',
      index: indexOf(s.metric),
      model: s.metric.model ?? '-',
      vendor: s.metric.vendor ?? '-',
      memTotal: sampleValue(s),
      memUsed: usedByIndex.get(s.metric.index ?? ''),
    }))
    .sort((a, b) => a.index - b.index)
}

function buildProcessRows(procs: InstantResult | undefined): ProcessRow[] {
  if (!procs) return []
  return procs.result
    .map((s) => ({
      key: `${s.metric.index}-${s.metric.pid}`,
      index: indexOf(s.metric),
      pid: s.metric.pid ?? '-',
      memBytes: sampleValue(s),
    }))
    .sort((a, b) => a.index - b.index || b.memBytes - a.memBytes)
}

function buildDiskRows(used: InstantResult | undefined, total: InstantResult | undefined): DiskRow[] {
  const rows = new Map<string, DiskRow>()
  const ensure = (mount: string) => {
    let r = rows.get(mount)
    if (!r) {
      r = { key: mount, mount }
      rows.set(mount, r)
    }
    return r
  }
  used?.result.forEach((s) => (ensure(s.metric.mount ?? '?').used = sampleValue(s)))
  total?.result.forEach((s) => (ensure(s.metric.mount ?? '?').total = sampleValue(s)))
  return [...rows.values()].sort((a, b) => a.mount.localeCompare(b.mount))
}

export function HostDetailPage() {
  const { host: rawHost = '' } = useParams<{ host: string }>()
  const host = decodeURIComponent(rawHost)
  const params = useMemo(() => ({ host }), [host])
  const enabled = host.length > 0

  const agents = useAgents()
  const agent = agents.data?.find((a) => a.host === host)
  const services = useAgentServices(agent?.id)

  const devices = useInstant('host_devices', params, enabled)
  const devMemUsedNow = useInstant('host_device_mem_used', params, enabled)
  const processes = useInstant('host_device_process_mem', params, enabled)
  const diskUsed = useInstant('host_disk_used', params, enabled)
  const diskTotal = useInstant('host_disk_total', params, enabled)

  const util = useRange('host_device_util', params, enabled)
  const mem = useRange('host_device_mem_used', params, enabled)
  const temp = useRange('host_device_temp', params, enabled)
  const power = useRange('host_device_power', params, enabled)
  const cpu = useRange('host_cpu', params, enabled)
  const hostMem = useRange('host_mem_used', params, enabled)

  const deviceSeriesName = useCallback((m: MetricLabels) => `#${m.index ?? '?'}`, [])
  const hostSeriesName = useCallback((m: MetricLabels) => m.host ?? host, [host])

  const deviceRows = useMemo(() => buildDeviceRows(devices.data, devMemUsedNow.data), [devices.data, devMemUsedNow.data])
  const processRows = useMemo(() => buildProcessRows(processes.data), [processes.data])
  const diskRows = useMemo(() => buildDiskRows(diskUsed.data, diskTotal.data), [diskUsed.data, diskTotal.data])

  const deviceColumns: TableProps<DeviceRow>['columns'] = [
    { title: '卡', dataIndex: 'index', key: 'index', width: 80, render: (v: number) => `#${v}` },
    { title: '型号', dataIndex: 'model', key: 'model' },
    { title: '厂商', dataIndex: 'vendor', key: 'vendor', width: 120 },
    {
      title: '当前显存 (GiB)',
      key: 'mem',
      align: 'right',
      render: (_, r) => `${formatGiB(r.memUsed)} / ${formatGiB(r.memTotal)}`,
    },
  ]

  const processColumns: TableProps<ProcessRow>['columns'] = [
    { title: '卡', dataIndex: 'index', key: 'index', width: 80, render: (v: number) => `#${v}` },
    { title: 'PID', dataIndex: 'pid', key: 'pid' },
    {
      title: '显存 (GiB)',
      dataIndex: 'memBytes',
      key: 'memBytes',
      align: 'right',
      render: (v: number) => formatGiB(v),
    },
  ]

  const diskColumns: TableProps<DiskRow>['columns'] = [
    { title: '挂载点', dataIndex: 'mount', key: 'mount' },
    {
      title: '已用 / 总量 (GiB)',
      key: 'usage',
      align: 'right',
      render: (_, r) => `${formatGiB(r.used)} / ${formatGiB(r.total)}`,
    },
    {
      title: '使用率',
      key: 'pct',
      align: 'right',
      width: 100,
      render: (_, r) =>
        r.used !== undefined && r.total ? `${((r.used / r.total) * 100).toFixed(1)}%` : '-',
    },
  ]

  return (
    <Space direction="vertical" size={16} style={{ display: 'flex' }}>
      <Card
        size="small"
        title={
          <Space>
            <Link to="/hosts">
              <Button type="text" size="small" icon={<ArrowLeftOutlined />} />
            </Link>
            <Typography.Text strong>{host}</Typography.Text>
            {agent ? <StatusTag status={agent.status} /> : null}
          </Space>
        }
        loading={agents.isLoading}
      >
        {!agents.isLoading && !agent ? (
          <Alert type="warning" showIcon message={`机器 "${host}" 未在注册表中找到`} />
        ) : (
          <Descriptions size="small" column={{ xs: 1, sm: 2, md: 3 }}>
            <Descriptions.Item label="IP">{agent?.ip ?? '-'}</Descriptions.Item>
            <Descriptions.Item label="硬件">{agent?.hardware_vendor ?? '-'}</Descriptions.Item>
            <Descriptions.Item label="Agent 版本">{agent?.version ?? '-'}</Descriptions.Item>
            <Descriptions.Item label="卡数">{agent?.device_count ?? '-'}</Descriptions.Item>
            <Descriptions.Item label="状态">{agent ? <StatusTag status={agent.status} /> : '-'}</Descriptions.Item>
            <Descriptions.Item label="最后心跳">
              {formatRelative(agent?.last_seen_at)}
              {agent?.last_seen_at ? (
                <Typography.Text type="secondary"> ({formatDateTime(agent.last_seen_at)})</Typography.Text>
              ) : null}
            </Descriptions.Item>
          </Descriptions>
        )}
      </Card>

      <Card title="加速卡" size="small">
        {devices.error ? (
          <Alert type="error" showIcon message="卡列表查询失败" />
        ) : (
          <Table<DeviceRow>
            size="small"
            rowKey="key"
            loading={devices.isLoading}
            columns={deviceColumns}
            dataSource={deviceRows}
            pagination={false}
          />
        )}
      </Card>

      <Card
        title="服务"
        size="small"
        extra={
          <Typography.Text type="secondary">
            {services.data ? `${services.data.filter((s) => s.status === 'running').length} 运行中 / ${services.data.length}` : null}
          </Typography.Text>
        }
      >
        {services.error ? (
          <Alert type="error" showIcon message="服务列表查询失败" />
        ) : (
          <ServicesTable
            data={services.data}
            loading={!!agent && services.isLoading}
            compact
            hideHost
            statusFilters={false}
            size="small"
            pagination={false}
          />
        )}
      </Card>

      <Row gutter={[16, 16]}>
        <Col xs={24} xl={12}>
          <Card title="利用率" size="small">
            <TimeSeriesChart data={util.data} seriesName={deviceSeriesName} unit="percent" loading={util.isLoading} error={util.error} />
          </Card>
        </Col>
        <Col xs={24} xl={12}>
          <Card title="显存占用" size="small">
            <TimeSeriesChart data={mem.data} seriesName={deviceSeriesName} unit="bytes" loading={mem.isLoading} error={mem.error} />
          </Card>
        </Col>
        <Col xs={24} xl={12}>
          <Card title="温度" size="small">
            <TimeSeriesChart data={temp.data} seriesName={deviceSeriesName} unit="celsius" loading={temp.isLoading} error={temp.error} />
          </Card>
        </Col>
        <Col xs={24} xl={12}>
          <Card title="功耗" size="small">
            <TimeSeriesChart data={power.data} seriesName={deviceSeriesName} unit="watts" loading={power.isLoading} error={power.error} />
          </Card>
        </Col>
      </Row>

      <Card title="卡上进程" size="small">
        {processes.error ? (
          <Alert type="error" showIcon message="进程查询失败" />
        ) : (
          <Table<ProcessRow>
            size="small"
            rowKey="key"
            loading={processes.isLoading}
            columns={processColumns}
            dataSource={processRows}
            pagination={{ pageSize: 20, hideOnSinglePage: true }}
          />
        )}
      </Card>

      <Typography.Title level={5} style={{ margin: 0 }}>
        主机
      </Typography.Title>
      <Row gutter={[16, 16]}>
        <Col xs={24} xl={12}>
          <Card title="CPU 使用率" size="small">
            <TimeSeriesChart data={cpu.data} seriesName={hostSeriesName} unit="percent" loading={cpu.isLoading} error={cpu.error} />
          </Card>
        </Col>
        <Col xs={24} xl={12}>
          <Card title="内存占用" size="small">
            <TimeSeriesChart data={hostMem.data} seriesName={hostSeriesName} unit="bytes" loading={hostMem.isLoading} error={hostMem.error} />
          </Card>
        </Col>
        <Col xs={24}>
          <Card title="磁盘" size="small">
            {diskUsed.error || diskTotal.error ? (
              <Alert type="error" showIcon message="磁盘查询失败" />
            ) : (
              <Table<DiskRow>
                size="small"
                rowKey="key"
                loading={diskUsed.isLoading || diskTotal.isLoading}
                columns={diskColumns}
                dataSource={diskRows}
                pagination={false}
              />
            )}
          </Card>
        </Col>
      </Row>
    </Space>
  )
}

export default HostDetailPage
