# 第一期「基础监控」实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付可部署的最小闭环：Agent 采集 GPU/NPU/主机指标并心跳注册，server 提供认证、机器注册表、VM 抓取目标与受限 PromQL 代理，web 提供登录、集群总览、机器详情，compose 一键起全栈并用 `--fake` Agent 端到端验证。

**Architecture:** 指标 Pull（VictoriaMetrics 通过 file_sd scrape Agent `:9400/metrics`），Agent 定时向 server 心跳注册（Bearer agent token），server 据心跳生成 `file_sd/agents.json`；web 只经 server API 访问数据，PromQL 由后端模板生成，前端仅传模板名与参数。

**Tech Stack:** Python 3.11+、FastAPI、prometheus-client、psutil、nvidia-ml-py、httpx、SQLAlchemy 2（async）+ Alembic + PostgreSQL（测试用 SQLite）、PyJWT + bcrypt；React 18 + TypeScript + Vite + Ant Design 5 + ECharts 5 + TanStack Query；VictoriaMetrics 单机版、Loki、docker-compose。

## Global Constraints

- 统一硬件指标名与标签严格按 spec 第 6 节：`accel_util_percent`、`accel_mem_used_bytes`、`accel_mem_total_bytes`、`accel_temp_celsius`、`accel_power_watts`、`accel_ecc_errors_total`（仅 NVIDIA）、`accel_health`（Ascend 健康码，NVIDIA 恒为 0）、`accel_process_mem_bytes{pid}`；标签 `vendor, host, index, model`。
- 主机指标：`host_cpu_percent`、`host_mem_used_bytes`、`host_mem_total_bytes`、`host_disk_used_bytes{mount}`、`host_disk_total_bytes{mount}`、`host_net_rx_bytes_total{iface}`、`host_net_tx_bytes_total{iface}`；Agent 自身 `agent_collector_up{collector}`。
- Agent 默认监听 `0.0.0.0:9400`；采集间隔 15s；心跳 30s；机器离线判定：心跳超 120s。
- VM 保留期 `-retentionPeriod=6`（6 个月）。
- JWT access 1h、refresh 7d；密码 bcrypt；角色 `admin` / `viewer`。
- Agent token 明文只在创建时返回一次，server 存 sha256 哈希。
- 前端不接受任意 PromQL，只能传模板名 + 白名单参数（`host`、`index`）。
- 任一采集器初始化失败只记录一次警告，不影响其他采集器。
- 所有 Python 包使用 `pyproject.toml`，测试用 pytest；前端用 Vitest。
- 每个任务结束提交一次。

---

## 文件结构

```
agent/
  pyproject.toml
  Dockerfile
  ai_monitor_agent/
    __init__.py            # __version__
    __main__.py            # CLI 入口
    config.py              # AgentConfig（YAML 加载）
    collectors/
      __init__.py
      base.py              # DeviceSample / ProcessMem / HostSample / Collector 协议
      nvidia.py            # NvidiaCollector（pynvml）
      ascend.py            # AscendCollector + parse_npu_smi_info
      host.py              # HostCollector（psutil）
      fake.py              # FakeCollector
    metrics.py             # AgentMetricsCollector → prometheus registry
    heartbeat.py           # HeartbeatClient
    app.py                 # create_app：/metrics /healthz + lifespan 心跳
  tests/
    fixtures/npu_smi_info_910b.txt
    fixtures/npu_smi_info_310p.txt
    test_ascend_parser.py
    test_metrics.py
    test_heartbeat.py
    test_app.py
server/
  pyproject.toml
  Dockerfile
  alembic.ini
  alembic/env.py
  alembic/versions/0001_initial.py
  ai_monitor_server/
    __init__.py
    config.py              # Settings（pydantic-settings）
    db.py                  # engine / session / Base
    models.py              # User / Agent
    main.py                # create_app + startup（建管理员）
    auth/
      security.py          # hash_password / verify_password / create_token / decode_token
      deps.py              # get_current_user / require_admin
      router.py            # /api/auth/*
    agents/
      schemas.py
      tokens.py            # generate_agent_token / hash_agent_token
      file_sd.py           # write_file_sd(agents, path)
      router.py            # /api/agents/*
    metrics/
      templates.py         # TEMPLATES 字典 + render(template, params)
      vm_client.py         # VMClient.query / query_range
      router.py            # /api/metrics/*
  tests/
    conftest.py            # SQLite 内存 + TestClient + admin 用户
    test_auth.py
    test_agents.py
    test_file_sd.py
    test_metrics_templates.py
    test_metrics_router.py
web/
  package.json, vite.config.ts, tsconfig.json, index.html, Dockerfile, nginx.conf
  src/
    main.tsx, App.tsx
    api/client.ts, api/auth.ts, api/agents.ts, api/metrics.ts
    auth/AuthContext.tsx, auth/RequireAuth.tsx
    time/TimeRangeContext.tsx, time/timeRange.ts
    components/AppLayout.tsx, StatCard.tsx, TimeRangeSelector.tsx, TimeSeriesChart.tsx
    pages/LoginPage.tsx, OverviewPage.tsx, HostDetailPage.tsx, HostsPage.tsx
    time/timeRange.test.ts
deploy/
  docker-compose.yml
  .env.example
  vm/scrape.yml
  loki/config.yml
  agent/ai-monitor-agent.service
  agent/agent.example.yaml
  install-agent.sh
README.md
```

