import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ServiceDetail } from '../api/services'

const role = { current: 'admin' as string }

vi.mock('../auth/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 1, username: 'u', role: role.current },
    isAuthenticated: true,
    login: async () => ({ id: 1, username: 'u', role: role.current }),
    logout: () => {},
  }),
}))

const service: ServiceDetail = {
  id: 7,
  agent_id: 1,
  host: 'fake-gpu-01',
  name: 'fake-vllm-0',
  source: 'manual',
  port: 18000,
  metrics_url: 'http://127.0.0.1:18000',
  pid: 1,
  container_name: null,
  model: 'fake/Qwen2.5-7B-Instruct',
  vllm_version: '0.0.0',
  started_at: null,
  log_source: 'file',
  scrape_ok: true,
  active: true,
  last_seen_at: '2026-09-05T00:00:00Z',
  status: 'running',
  cmdline: 'vllm serve',
  cwd: '/',
  env: {},
  container_id: null,
  log_path: '/tmp/x.log',
  profiler_dir: '/tmp/prof',
  first_seen_at: '2026-09-05T00:00:00Z',
}

vi.mock('../hooks/useServices', () => ({
  useService: () => ({ data: service, isLoading: false, error: null }),
}))

vi.mock('../hooks/useMetrics', () => ({
  useRange: () => ({ data: undefined, isLoading: false, error: null }),
}))

vi.mock('../time/TimeRangeContext', () => ({
  useTimeRange: () => ({ range: { start: 0, end: 1 }, refreshKey: 0 }),
}))

import { ServiceDetailPage } from './ServiceDetailPage'

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/services/7']}>
        <Routes>
          <Route path="/services/:id" element={<ServiceDetailPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('ServiceDetailPage debug tab', () => {
  beforeEach(() => {
    role.current = 'admin'
    Object.defineProperty(window, 'matchMedia', {
      writable: true,
      value: (query: string) => ({
        matches: false,
        media: query,
        onchange: null,
        addListener: () => {},
        removeListener: () => {},
        addEventListener: () => {},
        removeEventListener: () => {},
        dispatchEvent: () => false,
      }),
    })
  })

  it('shows 调试 for admin', () => {
    renderPage()
    expect(screen.getByText('调试')).toBeTruthy()
  })

  it('hides 调试 for viewer', () => {
    role.current = 'viewer'
    renderPage()
    expect(screen.queryByText('调试')).toBeNull()
    expect(screen.getByText('指标')).toBeTruthy()
  })
})
