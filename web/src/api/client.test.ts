import axios, { type AxiosAdapter, type AxiosResponse, type InternalAxiosRequestConfig } from 'axios'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from './client'
import { authStorage } from './storage'

type Handler = (config: InternalAxiosRequestConfig) => AxiosResponse | Promise<AxiosResponse>

function makeAdapter(handler: Handler): AxiosAdapter {
  return async (config) => {
    const res = await handler(config)
    if (res.status >= 400) {
      throw new axios.AxiosError(`HTTP ${res.status}`, String(res.status), config, undefined, res)
    }
    return res
  }
}

function respond(config: InternalAxiosRequestConfig, status: number, data: unknown): AxiosResponse {
  return { status, statusText: String(status), headers: {}, config, data }
}

describe('api client auth interceptors', () => {
  const originalAdapter = axios.defaults.adapter
  const originalApiAdapter = api.defaults.adapter

  beforeEach(() => {
    localStorage.clear()
    authStorage.setTokens('old-access', 'refresh-1')
  })

  afterEach(() => {
    axios.defaults.adapter = originalAdapter
    api.defaults.adapter = originalApiAdapter
    vi.restoreAllMocks()
  })

  it('injects the bearer token', async () => {
    const seen: string[] = []
    api.defaults.adapter = makeAdapter((c) => {
      seen.push(String(c.headers.Authorization))
      return respond(c, 200, [])
    })
    await api.get('/agents')
    expect(seen).toEqual(['Bearer old-access'])
  })

  it('refreshes once on 401 and replays the request', async () => {
    let refreshCalls = 0
    const apiCalls: string[] = []

    axios.defaults.adapter = makeAdapter((c) => {
      // plain axios is used only for the refresh call
      expect(c.url).toBe('/api/auth/refresh')
      expect(c.data).toBe(JSON.stringify({ refresh_token: 'refresh-1' }))
      refreshCalls += 1
      return respond(c, 200, { access_token: 'new-access', token_type: 'bearer' })
    })
    api.defaults.adapter = makeAdapter((c) => {
      const auth = String(c.headers.Authorization)
      apiCalls.push(auth)
      if (auth === 'Bearer old-access') return respond(c, 401, { detail: 'expired' })
      return respond(c, 200, { ok: true })
    })

    const [a, b] = await Promise.all([api.get('/agents'), api.get('/metrics/query')])
    expect(a.data).toEqual({ ok: true })
    expect(b.data).toEqual({ ok: true })
    // concurrent 401s share a single refresh
    expect(refreshCalls).toBe(1)
    expect(apiCalls.filter((h) => h === 'Bearer new-access')).toHaveLength(2)
    expect(authStorage.getAccessToken()).toBe('new-access')
  })

  it('clears storage and redirects to /login when refresh fails', async () => {
    const assign = vi.fn()
    vi.spyOn(window, 'location', 'get').mockReturnValue({
      ...window.location,
      pathname: '/',
      assign,
    } as unknown as Location)

    axios.defaults.adapter = makeAdapter((c) => respond(c, 401, { detail: 'bad refresh' }))
    api.defaults.adapter = makeAdapter((c) => respond(c, 401, { detail: 'expired' }))

    await expect(api.get('/agents')).rejects.toBeTruthy()
    expect(authStorage.getAccessToken()).toBeNull()
    expect(authStorage.getRefreshToken()).toBeNull()
    expect(assign).toHaveBeenCalledWith('/login')
  })

  it('does not attempt refresh for /auth endpoints', async () => {
    let refreshCalls = 0
    axios.defaults.adapter = makeAdapter((c) => {
      refreshCalls += 1
      return respond(c, 200, {})
    })
    api.defaults.adapter = makeAdapter((c) => respond(c, 401, { detail: 'bad credentials' }))

    await expect(api.post('/auth/login', { username: 'x', password: 'y' })).rejects.toBeTruthy()
    expect(refreshCalls).toBe(0)
    // tokens untouched: a failed login must not log out an existing session
    expect(authStorage.getAccessToken()).toBe('old-access')
  })
})
