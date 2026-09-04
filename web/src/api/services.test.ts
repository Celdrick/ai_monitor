import { describe, expect, it } from 'vitest'
import { sortServices, type ServiceInfo, type ServiceStatus } from './services'

function svc(host: string, name: string, status: ServiceStatus): ServiceInfo {
  return {
    id: 0,
    agent_id: 0,
    host,
    name,
    source: 'docker',
    port: 8000,
    metrics_url: '',
    pid: null,
    container_name: null,
    model: null,
    vllm_version: null,
    started_at: null,
    log_source: 'none',
    scrape_ok: true,
    active: status !== 'stopped',
    last_seen_at: null,
    status,
  }
}

describe('sortServices', () => {
  it('puts stopped last, then orders by host and name', () => {
    const list = [
      svc('b', 'z', 'running'),
      svc('a', 'y', 'stopped'),
      svc('a', 'x', 'degraded'),
      svc('b', 'a', 'unknown'),
      svc('a', 'a', 'stopped'),
    ]
    const out = sortServices(list).map((s) => `${s.host}/${s.name}:${s.status}`)
    expect(out).toEqual(['a/x:degraded', 'b/a:unknown', 'b/z:running', 'a/a:stopped', 'a/y:stopped'])
  })

  it('does not mutate the input', () => {
    const list = [svc('b', 'b', 'stopped'), svc('a', 'a', 'running')]
    sortServices(list)
    expect(list[0].host).toBe('b')
  })
})
