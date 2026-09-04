import { api } from './client'

export type MetricLabels = Record<string, string>

export interface RangeSeries {
  metric: MetricLabels
  values: [number, string][]
}

export interface InstantSample {
  metric: MetricLabels
  value: [number, string]
}

export interface RangeResult {
  resultType: 'matrix'
  result: RangeSeries[]
}

export interface InstantResult {
  resultType: 'vector'
  result: InstantSample[]
}

export type TemplateName =
  | 'cluster_device_count'
  | 'cluster_avg_util'
  | 'cluster_mem_used'
  | 'cluster_mem_total'
  | 'cluster_util_by_host'
  | 'host_devices'
  | 'host_device_util'
  | 'host_device_mem_used'
  | 'host_device_mem_total'
  | 'host_device_temp'
  | 'host_device_power'
  | 'host_device_process_mem'
  | 'host_cpu'
  | 'host_mem_used'
  | 'host_mem_total'
  | 'host_disk_used'
  | 'host_disk_total'

export async function queryInstant(
  template: TemplateName | string,
  params: Record<string, string> = {},
): Promise<InstantResult> {
  const res = await api.get<InstantResult>('/metrics/query', {
    params: { template, ...params },
  })
  return res.data
}

export async function queryRange(
  template: TemplateName | string,
  params: Record<string, string>,
  start: number,
  end: number,
  step: string,
): Promise<RangeResult> {
  const res = await api.get<RangeResult>('/metrics/query_range', {
    params: { template, ...params, start, end, step },
  })
  return res.data
}

/** First scalar value of an instant vector, or undefined when empty / NaN. */
export function firstValue(result: InstantResult | undefined): number | undefined {
  const raw = result?.result?.[0]?.value?.[1]
  if (raw === undefined) return undefined
  const n = Number(raw)
  return Number.isFinite(n) ? n : undefined
}

export function sampleValue(sample: InstantSample): number {
  const n = Number(sample.value[1])
  return Number.isFinite(n) ? n : NaN
}
