import { useQuery } from '@tanstack/react-query'
import { useMemo } from 'react'
import { sampleValue, type InstantResult } from '../api/metrics'
import { getService, listAgentServices, listServices, type ActiveFilter } from '../api/services'
import { REFETCH_MS, useInstant } from './useMetrics'

export function useServices(active: ActiveFilter = 'all') {
  return useQuery({
    queryKey: ['services', active],
    queryFn: () => listServices(active),
    refetchInterval: REFETCH_MS,
  })
}

export function useService(id: number | undefined) {
  return useQuery({
    queryKey: ['service', id],
    queryFn: () => getService(id as number),
    enabled: id !== undefined && Number.isFinite(id),
    refetchInterval: REFETCH_MS,
  })
}

export function useAgentServices(agentId: number | undefined) {
  return useQuery({
    queryKey: ['agent-services', agentId],
    queryFn: () => listAgentServices(agentId as number),
    enabled: agentId !== undefined,
    refetchInterval: REFETCH_MS,
  })
}

export function loadKey(host: string, service: string): string {
  return `${host}\u0000${service}`
}

function toMap(result: InstantResult | undefined): Map<string, number> {
  const m = new Map<string, number>()
  result?.result.forEach((s) => {
    const v = sampleValue(s)
    if (Number.isFinite(v)) m.set(loadKey(s.metric.host ?? '', s.metric.service ?? ''), v)
  })
  return m
}

export interface ServiceLoad {
  running: Map<string, number>
  waiting: Map<string, number>
  loading: boolean
}

/** Current running / waiting request counts per (host, service) from cluster-wide instant queries. */
export function useServiceLoad(enabled = true): ServiceLoad {
  const running = useInstant('cluster_vllm_running_by_service', {}, enabled)
  const waiting = useInstant('cluster_vllm_waiting_by_service', {}, enabled)
  const runningMap = useMemo(() => toMap(running.data), [running.data])
  const waitingMap = useMemo(() => toMap(waiting.data), [waiting.data])
  return { running: runningMap, waiting: waitingMap, loading: running.isLoading || waiting.isLoading }
}
