FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY pyproject.toml README.md ./
COPY ai_monitor_agent ./ai_monitor_agent
RUN pip install .

EXPOSE 9400
CMD ["ai-monitor-agent", "--config", "/etc/ai-monitor/agent.yaml"]
