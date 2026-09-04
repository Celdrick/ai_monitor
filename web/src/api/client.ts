import axios, { AxiosError, type AxiosRequestConfig, type InternalAxiosRequestConfig } from 'axios'
import { authStorage } from './storage'

export const api = axios.create({
  baseURL: '/api',
  timeout: 30_000,
})

type RetriableConfig = InternalAxiosRequestConfig & { _retried?: boolean }

api.interceptors.request.use((config) => {
  const token = authStorage.getAccessToken()
  if (token) {
    config.headers.set('Authorization', `Bearer ${token}`)
  }
  return config
})

/** Shared in-flight refresh so concurrent 401s trigger a single refresh call. */
let refreshPromise: Promise<string> | null = null

async function refreshAccessToken(): Promise<string> {
  if (!refreshPromise) {
    const refresh = authStorage.getRefreshToken()
    if (!refresh) return Promise.reject(new Error('no refresh token'))
    refreshPromise = axios
      .post<{ access_token: string; token_type: string }>('/api/auth/refresh', {
        refresh_token: refresh,
      })
      .then((res) => {
        authStorage.setTokens(res.data.access_token)
        return res.data.access_token
      })
      .finally(() => {
        refreshPromise = null
      })
  }
  return refreshPromise
}

function redirectToLogin() {
  authStorage.clear()
  if (window.location.pathname !== '/login') {
    window.location.assign('/login')
  }
}

function isAuthEndpoint(config?: AxiosRequestConfig): boolean {
  const url = config?.url ?? ''
  return url.startsWith('/auth/') || url.startsWith('auth/') || url.includes('/api/auth/')
}

api.interceptors.response.use(
  (res) => res,
  async (error: AxiosError) => {
    const config = error.config as RetriableConfig | undefined
    if (!config || error.response?.status !== 401 || isAuthEndpoint(config) || config._retried) {
      return Promise.reject(error)
    }
    config._retried = true
    try {
      const access = await refreshAccessToken()
      config.headers.set('Authorization', `Bearer ${access}`)
      return api.request(config)
    } catch {
      redirectToLogin()
      return Promise.reject(error)
    }
  },
)

/** Extract a human readable message from an API error. */
export function errorMessage(err: unknown): string {
  if (axios.isAxiosError(err)) {
    const detail = (err.response?.data as { detail?: unknown } | undefined)?.detail
    if (typeof detail === 'string') return detail
    if (detail) return JSON.stringify(detail)
    return err.message
  }
  if (err instanceof Error) return err.message
  return String(err)
}
