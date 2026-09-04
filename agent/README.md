# ai-monitor-agent

Per-host agent for AI Monitor. Collects NVIDIA GPU / Ascend NPU and host metrics,
exposes them at `:9400/metrics` in Prometheus format and sends periodic heartbeats
to the AI Monitor server.

```bash
pip install -e '.[dev]'
pytest -q
ai-monitor-agent --config agent.yaml
# no hardware? run with synthetic devices:
AI_MONITOR_SERVER_URL=http://server:8000 AI_MONITOR_AGENT_TOKEN=xxx \
  ai-monitor-agent --fake 4 --fake-vendor nvidia --host demo
```
