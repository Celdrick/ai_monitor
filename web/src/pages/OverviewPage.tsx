import { Card, Col, Row, Space, Typography } from 'antd'
import { useCallback, useMemo } from 'react'
import { Link } from 'react-router-dom'
import { firstValue, type MetricLabels } from '../api/metrics'
import { AgentsTable } from '../components/AgentsTable'
import { ServicesTable } from '../components/ServicesTable'
import { StatCard } from '../components/StatCard'
import { TimeSeriesChart } from '../components/TimeSeriesChart'
import { loadKey, useServiceLoad, useServices } from '../hooks/useServices'
import { useAgents, useInstant, useRange } from '../hooks/useMetrics'
import { formatGiB, formatPercent } from '../utils/format'

const TOP_SERVICES = 10

export function OverviewPage() {
  const agents = useAgents()
  const deviceCount = useInstant('cluster_device_count')
  const avgUtil = useInstant('cluster_avg_util')
  const memUsed = useInstant('cluster_mem_used')
  const memTotal = useInstant('cluster_mem_total')
  const utilByHost = useRange('cluster_util_by_host')
  const services = useServices('all')
  const load = useServiceLoad()

  const hostSeriesName = useCallback((m: MetricLabels) => m.host ?? 'cluster', [])

  const total = agents.data?.length ?? 0
  const online = agents.data?.filter((a) => a.status === 'online').length ?? 0

  const usedGiB = formatGiB(firstValue(memUsed.data))
  const totalGiB = formatGiB(firstValue(memTotal.data))

  const serviceTotal = services.data?.length ?? 0
  const serviceRunning = services.data?.filter((s) => s.status === 'running').length ?? 0

  /** Top N by current waiting requests (desc); stopped services sink to the bottom. */
  const topServices = useMemo(() => {
    if (!services.data) return undefined
    const score = (s: (typeof services.data)[number]) =>
      s.status === 'stopped' ? -Infinity : (load.waiting.get(loadKey(s.host, s.name)) ?? -1)
    return [...services.data]
      .sort((a, b) => score(b) - score(a) || a.host.localeCompare(b.host) || a.name.localeCompare(b.name))
      .slice(0, TOP_SERVICES)
  }, [services.data, load.waiting])

  return (
    <div>
      <Row gutter={[16, 16]}>
        <Col xs={24} sm={12} lg={8} xl={{ flex: '1 1 0' }}>
          <StatCard title="在线机器" value={`${online} / ${total}`} loading={agents.isLoading} />
        </Col>
        <Col xs={24} sm={12} lg={8} xl={{ flex: '1 1 0' }}>
          <StatCard
            title="加速卡总数"
            value={firstValue(deviceCount.data) ?? 0}
            loading={deviceCount.isLoading}
          />
        </Col>
        <Col xs={24} sm={12} lg={8} xl={{ flex: '1 1 0' }}>
          <StatCard
            title="平均利用率"
            value={formatPercent(firstValue(avgUtil.data))}
            suffix="%"
            loading={avgUtil.isLoading}
          />
        </Col>
        <Col xs={24} sm={12} lg={8} xl={{ flex: '1 1 0' }}>
          <StatCard
            title="显存占用"
            value={`${usedGiB} / ${totalGiB}`}
            suffix="GiB"
            loading={memUsed.isLoading || memTotal.isLoading}
          />
        </Col>
        <Col xs={24} sm={12} lg={8} xl={{ flex: '1 1 0' }}>
          <StatCard
            title="vLLM 服务"
            value={`${serviceRunning} / ${serviceTotal}`}
            suffix={<Typography.Text type="secondary" style={{ fontSize: 14 }}>运行中</Typography.Text>}
            loading={services.isLoading}
          />
        </Col>
      </Row>

      <Card title="各机器平均利用率" size="small" style={{ marginTop: 16 }}>
        <TimeSeriesChart
          data={utilByHost.data}
          seriesName={hostSeriesName}
          unit="percent"
          loading={utilByHost.isLoading}
          error={utilByHost.error}
        />
      </Card>

      <Card
        title={
          <Space>
            <span>vLLM 服务</span>
            <Typography.Text type="secondary" style={{ fontWeight: 400 }}>
              按 waiting 降序，前 {TOP_SERVICES} 个
            </Typography.Text>
          </Space>
        }
        size="small"
        style={{ marginTop: 16 }}
        extra={<Link to="/services">全部服务</Link>}
      >
        <ServicesTable
          data={topServices}
          loading={services.isLoading}
          load={load}
          compact
          statusFilters={false}
          size="small"
          pagination={false}
        />
      </Card>

      <Card title="机器" size="small" style={{ marginTop: 16 }}>
        <AgentsTable data={agents.data} loading={agents.isLoading} />
      </Card>
    </div>
  )
}

export default OverviewPage
