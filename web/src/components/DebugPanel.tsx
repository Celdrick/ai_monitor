import { DownloadOutlined, QuestionCircleOutlined } from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, App, Button, InputNumber, Popover, Space, Spin, Table, Tag, Tooltip, Typography } from 'antd'
import { useState } from 'react'
import {
  actionDisabled,
  actionDisabledReason,
  anyTaskBusy,
  clampDuration,
  createDebugTask,
  downloadArtifact,
  DURATION_DEFAULT,
  DURATION_MAX,
  DURATION_MIN,
  getDebugCapabilities,
  isPerfettoFilename,
  listDebugTasks,
  type DebugAction,
  type DebugArtifact,
  type DebugTask,
} from '../api/debug'
import { errorMessage } from '../api/client'
import type { ServiceDetail } from '../api/services'
import { formatDateTime } from '../utils/format'

const TYPE_LABEL: Record<string, string> = {
  profile_start: 'Profile 开始',
  profile_stop: 'Profile 结束',
  pyspy_dump: 'py-spy dump',
  pyspy_record: 'py-spy record',
  nsys: 'nsys',
  msprof: 'msprof',
}

const STATUS_COLOR: Record<string, string> = {
  pending: 'default',
  running: 'processing',
  succeeded: 'green',
  failed: 'red',
  cancelled: 'default',
}

const PERFECTTO_HELP = (
  <div style={{ maxWidth: 320 }}>
    下载到本机后，打开{' '}
    <Typography.Link href="https://ui.perfetto.dev" target="_blank" rel="noreferrer">
      ui.perfetto.dev
    </Typography.Link>{' '}
    选择 Open trace file。公网 Perfetto 无法拉取内网文件。
  </div>
)

function ActionButton({
  kind,
  label,
  caps,
  tasks,
  serviceStatus,
  loading,
  onClick,
}: {
  kind: DebugAction
  label: string
  caps: Parameters<typeof actionDisabled>[1]
  tasks: DebugTask[]
  serviceStatus: string
  loading?: boolean
  onClick: () => void
}) {
  const reason = actionDisabledReason(kind, caps, tasks, serviceStatus)
  const disabled = actionDisabled(kind, caps, tasks, serviceStatus)
  const btn = (
    <Button disabled={disabled} loading={loading} onClick={onClick}>
      {label}
    </Button>
  )
  return reason ? <Tooltip title={reason}>{btn}</Tooltip> : btn
}

function ArtifactCell({ artifacts }: { artifacts: DebugArtifact[] }) {
  const { message } = App.useApp()
  if (!artifacts.length) return <Typography.Text type="secondary">-</Typography.Text>
  return (
    <Space wrap size={4}>
      {artifacts.map((a) => (
        <Space key={a.id} size={4}>
          <Button
            type="link"
            size="small"
            icon={<DownloadOutlined />}
            onClick={async () => {
              try {
                await downloadArtifact(a.id, a.filename)
              } catch (err) {
                message.error(errorMessage(err))
              }
            }}
          >
            {a.filename}
          </Button>
          {isPerfettoFilename(a.filename) ? (
            <Popover content={PERFECTTO_HELP} title="Perfetto 用法">
              <Typography.Link>
                <QuestionCircleOutlined /> Perfetto
              </Typography.Link>
            </Popover>
          ) : null}
        </Space>
      ))}
    </Space>
  )
}

export function DebugPanel({ service }: { service: ServiceDetail }) {
  const { message } = App.useApp()
  const qc = useQueryClient()
  const [duration, setDuration] = useState(DURATION_DEFAULT)

  const caps = useQuery({
    queryKey: ['debug-caps', service.id],
    queryFn: () => getDebugCapabilities(service.id),
  })

  const tasks = useQuery({
    queryKey: ['debug-tasks', service.id],
    queryFn: () => listDebugTasks(service.id),
    refetchInterval: (q) => (anyTaskBusy(q.state.data ?? []) ? 2000 : false),
  })

  const create = useMutation({
    mutationFn: (type: DebugAction) => {
      const needsDuration = type === 'pyspy_record' || type === 'nsys' || type === 'msprof'
      return createDebugTask({
        service_id: service.id,
        type,
        ...(needsDuration ? { duration: clampDuration(duration) } : {}),
      })
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['debug-tasks', service.id] })
    },
    onError: (err) => message.error(errorMessage(err)),
  })

  const list = tasks.data ?? []
  const busy = anyTaskBusy(list) || create.isPending

  const run = (type: DebugAction) => create.mutate(type)

  return (
    <Space direction="vertical" size={16} style={{ display: 'flex' }}>
      {caps.error ? (
        <Alert type="error" showIcon message="无法获取调试能力" description={errorMessage(caps.error)} />
      ) : null}
      {tasks.error ? (
        <Alert type="error" showIcon message="任务列表加载失败" description={errorMessage(tasks.error)} />
      ) : null}

      <Spin spinning={busy}>
        <Space wrap align="center">
          <ActionButton
            kind="profile_start"
            label="Profile 开始"
            caps={caps.data}
            tasks={list}
            serviceStatus={service.status}
            onClick={() => run('profile_start')}
          />
          <ActionButton
            kind="profile_stop"
            label="Profile 结束"
            caps={caps.data}
            tasks={list}
            serviceStatus={service.status}
            onClick={() => run('profile_stop')}
          />
          <ActionButton
            kind="pyspy_dump"
            label="py-spy dump"
            caps={caps.data}
            tasks={list}
            serviceStatus={service.status}
            onClick={() => run('pyspy_dump')}
          />
          <ActionButton
            kind="pyspy_record"
            label="py-spy record"
            caps={caps.data}
            tasks={list}
            serviceStatus={service.status}
            onClick={() => run('pyspy_record')}
          />
          <ActionButton
            kind="nsys"
            label="nsys"
            caps={caps.data}
            tasks={list}
            serviceStatus={service.status}
            onClick={() => run('nsys')}
          />
          <ActionButton
            kind="msprof"
            label="msprof"
            caps={caps.data}
            tasks={list}
            serviceStatus={service.status}
            onClick={() => run('msprof')}
          />
          <Space size={4}>
            <Typography.Text type="secondary">时长</Typography.Text>
            <InputNumber
              min={DURATION_MIN}
              max={DURATION_MAX}
              value={duration}
              onChange={(v) => setDuration(clampDuration(v ?? DURATION_DEFAULT))}
              addonAfter="秒"
              style={{ width: 120 }}
            />
          </Space>
        </Space>
      </Spin>

      <Table<DebugTask>
        size="small"
        rowKey="id"
        loading={tasks.isLoading}
        dataSource={list}
        pagination={{ pageSize: 10, hideOnSinglePage: true }}
        columns={[
          { title: '类型', dataIndex: 'type', render: (t: string) => TYPE_LABEL[t] ?? t },
          {
            title: '状态',
            dataIndex: 'status',
            render: (s: string) => <Tag color={STATUS_COLOR[s] ?? 'default'}>{s}</Tag>,
          },
          { title: '创建时间', dataIndex: 'created_at', render: (v: string) => formatDateTime(v) },
          {
            title: '错误',
            dataIndex: 'error',
            render: (v: string | null) =>
              v ? <Typography.Text type="danger">{v}</Typography.Text> : <Typography.Text type="secondary">-</Typography.Text>,
          },
          {
            title: '产物',
            dataIndex: 'artifacts',
            render: (arts: DebugArtifact[]) => <ArtifactCell artifacts={arts ?? []} />,
          },
        ]}
      />
    </Space>
  )
}