---

### Task 1: Agent 包骨架、配置与采集器接口

**Files:**
- Create: `agent/pyproject.toml`, `agent/ai_monitor_agent/__init__.py`, `agent/ai_monitor_agent/config.py`, `agent/ai_monitor_agent/collectors/__init__.py`, `agent/ai_monitor_agent/collectors/base.py`
- Test: `agent/tests/test_config.py`

**Interfaces（Produces）:**

```python
# collectors/base.py
@dataclass
class ProcessMem: pid: int; mem_bytes: int
@dataclass
class DeviceSample:
    vendor: str; index: int; model: str
    util_percent: float; mem_used_bytes: int; mem_total_bytes: int
    temp_celsius: float; power_watts: float
    ecc_errors_total: int | None = None   # NVIDIA only
    health: int = 0                        # Ascend 健康码，0=OK
    processes: list[ProcessMem] = field(default_factory=list)
@dataclass
class HostSample:
    cpu_percent: float; mem_used_bytes: int; mem_total_bytes: int
    disks: dict[str, tuple[int, int]]      # mount -> (used, total)
    net: dict[str, tuple[int, int]]        # iface -> (rx_total, tx_total)
class DeviceCollector(Protocol):
    name: str
    def available(self) -> bool: ...
    def collect(self) -> list[DeviceSample]: ...

# config.py
class ManualService(BaseModel): name: str; port: int; log_path: str | None = None; profiler_dir: str | None = None
class AgentConfig(BaseModel):
    server_url: str; agent_token: str
    loki_url: str | None = None
    listen: str = "0.0.0.0:9400"
    advertise_address: str | None = None   # "ip:port"，缺省自动探测
    scrape_interval_seconds: int = 15
    heartbeat_interval_seconds: int = 30
    services: list[ManualService] = []
    artifacts_dir: str = "/var/lib/ai-monitor-agent/artifacts"
def load_config(path: str) -> AgentConfig
```

- [ ] Step 1: 写 `agent/pyproject.toml`（依赖 fastapi, uvicorn, prometheus-client, psutil, httpx, pyyaml, pydantic>=2, nvidia-ml-py；dev: pytest, pytest-asyncio, respx；脚本 `ai-monitor-agent = ai_monitor_agent.__main__:main`）
- [ ] Step 2: 写失败测试 `tests/test_config.py`：加载临时 YAML，断言默认值与 `services` 解析
- [ ] Step 3: 实现 `config.py`、`collectors/base.py`
- [ ] Step 4: `cd agent && pip install -e .[dev] && pytest -q` 通过
- [ ] Step 5: Commit `feat(agent): package skeleton, config and collector interfaces`

### Task 2: Ascend 采集器与 npu-smi 解析

