import type { TimeRange } from '../time/timeRange'

export type RateWindow = '1m' | '5m' | '30m'

const SIX_HOURS = 6 * 3600
const SEVEN_DAYS = 7 * 86400

/** Rate window for vLLM PromQL templates: ≤6h → 1m, ≤7d → 5m, otherwise 30m. */
export function windowForRange(range: TimeRange): RateWindow {
  const span = Math.max(0, range.end - range.start)
  if (span <= SIX_HOURS) return '1m'
  if (span <= SEVEN_DAYS) return '5m'
  return '30m'
}
