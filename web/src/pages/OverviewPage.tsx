import { Card, Col, Row } from 'antd'
import { useCallback } from 'react'
import { firstValue, type MetricLabels } from '../api/metrics'
import { AgentsTable } from '../components/AgentsTable'
import { StatCard } from '../components/StatCard'
import { TimeSeriesChart } from '../components/TimeSeriesChart'
import { useAgents, useInstant, useRange } from '../hooks/useMetrics'
import { formatGiB, formatPercent } from '../utils/format'

export function OverviewPage() {
  const agents = useAgents()
  const deviceCount = useInstant('cluster_device_count')
  const avgUtil = useInstant('cluster_avg_util')
  const memUsed = useInstant('cluster_mem_used')
  const memTotal = useInstant('cluster_mem_total')
  const utilByHost = useRange('cluster_util_by_host')

  const hostSeriesName = useCallback((m: MetricLabels) => m.host ?? 'cluster', [])

  const total = agents.data?.length ?? 0
  const online = agents.data?.filter((a) => a.status === 'online').length ?? 0

  const usedGiB = formatGiB(firstValue(memUsed.data))
  const totalGiB = formatGiB(firstValue(memTotal.data))

  return (
    <div>
      <Row gutter={[16, 16]}>
        <Col xs={24} sm={12} lg={6}>
          <StatCard title="在线机器" value={`${online} / ${total}`} loading={agents.isLoading} />
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <StatCard
            title="加速卡总数"
            value={firstValue(deviceCount.data) ?? 0}
            loading={deviceCount.isLoading}
          />
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <StatCard
            title="平均利用率"
            value={formatPercent(firstValue(avgUtil.data))}
            suffix="%"
            loading={avgUtil.isLoading}
          />
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <StatCard
            title="显存占用"
            value={`${usedGiB} / ${totalGiB}`}
            suffix="GiB"
            loading={memUsed.isLoading || memTotal.isLoading}
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

      <Card title="机器" size="small" style={{ marginTop: 16 }}>
        <AgentsTable data={agents.data} loading={agents.isLoading} />
      </Card>
    </div>
  )
}

export default OverviewPage