**Files:**
- Create: `agent/ai_monitor_agent/collectors/ascend.py`, `agent/tests/fixtures/npu_smi_info_910b.txt`, `agent/tests/fixtures/npu_smi_info_310p.txt`
- Test: `agent/tests/test_ascend_parser.py`

**Interfaces:**
- Produces: `parse_npu_smi_info(text: str) -> list[DeviceSample]`；`class AscendCollector(DeviceCollector)`，`name="ascend"`，`available()` 检测 `shutil.which("npu-smi")`，`collect()` 执行 `npu-smi info` 并解析。

解析规则：设备块为两行，第一行 `| <npu> <Name> | <Health> | <Power> <Temp> <Hugepages> |`，第二行 `| <chip> | <Bus-Id> | <AICore%> <Mem used / total> <HBM used / total> |`。HBM total > 0 用 HBM，否则用 Memory-Usage。健康码映射 `OK=0, Warning=1, Alarm=2, Critical=3`，其他=4。进程表 `| <npu> <chip> | <pid> | <name> | <mem MB> |` 关联到对应 NPU。

- [ ] Step 1: 写 fixtures（910B：2 卡 HBM 32768MB、1 个进程；310P：1 卡 HBM 0/0，走 Memory）
- [ ] Step 2: 写失败测试：断言设备数、vendor="ascend"、mem_total_bytes、util、temp、power、health、processes
- [ ] Step 3: 实现解析器与采集器
- [ ] Step 4: `pytest tests/test_ascend_parser.py -v` 通过
- [ ] Step 5: Commit `feat(agent): ascend collector with npu-smi parser`

### Task 3: NVIDIA、主机、Fake 采集器

**Files:**
- Create: `collectors/nvidia.py`, `collectors/host.py`, `collectors/fake.py`
- Test: `agent/tests/test_fake_collector.py`, `agent/tests/test_host_collector.py`

**Interfaces:**
- `NvidiaCollector(name="nvidia")`：`available()` 尝试 `import pynvml; nvmlInit()`，失败返回 False 并记录一次警告；`collect()` 读 util、memory、temperature、power、ECC（volatile total，不支持则 None）、`nvmlDeviceGetComputeRunningProcesses`。
- `HostCollector.collect() -> HostSample`（psutil；磁盘只取物理分区 `psutil.disk_partitions(all=False)`；网络 `psutil.net_io_counters(pernic=True)` 排除 `lo`）。
- `FakeCollector(vendor: str, count: int, seed: int | None = None)`：`available()=True`，`collect()` 返回 `count` 个随机样本（util 0-100，mem_total 80GB nvidia / 64GB ascend，温度 30-80，功耗 50-400），每卡附 1 个假进程。

- [ ] Step 1: 写测试：Fake 返回 count 个样本且 vendor 正确、值域合法；Host 返回 mem_total>0
- [ ] Step 2: 实现三者
- [ ] Step 3: 测试通过
- [ ] Step 4: Commit `feat(agent): nvidia, host and fake collectors`

### Task 4: Prometheus 指标导出

**Files:**
- Create: `agent/ai_monitor_agent/metrics.py`
- Test: `agent/tests/test_metrics.py`

**Interfaces:**
- `class AgentMetricsCollector:` 构造 `(host: str, device_collectors: list[DeviceCollector], host_collector: HostCollector | None)`；实现 prometheus_client 自定义 collector `collect()`，每次 scrape 调用各采集器；采集器异常 → `agent_collector_up{collector}=0`，其余照常。
- `build_registry(collector) -> CollectorRegistry`；`render(registry) -> bytes` 用 `generate_latest`。

- [ ] Step 1: 写测试：用 FakeCollector(nvidia,2)+FakeCollector(ascend,1)+一个 `collect()` 抛异常的采集器，渲染文本包含 `accel_util_percent{host="h1",index="0",model=...,vendor="nvidia"}`、`accel_ecc_errors_total` 只出现于 nvidia、`accel_health` 两者都有、`agent_collector_up{collector="broken"} 0`
- [ ] Step 2: 实现
- [ ] Step 3: 测试通过
- [ ] Step 4: Commit `feat(agent): prometheus metrics exporter`

