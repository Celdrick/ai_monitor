import { useQuery } from '@tanstack/react-query'
import { listAgents } from '../api/agents'
import { queryInstant, queryRange } from '../api/metrics'
import { stepForRange } from '../time/timeRange'
import { useTimeRange } from '../time/TimeRangeContext'

export const REFETCH_MS = 15_000

export function useAgents() {
  return useQuery({
    queryKey: ['agents'],
    queryFn: listAgents,
    refetchInterval: REFETCH_MS,
  })
}

export function useInstant(template: string, params: Record<string, string> = {}, enabled = true) {
  const { refreshKey } = useTimeRange()
  return useQuery({
    queryKey: ['metrics', 'instant', template, params, refreshKey],
    queryFn: () => queryInstant(template, params),
    refetchInterval: REFETCH_MS,
    enabled,
  })
}

export function useRange(template: string, params: Record<string, string> = {}, enabled = true) {
  const { range, refreshKey } = useTimeRange()
  const step = stepForRange(range)
  return useQuery({
    queryKey: ['metrics', 'range', template, params, range.start, range.end, step, refreshKey],
    queryFn: () => queryRange(template, params, range.start, range.end, step),
    refetchInterval: REFETCH_MS,
    enabled,
  })
}
