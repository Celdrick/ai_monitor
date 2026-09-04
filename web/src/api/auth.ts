import { api } from './client'
import type { AuthUser } from './storage'

export interface LoginResponse {
  access_token: string
  refresh_token: string
  token_type: string
  user: AuthUser
}

export async function login(username: string, password: string): Promise<LoginResponse> {
  const res = await api.post<LoginResponse>('/auth/login', { username, password })
  return res.data
}

export async function me(): Promise<AuthUser> {
  const res = await api.get<AuthUser>('/auth/me')
  return res.data
}