### Task 5: 心跳客户端、HTTP 应用与 CLI

**Files:**
- Create: `heartbeat.py`, `app.py`, `__main__.py`, `agent/Dockerfile`, `deploy/agent/ai-monitor-agent.service`, `deploy/agent/agent.example.yaml`
- Test: `agent/tests/test_heartbeat.py`, `agent/tests/test_app.py`

**Interfaces:**
- 心跳请求体（server Task 8 消费）：

```json
{"host": "gpu-01", "ip": "10.0.0.11", "listen_port": 9400, "advertise_address": "10.0.0.11:9400",
 "agent_version": "0.1.0", "hardware_vendor": "nvidia|ascend|none", "device_count": 8, "services": []}
```
  Header `Authorization: Bearer <agent_token>`。`POST {server_url}/api/agents/heartbeat`，期望 200/204；失败记录警告并继续。
- `class HeartbeatClient: __init__(config, host, inventory: Callable[[], dict]); async run(stop: asyncio.Event)`；`detect_ip() -> str`（UDP connect 探测，失败回退 `127.0.0.1`）。
- `create_app(config, device_collectors, host_collector) -> FastAPI`：`GET /metrics`（text/plain; version=0.0.4）、`GET /healthz` → `{"status":"ok","collectors":{...}}`；lifespan 启停心跳任务。
- CLI：`ai-monitor-agent --config agent.yaml [--fake N] [--fake-vendor nvidia|ascend] [--host NAME]`；`--fake` 时不加载真实硬件采集器；无 `--config` 且 `--fake` 时允许仅用环境变量 `AI_MONITOR_SERVER_URL` / `AI_MONITOR_AGENT_TOKEN`。

- [ ] Step 1: 测试心跳：respx 模拟 server，断言请求体字段与 Authorization 头；server 500 时不抛异常
- [ ] Step 2: 测试 app：TestClient `GET /metrics` 200 且含 `accel_util_percent`，`/healthz` 200
- [ ] Step 3: 实现 heartbeat / app / CLI / Dockerfile（`python:3.12-slim`，`pip install .`，`CMD ["ai-monitor-agent","--config","/etc/ai-monitor/agent.yaml"]`）/ systemd unit / example yaml
- [ ] Step 4: 全部测试通过；本地 `ai-monitor-agent --fake 2 --host demo` 启动后 `curl :9400/metrics` 有数据
- [ ] Step 5: Commit `feat(agent): heartbeat client, http app and cli`

### Task 6: Server 骨架、配置、数据库与模型

**Files:**
- Create: `server/pyproject.toml`, `ai_monitor_server/__init__.py`, `config.py`, `db.py`, `models.py`, `main.py`, `alembic.ini`, `alembic/env.py`, `alembic/versions/0001_initial.py`, `tests/conftest.py`
- Test: `server/tests/test_health.py`

**Interfaces:**
- `Settings`: `database_url` (默认 `sqlite+aiosqlite:///./dev.db`)、`jwt_secret`、`access_token_minutes=60`、`refresh_token_days=7`、`admin_username="admin"`、`admin_password`、`vm_url="http://victoriametrics:8428"`、`loki_url`、`file_sd_path="/file_sd/agents.json"`、`agent_offline_seconds=120`、`cors_origins`。环境变量前缀 `AIM_`。
- `models.User(id, username unique, password_hash, role, created_at)`；`models.Agent(id, host unique, ip, advertise_address, listen_port, token_hash unique, version, hardware_vendor, device_count, last_seen_at nullable, created_at)`。
- `main.create_app() -> FastAPI`；startup：`create_all`（开发/测试）+ 若无用户则创建 admin。`GET /api/health` → `{"status":"ok"}`。
- conftest 提供 `app`、`client`（httpx AsyncClient/ASGITransport）、`admin_headers`。

