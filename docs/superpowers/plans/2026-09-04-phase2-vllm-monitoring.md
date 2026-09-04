# 第二期「vLLM 监测」实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Agent 自动发现 Docker/进程/手动登记的 vLLM 服务，透传其 `/metrics`、上报进程信息、把日志（带 level 标签）推送 Loki；server 维护 `services` 表并提供服务、vLLM 指标模板、日志查询与实时 tail；web 新增服务列表与服务详情（指标/进程/日志）。

**Architecture:** 沿用第一期：指标 Pull（vLLM 指标并入 Agent `/metrics`）、日志 Push（Agent → Loki）、心跳携带 `services[]`；server 代理 VM 与 Loki（HTTP 与 WebSocket），前端只访问 server。

**Tech Stack:** 第一期基础上，Agent 新增 `docker`（SDK）；server 新增 `websockets`（Loki tail 客户端）；web 复用现有依赖。

**Spec:** [2026-09-04-phase2-vllm-monitoring-design.md](../specs/2026-09-04-phase2-vllm-monitoring-design.md)

## Global Constraints

- 服务身份 `(host, name)` 唯一；Docker 服务 name = 容器名，进程服务 name = 手动登记名或 `vllm-<port>`。
- 三源合并优先级 manual > docker > process，按 `port` 去重。
- Docker `metrics_url`：host 网络 → `http://127.0.0.1:<port>`；有宿主机映射 → `http://127.0.0.1:<hostport>`；否则 `http://<容器IP>:<容器内端口>`。端口判定：`--port` > `VLLM_PORT` > 8000。
- 透传只保留名称以 `vllm` 开头的 metric family，追加 `host`、`service` 标签；新增 `agent_vllm_scrape_success{host,service}`、`agent_vllm_services{host}`、`agent_log_lines_dropped_total{host,service}`。
- 环境变量脱敏：名称含 `KEY`、`TOKEN`、`SECRET`、`PASSWORD`、`PASSWD`（不区分大小写）→ 值 `***`。
- Loki 流标签固定 `host, service, source, level`；`level ∈ {debug, info, warning, error, critical, unknown}`。
- Loki 保留 `4320h`。
- LogQL 只由 server 拼接；`host`/`service` 匹配 `^[A-Za-z0-9_.-]+$`，`level` 在 6 值内，`q` 转义 `\` 与 `"`。
- `window` 参数白名单 `^[0-9]+[smh]$`，默认 `1m`。
- `GET /api/services` 默认 `active=all`。
- 每个任务结束提交一次；测试不依赖网络与真实 Docker。

---

## 文件结构

```
agent/ai_monitor_agent/
  config.py                    # 修改：DiscoveryConfig / LogsConfig / state_dir
  services.py                  # 新增：ServiceInfo dataclass + to_dict()
  discovery/__init__.py
  discovery/docker.py          # DockerDiscovery
  discovery/process.py         # ProcessDiscovery
  discovery/manual.py          # ManualDiscovery
  discovery/process_info.py    # read_process_info / redact_env
  discovery/registry.py        # ServiceRegistry
  vllm/__init__.py
  vllm/metrics_proxy.py        # VllmMetricsProxy
  vllm/probe.py                # fetch_model_and_version
  logs/__init__.py
  logs/level_parser.py         # parse_level
  logs/loki_client.py          # LokiPusher
  logs/tailer.py               # FileTailer / DockerTailer / LogManager
  logs/cursors.py              # CursorStore
  fake/__init__.py
  fake/vllm.py                 # FakeVllmServer
  metrics.py                   # 修改：追加 proxy 输出与 agent_* 指标
  app.py                       # 修改：lifespan 启动 registry/proxy/logs；inventory 带 services
  __main__.py                  # 修改：--fake-vllm
agent/tests/
  test_services.py test_discovery_docker.py test_discovery_process.py test_registry.py
  test_process_info.py test_metrics_proxy.py test_level_parser.py test_loki_client.py
  test_tailer.py test_fake_vllm.py
server/ai_monitor_server/
  models.py                    # 修改：Service
  alembic/versions/0002_services.py
  agents/schemas.py            # 修改：ServiceIn；HeartbeatRequest.services: list[ServiceIn]
  agents/router.py             # 修改：heartbeat upsert services
  services/__init__.py services/schemas.py services/status.py services/router.py
  logs/__init__.py logs/logql.py logs/loki_client.py logs/router.py
  metrics/templates.py         # 修改：vLLM 模板 + window
  metrics/router.py            # 修改：允许 service/window 参数
  config.py                    # loki_url 已有
server/tests/
  test_services.py test_logql.py test_logs_router.py test_logs_tail.py test_metrics_templates.py(追加)
web/src/
  api/services.ts api/logs.ts
  pages/ServicesPage.tsx pages/ServiceDetailPage.tsx
  components/ServicesTable.tsx components/LogViewer.tsx components/EnvTable.tsx
  metrics/window.ts (+ window.test.ts)
  logs/logLines.ts (+ logLines.test.ts)   # 行缓冲上限、level 颜色映射
  pages/OverviewPage.tsx pages/HostDetailPage.tsx App.tsx components/AppLayout.tsx  # 修改
deploy/
  loki/config.yml docker-compose.yml install-agent.sh agent/agent.example.yaml  # 修改
```

