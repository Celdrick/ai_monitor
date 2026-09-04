import dayjs from 'dayjs'
import relativeTime from 'dayjs/plugin/relativeTime'
import 'dayjs/locale/zh-cn'

dayjs.extend(relativeTime)
dayjs.locale('zh-cn')

const GIB = 1024 ** 3

export function formatGiB(bytes: number | undefined | null, digits = 2): string {
  if (bytes === undefined || bytes === null || !Number.isFinite(bytes)) return '-'
  return (bytes / GIB).toFixed(digits)
}

export function formatPercent(v: number | undefined | null, digits = 1): string {
  if (v === undefined || v === null || !Number.isFinite(v)) return '-'
  return v.toFixed(digits)
}

export function formatRelative(iso: string | null | undefined): string {
  if (!iso) return '从未'
  const d = dayjs(iso)
  if (!d.isValid()) return iso
  return d.fromNow()
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return '-'
  const d = dayjs(iso)
  return d.isValid() ? d.format('YYYY-MM-DD HH:mm:ss') : iso
}

export type Unit = 'percent' | 'bytes' | 'celsius' | 'watts' | 'seconds' | 'rate' | 'count'

/** Seconds → "123 ms" below 1s, otherwise "1.23 s". */
export function formatSeconds(v: number, axis = false): string {
  if (!Number.isFinite(v)) return '-'
  if (Math.abs(v) < 1) return `${axis ? trimNumber(v * 1000) : (v * 1000).toFixed(0)} ms`
  return `${axis ? trimNumber(v) : v.toFixed(2)} s`
}

function trimNumber(v: number): string {
  return Number.isInteger(v) ? String(v) : String(Number(v.toFixed(2)))
}

export function formatByUnit(v: number, unit?: Unit): string {
  switch (unit) {
    case 'percent':
      return `${v.toFixed(1)}%`
    case 'bytes':
      return `${formatGiB(v)} GiB`
    case 'celsius':
      return `${v.toFixed(0)}°C`
    case 'watts':
      return `${v.toFixed(0)} W`
    case 'seconds':
      return formatSeconds(v)
    case 'rate':
      return `${v.toFixed(2)} /s`
    case 'count':
      return Number.isInteger(v) ? String(v) : v.toFixed(1)
    default:
      return Number.isInteger(v) ? String(v) : v.toFixed(2)
  }
}

export function axisLabelByUnit(v: number, unit?: Unit): string {
  switch (unit) {
    case 'percent':
      return `${v}%`
    case 'bytes':
      return `${formatGiB(v, v >= 10 * GIB ? 0 : 1)} GiB`
    case 'celsius':
      return `${v}°C`
    case 'watts':
      return `${v} W`
    case 'seconds':
      return formatSeconds(v, true)
    case 'rate':
      return `${trimNumber(v)}/s`
    default:
      return String(v)
  }
}