- [ ] Step 1: 写 `test_health.py`
- [ ] Step 2: 实现骨架；Alembic 初始迁移与模型一致
- [ ] Step 3: `pytest -q` 通过；`alembic upgrade head` 在 SQLite 上成功
- [ ] Step 4: Commit `feat(server): skeleton, settings, db models and alembic`

### Task 7: 认证

**Files:**
- Create: `auth/security.py`, `auth/deps.py`, `auth/router.py`
- Test: `tests/test_auth.py`

**Interfaces:**
- `hash_password(p) -> str`、`verify_password(p, h) -> bool`（bcrypt）；`create_access_token(user_id, role)`、`create_refresh_token(user_id)`、`decode_token(token) -> dict`（PyJWT HS256，`typ` 字段区分 access/refresh）。
- `POST /api/auth/login {username,password}` → `{access_token, refresh_token, token_type:"bearer", user:{id,username,role}}`；`POST /api/auth/refresh {refresh_token}` → 新 access；`GET /api/auth/me`。
- `get_current_user` 依赖；`require_admin` 依赖（403）。

- [ ] Step 1: 测试：正确登录 200、错误密码 401、me 需要 token、refresh 返回新 access、viewer 访问 admin 接口 403
- [ ] Step 2: 实现
- [ ] Step 3: 通过；Commit `feat(server): jwt auth with roles`

### Task 8: 机器注册表、Agent token 与 file_sd

**Files:**
- Create: `agents/schemas.py`, `agents/tokens.py`, `agents/file_sd.py`, `agents/router.py`
- Test: `tests/test_agents.py`, `tests/test_file_sd.py`

**Interfaces:**
- `generate_agent_token() -> str`（`secrets.token_urlsafe(32)`）；`hash_agent_token(t) -> str`（sha256 hex）。
- `POST /api/agents` (admin) `{host}` → 201 `{id, host, token}`（token 仅此一次）；重复 host 409。
- `POST /api/agents/{id}/rotate-token` (admin) → 新 token。
- `POST /api/agents/heartbeat`（Bearer agent token，非 JWT）：更新 ip/advertise_address/listen_port/version/vendor/device_count/last_seen_at；token 无效 401；成功 204，随后调用 `write_file_sd`。
- `GET /api/agents`（登录用户）→ `[{id, host, ip, advertise_address, version, hardware_vendor, device_count, last_seen_at, status:"online"|"offline"|"never"}]`；`GET /api/agents/{id}`。
- `write_file_sd(agents: Iterable[Agent], path: str)`：写 `[{"targets":["<advertise_address>"],"labels":{"host":"<host>"}}]`，只含曾心跳过的 agent；原子写（临时文件 + rename）。

- [ ] Step 1: 测试：创建 → 心跳（正确/错误 token）→ 列表 status=online；将 last_seen_at 改为 5 分钟前 → offline；file_sd 内容正确
- [ ] Step 2: 实现
- [ ] Step 3: 通过；Commit `feat(server): agent registry, tokens, heartbeat and file_sd`

### Task 9: 指标模板与 VM 代理

**Files:**
- Create: `metrics/templates.py`, `metrics/vm_client.py`, `metrics/router.py`
- Test: `tests/test_metrics_templates.py`, `tests/test_metrics_router.py`

**Interfaces:**
- `TEMPLATES: dict[str, Template]`，`Template(expr: str, params: tuple[str, ...])`；`render(name, params: dict) -> str`：未知模板 → `KeyError`；缺参/非法字符（仅允许 `[A-Za-z0-9_.-]`）→ `ValueError`。
- 模板清单：

