import pytest
from pydantic import ValidationError

from ai_monitor_agent.config import AgentConfig, ManualService, load_config


def test_load_config_defaults(tmp_path):
    cfg_file = tmp_path / "agent.yaml"
    cfg_file.write_text(
        "server_url: http://monitor.internal:8000\n"
        "agent_token: secret\n"
    )
    cfg = load_config(str(cfg_file))
    assert cfg.server_url == "http://monitor.internal:8000"
    assert cfg.agent_token == "secret"
    assert cfg.loki_url is None
    assert cfg.listen == "0.0.0.0:9400"
    assert cfg.advertise_address is None
    assert cfg.scrape_interval_seconds == 15
    assert cfg.heartbeat_interval_seconds == 30
    assert cfg.services == []
    assert cfg.artifacts_dir == "/var/lib/ai-monitor-agent/artifacts"


def test_load_config_full(tmp_path):
    cfg_file = tmp_path / "agent.yaml"
    cfg_file.write_text(
        """
server_url: http://monitor.internal:8000
agent_token: secret
loki_url: http://monitor.internal:3100
listen: 10.0.0.11:9500
advertise_address: 10.0.0.11:9500
scrape_interval_seconds: 5
heartbeat_interval_seconds: 10
services:
  - name: qwen-72b
    port: 8001
    log_path: /var/log/vllm/qwen-72b.log
    profiler_dir: /data/vllm_profile/qwen-72b
  - name: bare
    port: 8002
artifacts_dir: /tmp/artifacts
"""
    )
    cfg = load_config(str(cfg_file))
    assert cfg.loki_url == "http://monitor.internal:3100"
    assert cfg.listen == "10.0.0.11:9500"
    assert cfg.advertise_address == "10.0.0.11:9500"
    assert cfg.scrape_interval_seconds == 5
    assert cfg.heartbeat_interval_seconds == 10
    assert cfg.artifacts_dir == "/tmp/artifacts"
    assert cfg.services == [
        ManualService(
            name="qwen-72b",
            port=8001,
            log_path="/var/log/vllm/qwen-72b.log",
            profiler_dir="/data/vllm_profile/qwen-72b",
        ),
        ManualService(name="bare", port=8002),
    ]
    assert cfg.services[1].log_path is None
    assert cfg.services[1].profiler_dir is None


def test_missing_required_fields(tmp_path):
    cfg_file = tmp_path / "agent.yaml"
    cfg_file.write_text("server_url: http://x\n")
    with pytest.raises(ValidationError):
        load_config(str(cfg_file))


def test_listen_host_port_helpers():
    cfg = AgentConfig(server_url="http://x", agent_token="t", listen="127.0.0.1:1234")
    assert cfg.listen_host == "127.0.0.1"
    assert cfg.listen_port == 1234
    default = AgentConfig(server_url="http://x", agent_token="t")
    assert default.listen_host == "0.0.0.0"
    assert default.listen_port == 9400