---

### Task A1: 服务模型、配置扩展、进程信息读取

**Files:** `agent/ai_monitor_agent/services.py`, `config.py`（修改）, `discovery/__init__.py`, `discovery/process_info.py`; Test: `tests/test_services.py`, `tests/test_process_info.py`

**Interfaces（Produces）:**

```python
# services.py
Source = Literal["docker", "process", "manual"]; LogSource = Literal["docker", "file", "none"]
@dataclass
class ServiceInfo:
    name: str; source: Source; port: int; metrics_url: str
    pid: int | None = None; container_id: str | None = None; container_name: str | None = None
    log_source: LogSource = "none"; log_path: str | None = None; profiler_dir: str | None = None
    model: str | None = None; vllm_version: str | None = None; started_at: float | None = None
    cmdline: str | None = None; cwd: str | None = None; env: dict[str, str] = field(default_factory=dict)
    scrape_ok: bool = False
    def to_dict(self) -> dict[str, Any]   # asdict

# config.py
class DiscoveryConfig(BaseModel): docker: bool = True; process: bool = True; interval_seconds: int = 30
class LogsConfig(BaseModel): enabled: bool = True; batch_lines: int = 500; batch_interval_seconds: float = 1.0; buffer_max_lines: int = 10000
AgentConfig: + discovery: DiscoveryConfig = DiscoveryConfig(); logs: LogsConfig = LogsConfig(); state_dir: str = "/var/lib/ai-monitor-agent/state"

# discovery/process_info.py
REDACT_RE = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD)", re.I)
def redact_env(env: Mapping[str, str]) -> dict[str, str]
def read_process_info(pid: int) -> ProcessInfo   # dataclass(cmdline: str|None, cwd: str|None, started_at: float|None, env: dict)  用 psutil.Process；AccessDenied → env {}
```

- [ ] 测试：`redact_env({"HF_TOKEN":"x","PATH":"/bin","api_key":"k"})` → token/api_key 为 `***`；`read_process_info(os.getpid())` cmdline 非空、started_at>0
- [ ] 实现；`pytest -q` 通过；Commit `feat(agent): service model, discovery config, process info`

### Task A2: Docker / 进程 / 手动发现与 Registry

**Files:** `discovery/docker.py`, `discovery/process.py`, `discovery/manual.py`, `discovery/registry.py`, `vllm/probe.py`; Test: `tests/test_discovery_docker.py`, `tests/test_discovery_process.py`, `tests/test_registry.py`

**Interfaces:**