| name | params | expr |
| --- | --- | --- |
| cluster_device_count | - | `count(accel_mem_total_bytes)` |
| cluster_avg_util | - | `avg(accel_util_percent)` |
| cluster_mem_used | - | `sum(accel_mem_used_bytes)` |
| cluster_mem_total | - | `sum(accel_mem_total_bytes)` |
| cluster_util_by_host | - | `avg by (host) (accel_util_percent)` |
| host_devices | host | `accel_mem_total_bytes{host="$host"}` |
| host_device_util | host | `accel_util_percent{host="$host"}` |
| host_device_mem_used | host | `accel_mem_used_bytes{host="$host"}` |
| host_device_mem_total | host | `accel_mem_total_bytes{host="$host"}` |
| host_device_temp | host | `accel_temp_celsius{host="$host"}` |
| host_device_power | host | `accel_power_watts{host="$host"}` |
| host_device_process_mem | host | `accel_process_mem_bytes{host="$host"}` |
| host_cpu | host | `host_cpu_percent{host="$host"}` |
| host_mem_used | host | `host_mem_used_bytes{host="$host"}` |
| host_mem_total | host | `host_mem_total_bytes{host="$host"}` |
| host_disk_used | host | `host_disk_used_bytes{host="$host"}` |
| host_disk_total | host | `host_disk_total_bytes{host="$host"}` |

- `VMClient(base_url)`: `async query(expr, time=None) -> dict`、`async query_range(expr, start, end, step) -> dict`，透传 VM 的 `data` 字段；VM 不可达/非 2xx → `VMError`。
- `GET /api/metrics/query?template=&host=`、`GET /api/metrics/query_range?template=&host=&start=&end=&step=`（start/end 为 unix 秒，step 如 `15s`）；返回 VM `data`；模板错误 400；VM 错误 502 `{detail}`。需登录。

- [ ] Step 1: 模板测试（渲染、未知、非法字符）；路由测试用 respx 模拟 VM 返回并断言透传与 400/502
- [ ] Step 2: 实现
- [ ] Step 3: 通过；Commit `feat(server): templated promql proxy to victoriametrics`

### Task 10: Server Dockerfile 与启动脚本

**Files:**
- Create: `server/Dockerfile`, `server/entrypoint.sh`

- [ ] Step 1: Dockerfile `python:3.12-slim`，安装包，`entrypoint.sh` 先 `alembic upgrade head` 再 `uvicorn ai_monitor_server.main:app --host 0.0.0.0 --port 8000`
- [ ] Step 2: `docker build -t ai-monitor-server server/` 成功
- [ ] Step 3: Commit `build(server): dockerfile and entrypoint`

### Task 11: Web 脚手架、API 客户端与认证

**Files:**
- Create: `web/` Vite React TS 项目；`src/api/client.ts`（axios，baseURL `/api`，请求注入 access token，401 时用 refresh 换新后重放一次，失败则清空并跳登录）；`src/api/auth.ts`、`agents.ts`、`metrics.ts`；`src/auth/AuthContext.tsx`（localStorage 持久化 tokens 与 user）、`RequireAuth.tsx`；`pages/LoginPage.tsx`；`components/AppLayout.tsx`（侧边菜单：总览、机器；右上用户与退出）。

**Interfaces（TS 类型）:**

```ts
type AgentStatus = 'online' | 'offline' | 'never'
interface AgentInfo { id: number; host: string; ip: string | null; advertise_address: string | null; version: string | null; hardware_vendor: string | null; device_count: number; last_seen_at: string | null; status: AgentStatus }
interface RangeResult { resultType: 'matrix'; result: { metric: Record<string,string>; values: [number, string][] }[] }
interface InstantResult { resultType: 'vector'; result: { metric: Record<string,string>; value: [number, string] }[] }
queryInstant(template: string, params?: Record<string,string>): Promise<InstantResult>
queryRange(template: string, params: Record<string,string>, start: number, end: number, step: string): Promise<RangeResult>
```

- [ ] Step 1: `npm create vite@latest web -- --template react-ts`，安装 antd、@ant-design/icons、echarts、echarts-for-react、@tanstack/react-query、react-router-dom、axios、dayjs；dev: vitest、@testing-library/react、jsdom
- [ ] Step 2: 实现上述文件，`vite.config.ts` 开发代理 `/api → http://localhost:8000`
- [ ] Step 3: `npm run build` 通过
- [ ] Step 4: Commit `feat(web): scaffold, api client, auth and layout`

### Task 12: 时间范围与图表组件

**Files:**
- Create: `src/time/timeRange.ts`, `src/time/TimeRangeContext.tsx`, `components/TimeRangeSelector.tsx`, `components/TimeSeriesChart.tsx`, `components/StatCard.tsx`
- Test: `src/time/timeRange.test.ts`

