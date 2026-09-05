import { api } from './client'

export type DebugTaskType =
  | 'profile_start'
  | 'profile_stop'
  | 'pyspy_dump'
  | 'pyspy_record'
  | 'nsys'
  | 'msprof'

export type DebugTaskStatus = 'pending' | 'running' | 'succeeded' | 'failed' | 'cancelled'

export interface DebugCapabilities {
  profile: boolean
  pyspy: boolean
  nsys: boolean
  msprof: boolean
}

export interface DebugArtifact {
  id: number
  type: string
  filename: string
  size_bytes: number
  created_at: string
}

export interface DebugTask {
  id: number
  service_id: number
  type: DebugTaskType | string
  status: DebugTaskStatus | string
  error: string | null
  created_at: string
  finished_at: string | null
  artifacts: DebugArtifact[]
}

export interface CreateDebugTask {
  service_id: number
  type: DebugTaskType
  duration?: number
}

export const DURATION_MIN = 5
export const DURATION_MAX = 60
export const DURATION_DEFAULT = 15

export function isValidDuration(n: number): boolean {
  return Number.isInteger(n) && n >= DURATION_MIN && n <= DURATION_MAX
}

export function clampDuration(n: number): number {
  if (!Number.isFinite(n)) return DURATION_DEFAULT
  return Math.min(DURATION_MAX, Math.max(DURATION_MIN, Math.round(n)))
}

export function isActiveStatus(status: string): boolean {
  return status === 'pending' || status === 'running'
}

export function anyTaskBusy(tasks: { status: string }[]): boolean {
  return tasks.some((t) => isActiveStatus(t.status))
}

/** Newest-first: a start that has not been followed by a successful stop. */
export function profileSessionOpen(tasks: { type: string; status: string }[]): boolean {
  const last = tasks.find((t) => t.type === 'profile_start' || t.type === 'profile_stop')
  if (!last) return false
  if (last.type === 'profile_stop') {
    return last.status !== 'succeeded' && last.status !== 'failed' && last.status !== 'cancelled'
  }
  return last.status !== 'failed' && last.status !== 'cancelled'
}

export function serviceAllowsDebug(status: string): boolean {
  return status === 'running' || status === 'degraded'
}

export type DebugAction = DebugTaskType

const TOOL_HINT: Record<string, string> = {
  profile: '当前 Agent 不支持 torch profiler',
  pyspy: '未检测到 py-spy，请在 Agent 主机安装后重试',
  nsys: '未检测到 nsys，请在宿主机安装 NVIDIA Nsight Systems',
  msprof: '未检测到 msprof，请在宿主机安装昇腾 MindStudio',
}

function toolOf(kind: DebugAction): keyof DebugCapabilities {
  if (kind === 'pyspy_dump' || kind === 'pyspy_record') return 'pyspy'
  if (kind === 'nsys') return 'nsys'
  if (kind === 'msprof') return 'msprof'
  return 'profile'
}

export function actionDisabled(
  kind: DebugAction,
  caps: DebugCapabilities | undefined,
  tasks: { type: string; status: string }[],
  serviceStatus?: string,
): boolean {
  return actionDisabledReason(kind, caps, tasks, serviceStatus) !== undefined
}

export function actionDisabledReason(
  kind: DebugAction,
  caps: DebugCapabilities | undefined,
  tasks: { type: string; status: string }[],
  serviceStatus?: string,
): string | undefined {
  if (serviceStatus !== undefined && !serviceAllowsDebug(serviceStatus)) {
    return '服务未在运行，无法采样'
  }
  if (!caps) return '正在查询调试能力'
  const tool = toolOf(kind)
  if (!caps[tool]) return TOOL_HINT[tool]
  if (anyTaskBusy(tasks)) return '已有采样任务进行中'
  const session = profileSessionOpen(tasks)
  if (kind === 'profile_start' && session) return '请先结束当前 Profile'
  if (kind === 'profile_stop' && !session) return '没有进行中的 Profile'
  return undefined
}

export function isPerfettoFilename(filename: string): boolean {
  const lower = filename.toLowerCase()
  return lower.endsWith('.json.gz') || lower.endsWith('.json') || lower.endsWith('.pt.trace.json')
}

export async function getDebugCapabilities(serviceId: number): Promise<DebugCapabilities> {
  const res = await api.get<DebugCapabilities>('/debug/capabilities', { params: { service_id: serviceId } })
  return res.data
}

export async function listDebugTasks(serviceId: number): Promise<DebugTask[]> {
  const res = await api.get<DebugTask[]>('/debug/tasks', { params: { service_id: serviceId } })
  return res.data
}

export async function createDebugTask(body: CreateDebugTask): Promise<DebugTask> {
  const res = await api.post<DebugTask>('/debug/tasks', body)
  return res.data
}

export async function downloadArtifact(id: number, filename: string): Promise<void> {
  const res = await api.get<Blob>(`/debug/artifacts/${id}`, { responseType: 'blob' })
  const url = URL.createObjectURL(res.data)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}
