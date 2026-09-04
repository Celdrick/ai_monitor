import { ArrowLeftOutlined } from '@ant-design/icons'
import type { UseQueryResult } from '@tanstack/react-query'
import { Alert, Button, Card, Col, Descriptions, Row, Space, Tabs, Tag, Tooltip, Typography } from 'antd'
import { useMemo, type CSSProperties } from 'react'
import { Link, useParams } from 'react-router-dom'
import { errorMessage } from '../api/client'
import type { MetricLabels, RangeResult } from '../api/metrics'
import type { ServiceDetail } from '../api/services'
import { EnvTable } from '../components/EnvTable'
import { LogViewer } from '../components/LogViewer'
import { ServiceStatusTag, SourceTag } from '../components/ServicesTable'
import { TimeSeriesChart } from '../components/TimeSeriesChart'
import { useRange } from '../hooks/useMetrics'
import { useService } from '../hooks/useServices'
import { windowForRange } from '../metrics/window'
import { useTimeRange } from '../time/TimeRangeContext'
import { formatDateTime, formatRelative } from '../utils/format'

const MONO: CSSProperties = {
  fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace',
  fontSize: 12,
  wordBreak: 'break-all',
  whiteSpace: 'pre-wrap',
}

const LOG_SOURCE_LABEL: Record<ServiceDetail['log_source'], string> = {
  docker: 'Docker 日志',
  file: '日志文件',
  none: '无',
}

/** Merge several range queries into one RangeResult, tagging each series with a fixed name. */
function useCombinedRange(parts: { label: string; query: UseQueryResult<RangeResult> }[]) {
  const datas = parts.map((p) => p.query.data)
  const loading = parts.some((p) => p.query.isLoading)
  const error = parts.find((p) => p.query.error)?.query.error
  const data = useMemo<RangeResult | undefined>(() => {
    if (datas.every((d) => d === undefined)) return undefined
    return {
      resultType: 'matrix',
      result: parts.flatMap((p, i) =>
        (datas[i]?.result ?? []).map((s) => ({ metric: { ...s.metric, __series: p.label }, values: s.values })),
      ),
    }
    // `parts` is rebuilt every render; the query data identities are what matter
  }, datas)
  return { data, loading, error }
}

const seriesByTag = (m: MetricLabels) => m.__series ?? '-'
const seriesByQuantile = (m: MetricLabels) => m.q ?? m.quantile ?? '-'
const kvName = () => 'KV cache'
const preemptName = () => 'preemptions'