**Interfaces:**
- `type Preset = '1h'|'6h'|'24h'|'7d'|'30d'`；`interface TimeRange { start: number; end: number }`（unix 秒）；`presetToRange(p, now=Date.now())`；`stepForRange(r): string`：目标约 300 个点，`Math.max(15, ceil((end-start)/300))` 秒，向 15 的倍数取整，返回如 `"15s"`。
- `TimeRangeProvider`/`useTimeRange()` → `{range, preset, setPreset, setCustom, refreshKey, refresh()}`。
- `TimeSeriesChart({ data: RangeResult | undefined, seriesName: (metric) => string, unit?: 'percent'|'bytes'|'celsius'|'watts', loading?, error? })`：ECharts 折线，bytes 用 GiB 格式化；error 显示 antd `Alert`。
- `StatCard({title, value, suffix?, loading?})`。

- [ ] Step 1: 写 `timeRange.test.ts`：`stepForRange({start:0,end:3600})==='15s'`；`stepForRange({start:0,end:604800})==='2025s'`（604800/300=2016，向上取到 15 的倍数 2025）；`presetToRange('1h', 1_000_000_000_000)` 返回 `{start: 999996400, end: 1000000000}`
- [ ] Step 2: 实现
- [ ] Step 3: `npx vitest run` 通过；Commit `feat(web): time range and chart components`

### Task 13: 总览、机器列表与机器详情页

**Files:**
- Create: `pages/OverviewPage.tsx`, `pages/HostsPage.tsx`, `pages/HostDetailPage.tsx`；修改 `App.tsx` 路由 `/login`、`/`（总览）、`/hosts`、`/hosts/:host`

内容：
- 总览：StatCard ×4（在线机器/总机器 来自 `/api/agents`；卡总数 `cluster_device_count`；平均利用率 `cluster_avg_util`；显存 `cluster_mem_used/total`）；`cluster_util_by_host` 折线；机器表格（host、vendor、卡数、版本、status Tag、最后心跳），行点击进详情。Query 每 15s 刷新。
- 机器列表：同表格 + 搜索框。
- 机器详情：顶部基本信息；卡列表表格（来自 `host_devices` 即时查询，显示 index、model、当前显存）；4 个折线图（利用率、显存、温度、功耗，series 按 `index`）；进程表（`host_device_process_mem` 即时查询：index、pid、显存）；主机 CPU/内存/磁盘折线。

- [ ] Step 1: 实现三页与路由
- [ ] Step 2: `npm run build` 与 `vitest` 通过；对接本地 server + fake agent 手动验证图表有数据
- [ ] Step 3: Commit `feat(web): overview, hosts and host detail pages`

### Task 14: Web Dockerfile 与 nginx

**Files:**
- Create: `web/Dockerfile`（node:20 构建 → nginx:alpine），`web/nginx.conf`（`/` SPA fallback；`/api/` 反代 `http://server:8000`；`/api/logs/tail` 预留 WebSocket 头）

- [ ] Step 1: 实现；`docker build -t ai-monitor-web web/` 成功
- [ ] Step 2: Commit `build(web): dockerfile and nginx`

### Task 15: 部署编排与安装脚本

**Files:**
- Create: `deploy/docker-compose.yml`, `deploy/.env.example`, `deploy/vm/scrape.yml`, `deploy/loki/config.yml`, `deploy/install-agent.sh`; Modify: `README.md`

