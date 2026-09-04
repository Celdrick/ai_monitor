import { ApiOutlined, ClusterOutlined, DashboardOutlined, LogoutOutlined, UserOutlined } from '@ant-design/icons'
import { Button, Layout, Menu, Space, Typography, theme } from 'antd'
import { useMemo } from 'react'
import { Link, Outlet, useLocation, useNavigate } from 'react-router-dom'
import { useAuth } from '../auth/AuthContext'
import { TimeRangeSelector } from './TimeRangeSelector'

const { Sider, Header, Content } = Layout

const MENU_ITEMS = [
  { key: '/', icon: <DashboardOutlined />, label: <Link to="/">总览</Link> },
  { key: '/hosts', icon: <ClusterOutlined />, label: <Link to="/hosts">机器</Link> },
  { key: '/services', icon: <ApiOutlined />, label: <Link to="/services">服务</Link> },
]

export function AppLayout() {
  const { user, logout } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const { token } = theme.useToken()

  const selectedKey = useMemo(() => {
    if (location.pathname.startsWith('/hosts')) return '/hosts'
    if (location.pathname.startsWith('/services')) return '/services'
    return '/'
  }, [location.pathname])

  const onLogout = () => {
    logout()
    navigate('/login', { replace: true })
  }

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Sider breakpoint="lg" collapsedWidth={64} theme="dark">
        <div
          style={{
            height: 48,
            margin: 8,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            color: '#fff',
            fontWeight: 600,
            fontSize: 16,
            whiteSpace: 'nowrap',
            overflow: 'hidden',
          }}
        >
          AI Monitor
        </div>
        <Menu theme="dark" mode="inline" selectedKeys={[selectedKey]} items={MENU_ITEMS} />
      </Sider>
      <Layout>
        <Header
          style={{
            background: token.colorBgContainer,
            padding: '0 16px',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            gap: 16,
            borderBottom: `1px solid ${token.colorBorderSecondary}`,
          }}
        >
          <TimeRangeSelector />
          <Space>
            <Typography.Text>
              <UserOutlined /> {user?.username}
              {user?.role ? <Typography.Text type="secondary"> ({user.role})</Typography.Text> : null}
            </Typography.Text>
            <Button type="text" icon={<LogoutOutlined />} onClick={onLogout}>
              退出
            </Button>
          </Space>
        </Header>
        <Content style={{ padding: 16 }}>
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  )
}

export default AppLayout
