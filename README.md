# AI Monitor

企业内网多机 NVIDIA GPU / Ascend NPU 与 vLLM 推理服务的长期监测平台。

- 设计文档：[docs/superpowers/specs/2026-09-04-ai-monitor-design.md](docs/superpowers/specs/2026-09-04-ai-monitor-design.md)
- 第一期实施计划：[docs/superpowers/plans/2026-09-04-phase1-basic-monitoring.md](docs/superpowers/plans/2026-09-04-phase1-basic-monitoring.md)

## 架构

```
GPU/NPU 机器                         监控服务器（docker compose）
+---------------------+             +------------------------------------+
| ai-monitor-agent    |<-- scrape --| VictoriaMetrics（时序，保留 6 个月）|
|  pynvml / npu-smi   |-- 心跳 ---->| ai-monitor-server（FastAPI）        |
|  psutil             |             |   PostgreSQL（元数据）              |
|  :9400/metrics      |-- 日志 ---->| Loki（日志，第二期接入）            |
+---------------------+             | ai-monitor-web（React + nginx）     |
                                    +------------------------------------+
```

- 指标 Pull：server 根据 Agent 心跳生成 `file_sd/agents.json`，VictoriaMetrics 据此抓取每台机器的 `:9400/metrics`。
- 统一硬件指标 `accel_*`（`vendor=nvidia|ascend`），NVIDIA 与 Ascend 共用一套页面。
- 前端只访问 server API；PromQL 由后端模板生成，前端仅传模板名与参数。

## 快速开始

```bash
cd deploy
cp .env.example .env            # 修改 AIM_JWT_SECRET / AIM_ADMIN_PASSWORD
docker compose up -d --build victoriametrics loki postgres server web
```

浏览器打开 `http://<监控服务器>:8080`，用 `.env` 中的管理员账号登录。

### 注册一台机器并安装 Agent

1. 用管理员登录后创建 Agent 记录并获取 token（token 只显示一次）：

   ```bash
   TOKEN=$(curl -s -X POST localhost:8000/api/auth/login \
     -H 'content-type: application/json' \
     -d '{"username":"admin","password":"<AIM_ADMIN_PASSWORD>"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')
   curl -s -X POST localhost:8000/api/agents -H "Authorization: Bearer $TOKEN" \
     -H 'content-type: application/json' -d '{"host":"gpu-01"}'
   # => {"id":1,"host":"gpu-01","token":"<AGENT_TOKEN>"}
   ```

2. 在目标机器上安装 Agent（Docker 或 systemd 二选一）：

   ```bash
   ./deploy/install-agent.sh --mode docker  --server http://<监控服务器>:8000 --token <AGENT_TOKEN>
   ./deploy/install-agent.sh --mode systemd --server http://<监控服务器>:8000 --token <AGENT_TOKEN>
   ```

   脚本自动检测 `nvidia-smi` / `npu-smi` 决定挂载哪种设备，配置写入 `/etc/ai-monitor/agent.yaml`。

3. 约 1 分钟后，总览页面出现该机器并显示为在线。

### 无 GPU 环境的端到端验证

```bash
# 先按上面步骤创建两个 agent：fake-gpu-01 与 fake-npu-01，把 token 写入 .env 的 FAKE_NVIDIA_TOKEN / FAKE_ASCEND_TOKEN
docker compose --profile fake up -d --build agent-fake-nvidia agent-fake-ascend
```

`fake-gpu-01` 模拟 4 张 NVIDIA 卡与 2 个 vLLM 服务，`fake-npu-01` 模拟 8 张 Ascend 卡与 1 个 vLLM 服务；假 vLLM 提供 `/metrics`、`/v1/models`、`/version` 并持续写入含 WARNING/ERROR 的日志。

### vLLM 服务监测

