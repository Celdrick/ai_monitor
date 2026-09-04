export type Preset = '1h' | '6h' | '24h' | '7d' | '30d'

export const PRESETS: Preset[] = ['1h', '6h', '24h', '7d', '30d']

/** Unix seconds. */
export interface TimeRange {
  start: number
  end: number
}

const PRESET_SECONDS: Record<Preset, number> = {
  '1h': 3600,
  '6h': 6 * 3600,
  '24h': 24 * 3600,
  '7d': 7 * 86400,
  '30d': 30 * 86400,
}

export function presetToRange(preset: Preset, nowMs: number = Date.now()): TimeRange {
  const end = Math.floor(nowMs / 1000)
  return { start: end - PRESET_SECONDS[preset], end }
}

/** Target ~300 points: max(15, ceil(span/300)) rounded up to a multiple of 15 seconds. */
export function stepForRange(range: TimeRange): string {
  const span = Math.max(0, range.end - range.start)
  const raw = Math.max(15, Math.ceil(span / 300))
  const step = Math.ceil(raw / 15) * 15
  return `${step}s`
}

export const PRESET_LABELS: Record<Preset, string> = {
  '1h': '最近 1 小时',
  '6h': '最近 6 小时',
  '24h': '最近 24 小时',
  '7d': '最近 7 天',
  '30d': '最近 30 天',
}