function MetricsTab({ service }: { service: ServiceDetail }) {
  const { range } = useTimeRange()
  const window = windowForRange(range)
  const base = useMemo(() => ({ host: service.host, service: service.name }), [service.host, service.name])
  const withWindow = useMemo(() => ({ ...base, window }), [base, window])

  const running = useRange('vllm_running', base)
  const waiting = useRange('vllm_waiting', base)
  const promptRate = useRange('vllm_prompt_tokens_rate', withWindow)
  const genRate = useRange('vllm_generation_tokens_rate', withWindow)
  const ttft = useRange('vllm_ttft_quantiles', withWindow)
  const tpot = useRange('vllm_tpot_quantiles', withWindow)
  const e2e = useRange('vllm_e2e_quantiles', withWindow)
  const kv = useRange('vllm_kv_cache_perc', base)
  const preempt = useRange('vllm_preemption_rate', withWindow)

  const requests = useCombinedRange([
    { label: 'running', query: running },
    { label: 'waiting', query: waiting },
  ])
  const tokens = useCombinedRange([
    { label: 'prompt', query: promptRate },
    { label: 'generation', query: genRate },
  ])

  return (
    <Row gutter={[16, 16]}>
      <Col xs={24} xl={12}>
        <Card title="请求数（running / waiting）" size="small">
          <TimeSeriesChart data={requests.data} seriesName={seriesByTag} unit="count" loading={requests.loading} error={requests.error} />
        </Card>
      </Col>
      <Col xs={24} xl={12}>
        <Card title={`Token 吞吐（tokens/s，窗口 ${window}）`} size="small">
          <TimeSeriesChart data={tokens.data} seriesName={seriesByTag} unit="rate" loading={tokens.loading} error={tokens.error} />
        </Card>
      </Col>
      <Col xs={24} xl={8}>
        <Card title="TTFT（首 token 延迟）" size="small">
          <TimeSeriesChart data={ttft.data} seriesName={seriesByQuantile} unit="seconds" loading={ttft.isLoading} error={ttft.error} />
        </Card>
      </Col>
      <Col xs={24} xl={8}>
        <Card title="TPOT（每 token 延迟）" size="small">
          <TimeSeriesChart data={tpot.data} seriesName={seriesByQuantile} unit="seconds" loading={tpot.isLoading} error={tpot.error} />
        </Card>
      </Col>
      <Col xs={24} xl={8}>
        <Card title="E2E 请求延迟" size="small">
          <TimeSeriesChart data={e2e.data} seriesName={seriesByQuantile} unit="seconds" loading={e2e.isLoading} error={e2e.error} />
        </Card>
      </Col>
      <Col xs={24} xl={12}>
        <Card title="KV cache 使用率" size="small">
          <TimeSeriesChart data={kv.data} seriesName={kvName} unit="percent" loading={kv.isLoading} error={kv.error} />
        </Card>
      </Col>
      <Col xs={24} xl={12}>
        <Card title={`抢占速率（次/s，窗口 ${window}）`} size="small">
          <TimeSeriesChart data={preempt.data} seriesName={preemptName} unit="rate" loading={preempt.isLoading} error={preempt.error} />
        </Card>
      </Col>
    </Row>
  )
}

function ProcessTab({ service }: { service: ServiceDetail }) {
  return (
    <Space direction="vertical" size={16} style={{ display: 'flex' }}>
      <Card title="启动命令" size="small">
        {service.cmdline ? (
          <Typography.Paragraph copyable={{ text: service.cmdline }} style={{ ...MONO, margin: 0 }}>
            {service.cmdline}
          </Typography.Paragraph>
        ) : (
          <Typography.Text type="secondary">未采集到启动命令（进程信息不可读或服务已停止）</Typography.Text>
        )}
      </Card>
      <Card title="进程信息" size="small">
        <Descriptions size="small" column={{ xs: 1, sm: 2 }}>
          <Descriptions.Item label="PID">{service.pid ?? '-'}</Descriptions.Item>
          <Descriptions.Item label="工作目录">
            <Typography.Text style={MONO}>{service.cwd ?? '-'}</Typography.Text>
          </Descriptions.Item>
          <Descriptions.Item label="容器 ID">
            <Typography.Text style={MONO} copyable={service.container_id ? { text: service.container_id } : false}>
              {service.container_id ?? '-'}
            </Typography.Text>
          </Descriptions.Item>
          <Descriptions.Item label="容器名">{service.container_name ?? '-'}</Descriptions.Item>
          <Descriptions.Item label="日志来源">{LOG_SOURCE_LABEL[service.log_source] ?? service.log_source}</Descriptions.Item>
          <Descriptions.Item label="日志路径">
            <Typography.Text style={MONO}>{service.log_path ?? '-'}</Typography.Text>
          </Descriptions.Item>
          <Descriptions.Item label="profiler 目录">
            <Typography.Text style={MONO}>{service.profiler_dir ?? '-'}</Typography.Text>
          </Descriptions.Item>
          <Descriptions.Item label="metrics URL">
            <Typography.Text style={MONO}>{service.metrics_url}</Typography.Text>
          </Descriptions.Item>
          <Descriptions.Item label="首次发现">{formatDateTime(service.first_seen_at)}</Descriptions.Item>
          <Descriptions.Item label="最后上报">{formatDateTime(service.last_seen_at)}</Descriptions.Item>
        </Descriptions>
      </Card>
      <Card title="环境变量" size="small">
        <EnvTable env={service.env} />
      </Card>
    </Space>
  )
}

