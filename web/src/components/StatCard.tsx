import { Card, Skeleton, Statistic } from 'antd'
import type { ReactNode } from 'react'

export interface StatCardProps {
  title: ReactNode
  value: ReactNode
  suffix?: ReactNode
  loading?: boolean
}

export function StatCard({ title, value, suffix, loading }: StatCardProps) {
  return (
    <Card size="small" style={{ height: '100%' }}>
      {loading ? (
        <Skeleton active paragraph={{ rows: 1 }} title={{ width: '60%' }} />
      ) : (
        <Statistic title={title} value={value as string | number} suffix={suffix} />
      )}
    </Card>
  )
}

export default StatCard