```python
class DockerDiscovery:
    def __init__(self, client_factory: Callable[[], Any] = docker.from_env); name = "docker"
    def available(self) -> bool             # 尝试 client.ping()，失败一次警告并禁用
    def discover(self) -> list[ServiceInfo]  # 只看 status=running 容器
def container_to_service(attrs: dict) -> ServiceInfo | None   # 纯函数，便于测试；非 vLLM 返回 None
class ProcessDiscovery:
    def __init__(self, proc_iter=psutil.process_iter, net_connections=psutil.net_connections, exclude_pids: Callable[[], set[int]] = set)
    def discover(self) -> list[ServiceInfo]
def is_vllm_cmdline(cmdline: list[str]) -> bool   # 含 "vllm" 且含 serve|api_server|entrypoints.openai
def port_from_cmdline(cmdline: list[str]) -> int | None   # --port 8001 / --port=8001
class ManualDiscovery: def __init__(self, services: list[ManualService]); def discover(self) -> list[ServiceInfo]
class ServiceRegistry:
    def __init__(self, sources: list, probe: Callable[[str], tuple[str|None, str|None]] = fetch_model_and_version)
    def refresh(self) -> list[ServiceInfo]   # 合并、去重、补 process_info、首次或缺失时 probe
    def snapshot(self) -> list[ServiceInfo]
    def set_scrape_ok(self, name: str, ok: bool) -> None
# vllm/probe.py
def fetch_model_and_version(base_url: str, timeout: float = 3.0) -> tuple[str | None, str | None]  # GET /v1/models data[0].id, GET /version version
```

- [ ] 测试：`container_to_service` 三种网络场景（host / 映射 8001→8000 / 仅容器 IP 172.17.0.5）+ 非 vLLM 容器 None + `--port`/`VLLM_PORT`/默认 8000；进程发现：fake 进程列表含 vLLM 父进程与 worker 子进程只返回父进程、端口来自 LISTEN；registry：manual 覆盖 docker 同端口的 name/log_path、process 中与 docker pid 相同的排除；probe 用 respx
- [ ] 实现；通过；Commit `feat(agent): docker/process/manual discovery and service registry`

### Task A3: vLLM 指标透传

**Files:** `vllm/metrics_proxy.py`, `metrics.py`（修改）; Test: `tests/test_metrics_proxy.py`

**Interfaces:**

```python
class VllmMetricsProxy:
    def __init__(self, registry: ServiceRegistry, host: str, timeout: float = 5.0)
    async def refresh_once(self, client: httpx.AsyncClient) -> None   # 并发抓取，更新 cache 与 registry.set_scrape_ok
    async def run(self, stop: asyncio.Event, interval: float) -> None
    def render(self) -> bytes            # 已重标签的 vLLM 文本（不含 # EOF）
    def scrape_status(self) -> dict[str, bool]
def relabel_vllm_text(text: str, host: str, service: str) -> str   # 纯函数：过滤 vllm* family，追加标签，重新序列化（HELP/TYPE 保留一次）
```

`metrics.py`：`AgentMetricsCollector.__init__` 新增可选 `proxy: VllmMetricsProxy | None`；`collect()` 追加 `agent_vllm_scrape_success{host,service}`、`agent_vllm_services{host}`；`app.py` 的 `/metrics` 响应 = `render(registry) + proxy.render()`（proxy 文本放在 prometheus_client 输出之后，去掉其结尾 `# EOF` 不适用——prometheus_client 0.0.4 文本格式无 EOF，直接拼接即可）。

- [ ] 测试：`relabel_vllm_text` 输入含 `python_gc_*`、`vllm:num_requests_running{model_name="m"} 3`、直方图 `vllm:e2e_request_latency_seconds_bucket{le="1.0",model_name="m"} 5` → 输出无 `python_`，样本带 `host="h",service="s"`，bucket `le` 保留；`refresh_once` 用 respx 两服务（一个 500）→ scrape_status 分别 True/False，render 只含成功服务
- [ ] 实现；通过；Commit `feat(agent): vllm metrics passthrough with host/service labels`

### Task A4: 日志级别解析、Loki 推送、tail、cursor

**Files:** `logs/level_parser.py`, `logs/loki_client.py`, `logs/cursors.py`, `logs/tailer.py`; Test: `tests/test_level_parser.py`, `tests/test_loki_client.py`, `tests/test_tailer.py`

**Interfaces:**