function LogsTab({ service }: { service: ServiceDetail }) {
  return (
    <Space direction="vertical" size={12} style={{ display: 'flex' }}>
      {service.log_source === 'none' ? (
        <Alert
          type="info"
          showIcon
          message="该服务未配置日志来源"
          description="进程方式发现的服务需在 Agent 配置 services 中手动登记 log_path 才能采集日志；此处仍可查询历史日志。"
        />
      ) : null}
      <LogViewer host={service.host} service={service.name} />
    </Space>
  )
}

export function ServiceDetailPage() {
  const { id: rawId = '' } = useParams<{ id: string }>()
  const id = Number(rawId)
  const service = useService(Number.isFinite(id) ? id : undefined)
  const s = service.data

  const tabs = useMemo(
    () =>
      s
        ? [
            { key: 'metrics', label: '指标', children: <MetricsTab service={s} /> },
            { key: 'process', label: '进程', children: <ProcessTab service={s} /> },
            { key: 'logs', label: '日志', children: <LogsTab service={s} /> },
          ]
        : [],
    [s],
  )

  return (
    <Space direction="vertical" size={16} style={{ display: 'flex' }}>
      <Card
        size="small"
        title={
          <Space>
            <Link to="/services">
              <Button type="text" size="small" icon={<ArrowLeftOutlined />} />
            </Link>
            <Typography.Text strong>{s?.name ?? `服务 #${rawId}`}</Typography.Text>
            {s ? <ServiceStatusTag status={s.status} /> : null}
            {s ? <SourceTag source={s.source} /> : null}
          </Space>
        }
        loading={service.isLoading}
      >
        {service.error ? (
          <Alert type="error" showIcon message="服务查询失败" description={errorMessage(service.error)} />
        ) : s ? (
          <Descriptions size="small" column={{ xs: 1, sm: 2, md: 3, xl: 4 }}>
            <Descriptions.Item label="机器">
              <Link to={`/hosts/${encodeURIComponent(s.host)}`}>{s.host}</Link>
            </Descriptions.Item>
            <Descriptions.Item label="模型">{s.model ?? '-'}</Descriptions.Item>
            <Descriptions.Item label="vLLM 版本">{s.vllm_version ?? '-'}</Descriptions.Item>
            <Descriptions.Item label="端口">{s.port}</Descriptions.Item>
            <Descriptions.Item label="来源">
              <SourceTag source={s.source} />
            </Descriptions.Item>
            <Descriptions.Item label="容器">{s.container_name ?? '-'}</Descriptions.Item>
            <Descriptions.Item label="PID">{s.pid ?? '-'}</Descriptions.Item>
            <Descriptions.Item label="启动时间">
              {s.started_at ? (
                <Tooltip title={formatDateTime(s.started_at)}>
                  <span>{formatRelative(s.started_at)}</span>
                </Tooltip>
              ) : (
                '-'
              )}
            </Descriptions.Item>
            <Descriptions.Item label="状态">
              <Space size={4}>
                <ServiceStatusTag status={s.status} />
                {s.status === 'degraded' ? <Tag color="orange">/metrics 抓取失败</Tag> : null}
              </Space>
            </Descriptions.Item>
            <Descriptions.Item label="日志来源">{LOG_SOURCE_LABEL[s.log_source] ?? s.log_source}</Descriptions.Item>
            <Descriptions.Item label="最后心跳">
              <Tooltip title={formatDateTime(s.last_seen_at)}>
                <span>{formatRelative(s.last_seen_at)}</span>
              </Tooltip>
            </Descriptions.Item>
          </Descriptions>
        ) : null}
      </Card>

      {s ? <Tabs defaultActiveKey="metrics" items={tabs} destroyOnHidden /> : null}
    </Space>
  )
}

export default ServiceDetailPage
