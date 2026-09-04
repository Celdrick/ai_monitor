import { api } from './client'

export type AgentStatus = 'online' | 'offline' | 'never'

export interface AgentInfo {
  id: number
  host: string
  ip: string | null
  advertise_address: string | null
  version: string | null
  hardware_vendor: string | null
  device_count: number
  last_seen_at: string | null
  status: AgentStatus
}

export interface AgentCreated {
  id: number
  host: string
  token: string
}

export async function listAgents(): Promise<AgentInfo[]> {
  const res = await api.get<AgentInfo[]>('/agents')
  return res.data
}

export async function getAgent(id: number): Promise<AgentInfo> {
  const res = await api.get<AgentInfo>(`/agents/${id}`)
  return res.data
}

export async function createAgent(host: string): Promise<AgentCreated> {
  const res = await api.post<AgentCreated>('/agents', { host })
  return res.data
}

export async function rotateAgentToken(id: number): Promise<AgentCreated> {
  const res = await api.post<AgentCreated>(`/agents/${id}/rotate-token`)
  return res.data
}

export async function deleteAgent(id: number): Promise<void> {
  await api.delete(`/agents/${id}`)
}
