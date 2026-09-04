from ai_monitor_agent.config import AgentConfig, DiscoveryConfig, LogsConfig
from ai_monitor_agent.services import ServiceInfo

EXPECTED_FIELDS = [
    "name",
    "source",
    "port",
    "metrics_url",
    "pid",
    "container_id",
    "container_name",
    "log_source",
    "log_path",
    "profiler_dir",
    "model",
    "vllm_version",
    "started_at",
    "cmdline",
    "cwd",
    "env",
    "scrape_ok",
]


def test_service_info_defaults_and_to_dict():
    s = ServiceInfo(name="svc", source="manual", port=8001, metrics_url="http://127.0.0.1:8001")
    d = s.to_dict()
    assert list(d.keys()) == EXPECTED_FIELDS
    assert d["pid"] is None
    assert d["log_source"] == "none"
    assert d["env"] == {}
    assert d["scrape_ok"] is False
    # to_dict must deep-copy env so later mutation doesn't leak
    d["env"]["X"] = "1"
    assert s.env == {}


def test_service_info_full_round_trip():
    s = ServiceInfo(
        name="c1",
        source="docker",
        port=8001,
        metrics_url="http://127.0.0.1:8001",
        pid=42,
        container_id="abc",
        container_name="c1",
        log_source="docker",
        model="m",
        vllm_version="0.11.0",
        started_at=1.5,
        cmdline="vllm serve m",
        cwd="/",
        env={"A": "b"},
        scrape_ok=True,
    )
    d = s.to_dict()
    assert d["container_id"] == "abc"
    assert d["env"] == {"A": "b"}
    assert d["started_at"] == 1.5
    assert ServiceInfo(**d) == s


def test_config_new_sections_defaults():
    cfg = AgentConfig(server_url="http://x", agent_token="t")
    assert cfg.discovery == DiscoveryConfig()
    assert cfg.discovery.docker is True
    assert cfg.discovery.process is True
    assert cfg.discovery.interval_seconds == 30
    assert cfg.logs == LogsConfig()
    assert cfg.logs.enabled is True
    assert cfg.logs.batch_lines == 500
    assert cfg.logs.batch_interval_seconds == 1.0
    assert cfg.logs.buffer_max_lines == 10000
    assert cfg.state_dir == "/var/lib/ai-monitor-agent/state"


def test_config_new_sections_from_yaml(tmp_path):
    from ai_monitor_agent.config import load_config

    cfg_file = tmp_path / "agent.yaml"
    cfg_file.write_text(
        """
server_url: http://x
agent_token: t
discovery:
  docker: false
  interval_seconds: 5
logs:
  enabled: false
  batch_lines: 10
  batch_interval_seconds: 0.5
  buffer_max_lines: 100
state_dir: /tmp/state
"""
    )
    cfg = load_config(str(cfg_file))
    assert cfg.discovery.docker is False
    assert cfg.discovery.process is True
    assert cfg.discovery.interval_seconds == 5
    assert cfg.logs.enabled is False
    assert cfg.logs.batch_lines == 10
    assert cfg.logs.batch_interval_seconds == 0.5
    assert cfg.logs.buffer_max_lines == 100
    assert cfg.state_dir == "/tmp/state"
