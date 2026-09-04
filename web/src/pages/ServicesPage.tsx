import { Alert, Card, Input, Space, Switch, Typography } from 'antd'
import { useMemo, useState } from 'react'
import { errorMessage } from '../api/client'
import { sortServices } from '../api/services'
import { ServicesTable } from '../components/ServicesTable'
import { useServices } from '../hooks/useServices'

export function ServicesPage() {
  const services = useServices('all')
  const [search, setSearch] = useState('')
  const [hideStopped, setHideStopped] = useState(false)

  const filtered = useMemo(() => {
    if (!services.data) return undefined
    const q = search.trim().toLowerCase()
    let list = services.data
    if (hideStopped) list = list.filter((s) => s.status !== 'stopped')
    if (q) {
      list = list.filter(
        (s) =>
          s.name.toLowerCase().includes(q) ||
          s.host.toLowerCase().includes(q) ||
          (s.model ?? '').toLowerCase().includes(q),
      )
    }
    return sortServices(list)
  }, [services.data, search, hideStopped])

  const total = services.data?.length ?? 0
  const running = services.data?.filter((s) => s.status === 'running').length ?? 0

  return (
    <Card
      title={
        <Space>
          <span>服务</span>
          {services.data ? (
            <Typography.Text type="secondary" style={{ fontWeight: 400 }}>
              运行中 {running} / 共 {total}
            </Typography.Text>
          ) : null}
        </Space>
      }
      size="small"
      extra={
        <Space wrap>
          <Space size={4}>
            <Switch size="small" checked={hideStopped} onChange={setHideStopped} />
            <Typography.Text>隐藏已停止</Typography.Text>
          </Space>
          <Input.Search
            allowClear
            placeholder="搜索服务名 / 机器 / 模型"
            style={{ width: 280 }}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </Space>
      }
    >
      {services.error ? (
        <Alert type="error" showIcon message="服务列表查询失败" description={errorMessage(services.error)} style={{ marginBottom: 12 }} />
      ) : null}
      <ServicesTable data={filtered} loading={services.isLoading} />
    </Card>
  )
}

export default ServicesPage
