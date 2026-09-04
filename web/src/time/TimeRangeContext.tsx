import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from 'react'
import { presetToRange, type Preset, type TimeRange } from './timeRange'

export interface TimeRangeContextValue {
  /** Current absolute range (unix seconds). Recomputed on every refresh in preset mode. */
  range: TimeRange
  /** Active preset, or null when a custom range is selected. */
  preset: Preset | null
  setPreset: (preset: Preset) => void
  setCustom: (start: number, end: number) => void
  /** Increments on each refresh; include in query keys to force refetch. */
  refreshKey: number
  refresh: () => void
}

const TimeRangeContext = createContext<TimeRangeContextValue | undefined>(undefined)

interface State {
  preset: Preset | null
  custom: TimeRange | null
  refreshKey: number
  computedAt: number
}

export function TimeRangeProvider({
  children,
  defaultPreset = '1h',
}: {
  children: ReactNode
  defaultPreset?: Preset
}) {
  const [state, setState] = useState<State>(() => ({
    preset: defaultPreset,
    custom: null,
    refreshKey: 0,
    computedAt: Date.now(),
  }))

  const setPreset = useCallback((preset: Preset) => {
    setState((s) => ({ ...s, preset, custom: null, computedAt: Date.now(), refreshKey: s.refreshKey + 1 }))
  }, [])

  const setCustom = useCallback((start: number, end: number) => {
    const s0 = Math.min(start, end)
    const e0 = Math.max(start, end)
    setState((s) => ({
      ...s,
      preset: null,
      custom: { start: Math.floor(s0), end: Math.floor(e0) },
      computedAt: Date.now(),
      refreshKey: s.refreshKey + 1,
    }))
  }, [])

  const refresh = useCallback(() => {
    setState((s) => ({ ...s, computedAt: Date.now(), refreshKey: s.refreshKey + 1 }))
  }, [])

  const range = useMemo<TimeRange>(() => {
    if (state.preset) return presetToRange(state.preset, state.computedAt)
    return state.custom ?? presetToRange('1h', state.computedAt)
  }, [state.preset, state.custom, state.computedAt])

  const value = useMemo<TimeRangeContextValue>(
    () => ({ range, preset: state.preset, setPreset, setCustom, refreshKey: state.refreshKey, refresh }),
    [range, state.preset, state.refreshKey, setPreset, setCustom, refresh],
  )

  return <TimeRangeContext.Provider value={value}>{children}</TimeRangeContext.Provider>
}

export function useTimeRange(): TimeRangeContextValue {
  const ctx = useContext(TimeRangeContext)
  if (!ctx) throw new Error('useTimeRange must be used within <TimeRangeProvider>')
  return ctx
}