```python
LEVELS = ("debug", "info", "warning", "error", "critical", "unknown")
def parse_level(line: str, prev_level: str = "unknown") -> str   # 规则见 spec 4.5
@dataclass
class LogLine: ts_ns: int; line: str; level: str
class LokiPusher:
    def __init__(self, loki_url: str, host: str, batch_lines: int, batch_interval: float, buffer_max_lines: int)
    def enqueue(self, service: str, source: str, line: LogLine) -> None   # 超限丢最旧并 dropped[service] += 1
    async def run(self, stop: asyncio.Event, client: httpx.AsyncClient) -> None   # 定时 flush；失败退避 1s→30s，数据保留在缓冲
    async def flush(self, client) -> bool
    def build_payload(self, batch: list[tuple[str, str, LogLine]]) -> dict   # 按 (service, source, level) 分 stream，values 内 ts 单调不减
    dropped: dict[str, int]
class CursorStore: __init__(path); get(key) -> dict|None; set(key, value: dict); async run(stop, interval=5.0) 定时落盘; save()
class FileTailer:   # key = f"file:{path}"；cursor {"inode", "offset"}；轮转（inode 变化或 size < offset）从 0 读
    def __init__(self, service: str, path: str, pusher: LokiPusher, cursors: CursorStore)
    async def run(self, stop: asyncio.Event, poll_interval=0.5)
class DockerTailer:  # key = f"docker:{container_id}"；cursor {"since": unix 秒}；container.logs(stream=True, follow=True, since=..., timestamps=True) 在线程中迭代，解析行首 RFC3339Nano 时间戳
class LogManager:    # 根据 registry.snapshot() 增删 tailer 任务
    def __init__(self, registry, pusher, cursors, docker_client_factory)
    async def run(self, stop: asyncio.Event, interval: float = 5.0)
```

- [ ] 测试：`parse_level` 五类样本（vLLM `INFO 09-04 ...`、`WARNING:  uvicorn`、`2026-09-04 [ERROR] x`、`Traceback (most recent call last):` 继承 error、`  File "x.py"` 继承、`ValueError: bad` 继承、随机行 unknown、`WARN`→warning）；`LokiPusher.build_payload` 分流与标签；缓冲超限丢弃计数；respx 500 → 退避且数据保留、恢复后发出；`FileTailer` 临时文件追加 3 行读到 3 行、cursor offset 正确、模拟轮转（重建文件）后重新从 0 读
- [ ] 实现；通过；Commit `feat(agent): log tailing with level labels and loki push`

### Task A5: 假 vLLM、应用装配、CLI、心跳

**Files:** `fake/vllm.py`, `app.py`（修改）, `__main__.py`（修改）, `heartbeat.py`（inventory 已含 services，无需改）; Test: `tests/test_fake_vllm.py`, `tests/test_app.py`（追加）

**Interfaces:**

```python
class FakeVllmServer:
    def __init__(self, index: int, port: int, log_path: str, model: str = "fake/Qwen2.5-7B-Instruct")
    def app(self) -> FastAPI   # /metrics /v1/models /version
    async def run(self, stop: asyncio.Event)   # uvicorn Server 在同一事件循环 + 日志写入任务（每秒 1 行；每 20 行 WARNING；每 60 行 ERROR + 4 行 Traceback）
def fake_vllm_manual_services(n: int, state_dir: str, base_port: int = 18000) -> list[ManualService]
```

`app.py`：`create_app(config, device_collectors, host_collector, host=None, registry: ServiceRegistry | None = None, fake_vllm: list[FakeVllmServer] = [])`；lifespan 启动：fake vLLM → registry 周期 refresh 任务（`discovery.interval_seconds`）→ proxy.run → 若 `loki_url` 且 `logs.enabled` 则 CursorStore/LokiPusher/LogManager；inventory 的 `services` = `[s.to_dict() for s in registry.snapshot()]`；`/healthz` 增加 `"services": <count>`、`"logs": bool`。`__main__.py`：`--fake-vllm N`；构建 registry 的 sources：ManualDiscovery(config.services + fake)、DockerDiscovery（config.discovery.docker 且非 --fake）、ProcessDiscovery（同）。

