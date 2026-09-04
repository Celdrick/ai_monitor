import { Card, Input } from 'antd'
import { useMemo, useState } from 'react'
import { AgentsTable } from '../components/AgentsTable'
import { useAgents } from '../hooks/useMetrics'

export function HostsPage() {
  const agents = useAgents()
  const [search, setSearch] = useState('')

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase()
    if (!q) return agents.data
    return agents.data?.filter(
      (a) => a.host.toLowerCase().includes(q) || (a.ip ?? '').toLowerCase().includes(q),
    )
  }, [agents.data, search])

  return (
    <Card
      title="机器"
      size="small"
      extra={
        <Input.Search
          allowClear
          placeholder="搜索主机名 / IP"
          style={{ width: 260 }}
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      }
    >
      <AgentsTable data={filtered} loading={agents.isLoading} />
    </Card>
  )
}

export default HostsPage