compose 服务：
- `victoriametrics`: `victoriametrics/victoria-metrics:v1.102.0`，`-retentionPeriod=6 -promscrape.config=/etc/vm/scrape.yml -storageDataPath=/storage`，卷 `vm-data`、`file_sd`（只读）、`./vm/scrape.yml`。
- `loki`: `grafana/loki:3.1.0`，`./loki/config.yml`，卷 `loki-data`（本期仅起服务）。
- `postgres`: `postgres:16-alpine`，卷 `pg-data`，环境来自 `.env`。
- `server`: build `../server`，环境 `AIM_DATABASE_URL=postgresql+asyncpg://...`、`AIM_JWT_SECRET`、`AIM_ADMIN_PASSWORD`、`AIM_VM_URL=http://victoriametrics:8428`、`AIM_FILE_SD_PATH=/file_sd/agents.json`，卷 `file_sd`，`depends_on` postgres。
- `web`: build `../web`，端口 `${WEB_PORT:-8080}:80`。
- profile `fake`：`agent-fake-nvidia`（build `../agent`，command `ai-monitor-agent --fake 4 --fake-vendor nvidia --host fake-gpu-01`，环境 `AI_MONITOR_SERVER_URL=http://server:8000`、`AI_MONITOR_AGENT_TOKEN=${FAKE_NVIDIA_TOKEN}`、`AI_MONITOR_ADVERTISE_ADDRESS=agent-fake-nvidia:9400`）与 `agent-fake-ascend`（8 卡 ascend）。
- `scrape.yml`：`scrape_interval: 15s`，job `ai-monitor-agent`，`file_sd_configs: [{files: ["/file_sd/agents.json"], refresh_interval: 30s}]`。
- `install-agent.sh --mode docker|systemd --server URL --token TOKEN [--vendor nvidia|ascend]`：写 `/etc/ai-monitor/agent.yaml`；docker 模式 `docker run -d --restart=always --net=host --pid=host -v /etc/ai-monitor:/etc/ai-monitor:ro -v /var/run/docker.sock:/var/run/docker.sock` 加 `--gpus all` 或 Ascend 设备挂载（`/dev/davinci*`、`/dev/davinci_manager`、`/dev/devmm_svm`、`/dev/hisi_hdc`、`/usr/local/Ascend/driver`、`/usr/local/bin/npu-smi`）；systemd 模式创建 venv、`pip install ./agent`、安装 unit。
- README：架构简介、快速开始（compose up、登录、创建 agent token、安装 agent）、开发方式。

- [ ] Step 1: 实现全部文件
- [ ] Step 2: `docker compose --profile fake config` 校验通过
- [ ] Step 3: Commit `build(deploy): docker-compose, vm/loki config, agent installer, readme`

### Task 16: 端到端验证

- [ ] Step 1: `cp .env.example .env`；`docker compose up -d --build victoriametrics loki postgres server web`
- [ ] Step 2: `curl -s localhost:8080/api/health` → `{"status":"ok"}`；登录拿 token；`POST /api/agents {host: fake-gpu-01}` 与 `{host: fake-npu-01}` 得到两个 token，写入 `.env` 的 `FAKE_NVIDIA_TOKEN`/`FAKE_ASCEND_TOKEN`
- [ ] Step 3: `docker compose --profile fake up -d agent-fake-nvidia agent-fake-ascend`；60s 后 `GET /api/agents` 两台 `online`；`docker compose exec victoriametrics wget -qO- 'localhost:8428/api/v1/query?query=count(accel_mem_total_bytes)'` 返回 12
- [ ] Step 4: `GET /api/metrics/query?template=cluster_avg_util` 有值；`query_range?template=host_device_util&host=fake-gpu-01` 返回 4 条 series
- [ ] Step 5: 浏览器打开 `http://<host>:8080`，登录、总览与机器详情图表有数据（无浏览器时用 `curl` 校验 `index.html` 200 及上述 API）
- [ ] Step 6: 记录验证输出到 PR/提交说明；Commit `docs: phase 1 e2e verification notes`（README 追加「已验证」小节）

---

## 自检

- Spec 覆盖：第一期范围（Agent 采集/心跳/`/metrics`、server 骨架/认证/agents/metrics 代理/file_sd、web 登录/总览/机器详情、compose 与安装脚本）全部有任务；Loki 仅起服务，日志采集属第二期。
- 类型一致：`DeviceSample`/`HostSample` 字段在 Task 1、3、4 一致；心跳体字段在 Task 5 与 Task 8 一致；模板名在 Task 9 与 Task 13 一致；`AgentInfo.status` 三态在 Task 8 与 Task 11 一致。