- [ ] 测试：FakeVllmServer `/metrics` 含 `vllm:num_requests_running`、直方图 bucket、`/v1/models`；app 在 `--fake-vllm 1` 等价装配下 `/metrics` 含 `vllm:num_requests_running{...service="fake-vllm-0"...}`、`/healthz.services == 1`；心跳 payload `services[0]["name"] == "fake-vllm-0"`
- [ ] 手动：`ai-monitor-agent --fake 1 --fake-vllm 2 --host demo`（环境变量假 server）→ `curl :9400/metrics | grep -c 'service="fake-vllm-1"'` > 0；日志文件在 state_dir 生成
- [ ] 更新 `deploy/agent/agent.example.yaml` 新增 `discovery`/`logs`/`state_dir` 段；Commit `feat(agent): fake vllm, app wiring, --fake-vllm cli`

### Task S1: services 表、心跳 upsert、services API

**Files:** `models.py`（修改）, `alembic/versions/0002_services.py`, `agents/schemas.py`（修改）, `agents/router.py`（修改）, `services/schemas.py`, `services/status.py`, `services/router.py`, `main.py`（注册路由）; Test: `tests/test_services.py`

**Interfaces:**

```python
class Service(Base): __tablename__="services"; id; agent_id FK(agents.id, ondelete=CASCADE); name; source; port; metrics_url; pid; container_id; container_name; log_source; log_path; profiler_dir; model; vllm_version; started_at: datetime|None; cmdline: Text|None; cwd; env_json: JSON; scrape_ok: bool; active: bool=True; first_seen_at; last_seen_at; UniqueConstraint(agent_id, name)
class ServiceIn(BaseModel, extra="ignore"): name; source; port; metrics_url; pid=None; container_id=None; container_name=None; log_source="none"; log_path=None; profiler_dir=None; model=None; vllm_version=None; started_at: float|None=None; cmdline=None; cwd=None; env: dict[str,str]={}; scrape_ok=False
HeartbeatRequest.services: list[ServiceIn] = []
ServiceStatus = Literal["running","degraded","stopped","unknown"]
def compute_status(service: Service, agent_status: AgentStatus) -> ServiceStatus  # agent 非 online → unknown；not active → stopped；not scrape_ok → degraded；else running
class ServiceOut: id, agent_id, host, name, source, port, metrics_url, pid, container_name, model, vllm_version, started_at, log_source, scrape_ok, active, last_seen_at, status
class ServiceDetail(ServiceOut): cmdline, cwd, env: dict, container_id, log_path, profiler_dir, first_seen_at
GET /api/services?active=all|true|false(默认 all)  -> list[ServiceOut]  (需登录)
GET /api/services/{id} -> ServiceDetail (404)
GET /api/agents/{id}/services -> list[ServiceOut]
```

心跳：在同一事务内 upsert；未出现者 `active=False`。`started_at` 从 unix 秒转 UTC datetime。

- [ ] 测试：心跳带 2 服务 → 列表 2 条 running；第二次心跳只带 1 个 → 另一个 `stopped` 且 `active=False`，默认列表仍返回 2 条，`active=true` 返回 1 条；`scrape_ok=false` → degraded；把 agent `last_seen_at` 改为 10 分钟前 → unknown；detail 含 env 与 cmdline；`GET /api/agents/{id}/services`
- [ ] 实现 + alembic 迁移（验证 `alembic upgrade head` 于 SQLite）；通过；Commit `feat(server): services registry from heartbeat and services api`

### Task S2: vLLM 指标模板与参数扩展

**Files:** `metrics/templates.py`（修改）, `metrics/router.py`（修改）; Test: `tests/test_metrics_templates.py`（追加）

**Interfaces:** `Template` 新增可选 `defaults: dict[str,str]`（`{"window":"1m"}`）；`render` 对 `window` 用 `WINDOW_RE = ^[0-9]+[smh]$` 校验；模板名与表达式**按 spec 5.3 表格**（13 个）。路由允许参数 `host, index, service, window`。

- [ ] 测试：`render("vllm_ttft_quantiles", {"host":"h","service":"s"})` 含 `[1m]` 与三个 `label_replace`；`window="5m"` 生效；`window="5x"` ValueError；`cluster_vllm_running_by_service` 无参数
- [ ] 实现；通过；Commit `feat(server): vllm promql templates with window param`