- **自动发现**：Docker 容器（命令或镜像含 `vllm`；支持 host 网络、端口映射、仅容器 IP 三种网络形态）与本机进程（命令行含 `vllm serve` / `api_server`）。
- **手动登记**：nohup/tmux 启动且需要采集日志文件的服务，在 `/etc/ai-monitor/agent.yaml` 的 `services` 段登记 `name`、`port`、`log_path`。
- **指标**：各服务 `/metrics` 中的 `vllm:*` 指标附加 `host`、`service` 标签后并入 Agent `/metrics`；页面展示请求队列、Token 吞吐、TTFT/TPOT/E2E 分位、KV cache、抢占。
- **日志**：`docker logs` 或日志文件推送 Loki（保留 180 天），流标签 `host, service, source, level`；`level` 由 Agent 解析（vLLM / uvicorn / python logging 格式，Traceback 续行继承前一行级别）。页面支持实时 tail、级别筛选与关键词检索。
- **进程信息**：启动参数、工作目录、环境变量（名称含 `KEY/TOKEN/SECRET/PASSWORD` 的值在 Agent 侧脱敏）、模型、vLLM 版本。

若宿主机 8080/8000/8428/3100 端口被占用，在 `.env` 中修改 `WEB_PORT` / `SERVER_PORT` / `VM_PORT` / `LOKI_PORT`。

### 第一期已验证（2026-09-04）

在无 GPU 的开发机上按上述步骤执行，结果：

- `agent` 55 个测试、`server` 60 个测试、`web` 9 个测试全部通过；三个镜像构建成功。
- 两个假 Agent 心跳后 `GET /api/agents` 均为 `online`，server 生成的 `file_sd/agents.json` 含两条 target。
- VictoriaMetrics 中 `count(accel_mem_total_bytes)` = 12；`accel_ecc_errors_total` 仅出现在 `vendor="nvidia"`，`accel_health` 两种硬件均有。
- `GET /api/metrics/query?template=cluster_avg_util` 返回数值；`query_range?template=host_device_util&host=fake-gpu-01` 返回 4 条 series；未知模板 400、未登录 401。
- Playwright（Chromium）登录后：总览页显示 2/2 在线、12 卡、各机器利用率折线；`/hosts/fake-npu-01` 显示 8 张 `Ascend 910B4` 卡表与利用率/显存/温度/功耗折线，无 JS 错误。

### 第二期已验证（2026-09-04）

- `agent` 150 个测试、`server` 125 个测试、`web` 28 个测试全部通过。
- `GET /api/services` 返回 3 个 `running` 服务（fake-gpu-01 上 `fake-vllm-0/1`，fake-npu-01 上 `fake-vllm-0`），模型与版本来自假 vLLM 的 `/v1/models`、`/version`。
- VictoriaMetrics 中 `count(vllm:num_requests_running)` = 3；`query_range?template=vllm_ttft_quantiles` 返回 p50/p90/p99 三条 series。
- `GET /api/logs/query` 返回带 `level` 的日志；`level=error` 只返回 ERROR 行及其 Traceback 续行（级别继承为 error）。
- 停掉 `agent-fake-ascend` 约 2 分钟后对应服务变为 `unknown`，重启后恢复 `running`。
- Playwright：总览出现服务卡片；`/services` 列出 3 行；服务详情指标 Tab 7 张图；日志 Tab 实时出现 INFO/WARNING，无 JS 错误。

## 开发

```bash
# Agent
cd agent && python3 -m venv .venv && . .venv/bin/activate && pip install -e '.[dev]' && pytest -q
ai-monitor-agent --fake 2 --fake-vendor nvidia --host dev   # 需要 AI_MONITOR_SERVER_URL / AI_MONITOR_AGENT_TOKEN

# Server
cd server && python3 -m venv .venv && . .venv/bin/activate && pip install -e '.[dev]' && pytest -q
uvicorn ai_monitor_server.main:app --reload      # 默认 SQLite dev.db，管理员 admin/admin123

# Web
cd web && npm install && npm run dev             # /api 代理到 http://localhost:8000
```

## 分期

1. 基础监控（已完成）：硬件/主机指标、机器注册与心跳、认证、总览与机器详情、compose 部署。
2. vLLM 监测（本期）：服务发现、`/metrics` 透传、进程信息、日志到 Loki、服务详情与实时日志。
3. 调试能力：torch profiler、py-spy、nsys、msprof 远程触发与产物管理。
4. 企业能力：告警与通知、利用率报表、审计与用户管理。
