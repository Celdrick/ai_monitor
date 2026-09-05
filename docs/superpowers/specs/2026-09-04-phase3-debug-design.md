# 第三期「调试能力」设计文档

日期：2026-09-04
状态：已确认
上游：[2026-09-04-ai-monitor-design.md](2026-09-04-ai-monitor-design.md)

## 1. 目标

对运行中的 vLLM 按需采集调试产物，不要求重启服务：

- torch profiler：转发 `/start_profile`、`/stop_profile`，回收 `profiler_dir` 中的 Chrome trace。
- py-spy：对主进程 dump / record。
- nsys（NVIDIA）：附着采样，产物 `.nsys-rep`。
- msprof（Ascend）：短时采集，产物目录打成 `.tar.gz`。

产物以下载为主；torch trace 提供「下载后用 ui.perfetto.dev 打开」说明。控制仅 admin；操作写入 `audit_logs`（无独立页面）。

非目标：告警/报表/用户管理 UI、自托管 Perfetto、重启服务后再采样。

## 2. 已确认决策

| 项 | 结论 |
| --- | --- |
| 附着 | 对运行中进程采样；工具不在 PATH 时按钮禁用并提示 |
| 查看 | 下载 + Perfetto 本地打开说明 |
| 并发 | 每台 Agent 同时只跑 1 个采样任务（profile start 占用到 stop/失败） |
| 时长 | py-spy / nsys / msprof 默认 15s，范围 5–60s；stop_profile 超时 600s |
| 无工具 e2e | `--fake-debug` 伪造 py-spy/nsys/msprof 产物；假 vLLM 实现 start/stop_profile |
| Agent token | server 增加 `agents.token_enc`（Fernet），创建/轮转时写入，才能回调 Agent |

## 3. Agent 控制 API（`:9400`，Bearer agent_token）

- `GET /control/capabilities` → `{profile: true, pyspy, nsys, msprof}`。后三者 `shutil.which`；`--fake-debug` 时三者为 true。
- `POST /control/profile/start|stop {service}`
- `POST /control/pyspy {service, mode: dump|record, duration}`
- `POST /control/nsys {service, duration}`、`POST /control/msprof {service, duration}`
- `GET /control/tasks/{id}`、`POST /control/tasks/{id}/cancel`
- `GET /control/artifacts/{id}`

任务状态：`pending/running/succeeded/failed/cancelled`。创建返回 202 `{id, status}`。

工具命令与产物类型见实施计划。404 `/start_profile` 的错误文案必须包含 `--profiler-config`。

## 4. Server

- 表：`debug_tasks`、`artifacts`、`audit_logs`；`agents.token_enc`。
- `POST /api/debug/tasks`（admin）、`GET /api/debug/tasks`、`GET /api/debug/tasks/{id}`、`GET /api/debug/artifacts/{id}`（admin 下载）、`GET /api/debug/capabilities?service_id=`。
- 服务须 `running|degraded`，agent `online` 且有 `advertise_address`；否则 409。
- 单文件上限 512MB。nvidia 隐藏 msprof，ascend 隐藏 nsys。

## 5. Web

服务详情第四 Tab「调试」，仅 admin。capabilities 控制按钮；任务表轮询 2s；torch 产物显示 Perfetto 用法。