### Task S3: 日志查询与实时 tail 代理

**Files:** `logs/logql.py`, `logs/loki_client.py`, `logs/router.py`, `main.py`（注册）, `pyproject.toml`（+ `websockets`）; Test: `tests/test_logql.py`, `tests/test_logs_router.py`, `tests/test_logs_tail.py`

**Interfaces:**

```python
def build_logql(host: str, service: str, levels: list[str], q: str | None) -> str
   # '{host="h",service="s"}' + (',level=~"error|warning"' 放入选择器花括号内) + (' |= "escaped"')；非法 host/service/level → ValueError
class LokiClient:
    def __init__(self, base_url: str)
    async def query_range(self, query: str, start_ns: int, end_ns: int, limit: int, direction: str) -> list[dict]  # 返回 [{"ts": str, "level": str, "line": str}] 按 ts 升序
    def tail_url(self, query: str, start_ns: int, limit: int = 100) -> str   # ws(s)://.../loki/api/v1/tail?query=...&delay_for=0&limit=...&start=...
GET /api/logs/query?host&service&level*&q&start&end&limit=500&direction=backward -> {"lines":[...], "has_more": bool}  # has_more = len(lines)==limit；limit 上限 5000；ValueError→400；LokiError→502
WS  /api/logs/tail?host&service&level*&q&token -> 每条 {"ts","level","line"}；token 无效/非 access → close(4401)
```

- [ ] 测试：`build_logql` 常规/带 level/带 q 转义（`a"b\c` → `a\"b\\c`）/非法值；`query_range` 用 respx 模拟 Loki 响应（两个 stream 不同 level）→ 合并升序且 level 正确；tail：用 `websockets.serve` 起假 Loki 发送两帧后关闭，通过 FastAPI TestClient `websocket_connect` 收到 2 条；无 token → 4401
- [ ] 实现；通过；Commit `feat(server): loki log query and websocket tail proxy`

### Task W1: API 客户端与工具

**Files:** `api/services.ts`, `api/logs.ts`, `metrics/window.ts`, `logs/logLines.ts`; Test: `metrics/window.test.ts`, `logs/logLines.test.ts`

**Interfaces:**

```ts
type ServiceStatus = 'running'|'degraded'|'stopped'|'unknown'
interface ServiceInfo { id:number; agent_id:number; host:string; name:string; source:'docker'|'process'|'manual'; port:number; metrics_url:string; pid:number|null; container_name:string|null; model:string|null; vllm_version:string|null; started_at:string|null; log_source:'docker'|'file'|'none'; scrape_ok:boolean; active:boolean; last_seen_at:string|null; status:ServiceStatus }
interface ServiceDetail extends ServiceInfo { cmdline:string|null; cwd:string|null; env:Record<string,string>; container_id:string|null; log_path:string|null; profiler_dir:string|null; first_seen_at:string }
listServices(active:'all'|'true'|'false'='all'), getService(id), listAgentServices(agentId)
type LogLevel = 'debug'|'info'|'warning'|'error'|'critical'|'unknown'
interface LogLine { ts:string; level:LogLevel; line:string }
queryLogs({host, service, levels, q, start, end, limit, direction}) -> {lines, has_more}
openLogTail({host, service, levels, q}, onLine:(l:LogLine)=>void, onStatus:(s:'open'|'closed'|'error')=>void): () => void   // 返回 close 函数；URL 从 location.protocol 推导 ws/wss，token 取 storage access token
windowForRange(range: TimeRange): '1m'|'5m'|'30m'   // ≤6h→1m, ≤7d→5m, 否则 30m
appendCapped(buf: LogLine[], incoming: LogLine[], cap=2000): LogLine[]
levelColor(level: LogLevel): string   // warning 橙 #fa8c16, error/critical 红 #f5222d, debug 灰 #8c8c8c, 其他 inherit
sortServices(list: ServiceInfo[]): ServiceInfo[]   // stopped 最后，其余按 host, name
```

- [ ] 测试：`windowForRange` 三档；`appendCapped` 超限保留最新 2000；`sortServices` stopped 在后
- [ ] 实现；`vitest` 通过；Commit `feat(web): services/logs api clients and helpers`

