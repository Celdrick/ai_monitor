import { Alert, Empty, Spin } from 'antd'
import type { EChartsOption } from 'echarts'
import ReactECharts from 'echarts-for-react'
import { useMemo } from 'react'
import type { MetricLabels, RangeResult } from '../api/metrics'
import { errorMessage } from '../api/client'
import { axisLabelByUnit, formatByUnit, type Unit } from '../utils/format'

export interface TimeSeriesChartProps {
  data: RangeResult | undefined
  seriesName: (metric: MetricLabels) => string
  unit?: Unit
  loading?: boolean
  error?: unknown
  height?: number
}

export function TimeSeriesChart({
  data,
  seriesName,
  unit,
  loading,
  error,
  height = 280,
}: TimeSeriesChartProps) {
  const option = useMemo<EChartsOption | null>(() => {
    if (!data || data.result.length === 0) return null
    const series = data.result.map((s) => ({
      name: seriesName(s.metric),
      type: 'line' as const,
      showSymbol: false,
      smooth: false,
      connectNulls: false,
      data: s.values.map(([ts, v]) => {
        const n = Number(v)
        return [ts * 1000, Number.isFinite(n) ? n : null] as [number, number | null]
      }),
    }))
    return {
      animation: false,
      grid: { left: 56, right: 24, top: 32, bottom: 56, containLabel: false },
      tooltip: {
        trigger: 'axis',
        valueFormatter: (v) => (typeof v === 'number' ? formatByUnit(v, unit) : '-'),
      },
      legend: {
        type: 'scroll',
        bottom: 0,
      },
      xAxis: { type: 'time' },
      yAxis: {
        type: 'value',
        min: 0,
        max: unit === 'percent' ? 100 : undefined,
        axisLabel: { formatter: (v: number) => axisLabelByUnit(v, unit) },
      },
      series,
    }
  }, [data, seriesName, unit])

  if (error) {
    return (
      <div style={{ height, display: 'flex', alignItems: 'center' }}>
        <Alert type="error" showIcon message="查询失败" description={errorMessage(error)} style={{ width: '100%' }} />
      </div>
    )
  }

  if (!option) {
    return (
      <div style={{ height, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        {loading ? <Spin /> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无数据" />}
      </div>
    )
  }

  return (
    <Spin spinning={!!loading} delay={300}>
      <ReactECharts option={option} notMerge style={{ height }} />
    </Spin>
  )
}

export default TimeSeriesChart
