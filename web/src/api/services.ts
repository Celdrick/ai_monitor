import { api } from './client'

export type ServiceStatus = 'running' | 'degraded' | 'stopped' | 'unknown'
export type ServiceSource = 'docker' | 'process' | 'manual'
export type LogSource = 'docker' | 'file' | 'none'

export interface ServiceInfo {
  id: number
  agent_id: number
  host: string
  name: string
  source: ServiceSource
  port: number
  metrics_url: string
  pid: number | null
  container_name: string | null
  model: string | null
  vllm_version: string | null
  started_at: string | null
  log_source: LogSource
  scrape_ok: boolean
  active: boolean
  last_seen_at: string | null
  status: ServiceStatus
}

export interface ServiceDetail extends ServiceInfo {
  cmdline: string | null
  cwd: string | null
  env: Record<string, string>
  container_id: string | null
  log_path: string | null
  profiler_dir: string | null
  first_seen_at: string
}

export type ActiveFilter = 'all' | 'true' | 'false'

export async function listServices(active: ActiveFilter = 'all'): Promise<ServiceInfo[]> {
  const res = await api.get<ServiceInfo[]>('/services', { params: { active } })
  return res.data
}

export async function getService(id: number): Promise<ServiceDetail> {
  const res = await api.get<ServiceDetail>(`/services/${id}`)
  return res.data
}

export async function listAgentServices(agentId: number): Promise<ServiceInfo[]> {
  const res = await api.get<ServiceInfo[]>(`/agents/${agentId}/services`)
  return res.data
}

/** Default ordering: stopped services last, everything else by host then name. */
export function sortServices(list: ServiceInfo[]): ServiceInfo[] {
  return [...list].sort((a, b) => {
    const sa = a.status === 'stopped' ? 1 : 0
    const sb = b.status === 'stopped' ? 1 : 0
    if (sa !== sb) return sa - sb
    return a.host.localeCompare(b.host) || a.name.localeCompare(b.name)
  })
}