### Task W2: 服务列表与服务详情页

**Files:** `components/ServicesTable.tsx`, `components/LogViewer.tsx`, `components/EnvTable.tsx`, `pages/ServicesPage.tsx`, `pages/ServiceDetailPage.tsx`, `App.tsx`（路由 `/services`, `/services/:id`）, `AppLayout.tsx`（菜单「服务」）

内容按 spec 第 6 节：ServicesTable（status Tag 四色、running/waiting 即时值来自 `cluster_vllm_running_by_service`/`cluster_vllm_waiting_by_service` 以 `host+service` 匹配，stopped 显示 `-`；行点击进详情）；ServicesPage（搜索、隐藏已停止开关默认关、状态列筛选、默认 `sortServices`）；ServiceDetailPage Tabs 指标/进程/日志；LogViewer（实时开关、级别多选默认全选、关键词、`appendCapped`、自动滚动可暂停、「加载更早」、按 `levelColor` 着色、等宽）。

- [ ] 实现；`npm run build`、`vitest` 通过；Commit `feat(web): services list and service detail with metrics/process/logs`

### Task W3: 总览与机器详情加入服务

**Files:** `pages/OverviewPage.tsx`, `pages/HostDetailPage.tsx`（修改）

总览：StatCard「vLLM 服务」running/总数；服务表前 10（按 waiting 降序，复用 ServicesTable 精简列）。机器详情：新增「服务」卡片（`listAgentServices(agent.id)`，复用 ServicesTable）。

- [ ] 实现；build 通过；Commit `feat(web): services on overview and host detail`

### Task D1: 部署更新

**Files:** `deploy/loki/config.yml`（`retention_period: 4320h`）, `deploy/docker-compose.yml`（fake agent 加 `--fake-vllm 2` / `--fake-vllm 1`、`AI_MONITOR_LOKI_URL=http://loki:3100`、匿名卷 `/var/lib/ai-monitor-agent`）, `deploy/install-agent.sh`（`--loki` 默认从 `--server` 推导同主机 `:3100`；`--log-dir` 可重复，Docker 模式追加 `-v dir:dir:ro`；配置写入 `discovery`/`logs`/`state_dir`）, `README.md`（服务监测说明、日志级别、手动登记示例）

- [ ] 实现；`docker compose --profile fake config -q`；Commit `build(deploy): loki retention 180d, fake vllm agents, installer loki/log-dir`

### Task E2E

- [ ] `docker compose up -d --build`（含 `--profile fake`），60s 后：`GET /api/services` 3 条 `running`（fake-vllm-0/1 于 fake-gpu-01，fake-vllm-0 于 fake-npu-01）
- [ ] VM：`count(vllm:num_requests_running)` = 3；`GET /api/metrics/query_range?template=vllm_ttft_quantiles&host=fake-gpu-01&service=fake-vllm-0` 返回 3 条 series（q=p50/p90/p99）
- [ ] `GET /api/logs/query?host=fake-gpu-01&service=fake-vllm-0&limit=50` 有行且 level 含 info；`level=error` 只返回 error 行（含 Traceback 续行）
- [ ] `docker compose stop agent-fake-ascend` → 3 分钟内 `fake-npu-01` 的服务 `unknown`；`start` 后恢复 `running`
- [ ] Playwright：登录 → `/services` 出现 3 行 → 进入详情 → 指标 Tab 有 canvas → 日志 Tab 开实时 10s 内新增行且存在橙色 WARNING 行；无 JS 错误
- [ ] README 追加「第二期已验证」；Commit；合并到 main 并推送

---

## 自检

- Spec 覆盖：4.1–4.7 → A1–A5；5.1–5.3 → S1–S3；第 6 节 → W1–W3；第 7 节 → D1；第 10 节测试分布在各任务与 E2E。
- 契约一致：`ServiceInfo.to_dict()` 字段 = `ServiceIn` 字段 = `ServiceDetail` 字段来源；日志行 `{ts, level, line}` 在 S3 与 W1 一致；模板名在 S2 与 W2 一致；`window` 三档在 W1 与 S2 白名单兼容。
