import pytest

from ai_monitor_agent.__main__ import build_runtime, parse_args


def test_parse_args_defaults():
    args = parse_args(["--config", "a.yaml"])
    assert args.config == "a.yaml"
    assert args.fake is None
    assert args.fake_vendor == "nvidia"
    assert args.host is None


def test_parse_args_fake():
    args = parse_args(["--fake", "4", "--fake-vendor", "ascend", "--host", "demo"])
    assert args.fake == 4
    assert args.fake_vendor == "ascend"
    assert args.host == "demo"


def test_parse_args_rejects_bad_vendor():
    with pytest.raises(SystemExit):
        parse_args(["--fake", "1", "--fake-vendor", "amd"])


def test_build_runtime_from_env_only(monkeypatch):
    monkeypatch.setenv("AI_MONITOR_SERVER_URL", "http://s:8000")
    monkeypatch.setenv("AI_MONITOR_AGENT_TOKEN", "envtok")
    monkeypatch.setenv("AI_MONITOR_ADVERTISE_ADDRESS", "agent-fake:9400")
    monkeypatch.setenv("AI_MONITOR_LISTEN", "127.0.0.1:9401")
    args = parse_args(["--fake", "2", "--fake-vendor", "ascend", "--host", "demo"])
    rt = build_runtime(args)
    assert rt.config.server_url == "http://s:8000"
    assert rt.config.agent_token == "envtok"
    assert rt.config.advertise_address == "agent-fake:9400"
    assert rt.config.listen == "127.0.0.1:9401"
    assert rt.host == "demo"
    assert [c.name for c in rt.device_collectors] == ["fake-ascend"]
    assert rt.device_collectors[0].count == 2
    assert rt.host_collector is not None


def test_build_runtime_missing_env_and_config_errors(monkeypatch):
    monkeypatch.delenv("AI_MONITOR_SERVER_URL", raising=False)
    monkeypatch.delenv("AI_MONITOR_AGENT_TOKEN", raising=False)
    args = parse_args(["--fake", "1"])
    with pytest.raises(SystemExit):
        build_runtime(args)


def test_build_runtime_config_file_with_env_override(tmp_path, monkeypatch):
    cfg = tmp_path / "agent.yaml"
    cfg.write_text("server_url: http://file:8000\nagent_token: filetok\nlisten: 0.0.0.0:9400\n")
    monkeypatch.setenv("AI_MONITOR_SERVER_URL", "http://env:8000")
    monkeypatch.delenv("AI_MONITOR_AGENT_TOKEN", raising=False)
    monkeypatch.delenv("AI_MONITOR_ADVERTISE_ADDRESS", raising=False)
    monkeypatch.delenv("AI_MONITOR_LISTEN", raising=False)
    args = parse_args(["--config", str(cfg), "--fake", "1"])
    rt = build_runtime(args)
    assert rt.config.server_url == "http://env:8000"
    assert rt.config.agent_token == "filetok"
    assert rt.config.listen == "0.0.0.0:9400"


def test_build_runtime_default_host_is_hostname(monkeypatch):
    import socket

    monkeypatch.setenv("AI_MONITOR_SERVER_URL", "http://s:8000")
    monkeypatch.setenv("AI_MONITOR_AGENT_TOKEN", "t")
    monkeypatch.setattr(socket, "gethostname", lambda: "my-box")
    rt = build_runtime(parse_args(["--fake", "1"]))
    assert rt.host == "my-box"


def test_build_runtime_real_mode_only_loads_available(monkeypatch):
    monkeypatch.setenv("AI_MONITOR_SERVER_URL", "http://s:8000")
    monkeypatch.setenv("AI_MONITOR_AGENT_TOKEN", "t")
    monkeypatch.setattr("ai_monitor_agent.__main__.NvidiaCollector.available", lambda self: False)
    monkeypatch.setattr("ai_monitor_agent.__main__.AscendCollector.available", lambda self: True)
    rt = build_runtime(parse_args([]))
    assert [c.name for c in rt.device_collectors] == ["ascend"]
    assert [s.name for s in rt.sources] == ["manual", "docker", "process"]
    assert rt.fake_vllm == []


def test_parse_args_fake_vllm():
    args = parse_args(["--fake", "1", "--fake-vllm", "2"])
    assert args.fake_vllm == 2
    assert parse_args([]).fake_vllm is None


def test_build_runtime_fake_vllm_registers_manual_services(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_MONITOR_SERVER_URL", "http://s:8000")
    monkeypatch.setenv("AI_MONITOR_AGENT_TOKEN", "t")
    monkeypatch.setenv("AI_MONITOR_LOKI_URL", "http://loki:3100")
    monkeypatch.setenv("AI_MONITOR_STATE_DIR", str(tmp_path / "state"))
    rt = build_runtime(parse_args(["--fake", "1", "--fake-vllm", "2", "--host", "demo"]))
    assert rt.config.loki_url == "http://loki:3100"
    assert rt.config.state_dir == str(tmp_path / "state")
    assert (tmp_path / "state").is_dir()
    assert [f.port for f in rt.fake_vllm] == [18000, 18001]
    assert rt.fake_vllm[1].log_path == str(tmp_path / "state" / "fake-vllm-1.log")
    # fake mode: only manual discovery (no docker / process)
    assert [s.name for s in rt.sources] == ["manual"]
    names = sorted(s.name for s in rt.sources[0].discover())
    assert names == ["fake-vllm-0", "fake-vllm-1"]
    assert rt.registry is not None


def test_build_runtime_fake_devices_only_disables_docker_and_process(monkeypatch):
    monkeypatch.setenv("AI_MONITOR_SERVER_URL", "http://s:8000")
    monkeypatch.setenv("AI_MONITOR_AGENT_TOKEN", "t")
    rt = build_runtime(parse_args(["--fake", "1"]))
    assert [s.name for s in rt.sources] == ["manual"]


def test_build_runtime_config_discovery_flags_and_env_override(tmp_path, monkeypatch):
    cfg = tmp_path / "agent.yaml"
    cfg.write_text(
        "server_url: http://file:8000\nagent_token: filetok\nloki_url: http://file-loki:3100\n"
        "state_dir: /tmp/from-file\ndiscovery:\n  docker: false\n  process: true\n"
        "services:\n  - name: q\n    port: 8001\n"
    )
    for var in ("AI_MONITOR_SERVER_URL", "AI_MONITOR_AGENT_TOKEN", "AI_MONITOR_ADVERTISE_ADDRESS", "AI_MONITOR_LISTEN"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("AI_MONITOR_LOKI_URL", "http://env-loki:3100")
    monkeypatch.setenv("AI_MONITOR_STATE_DIR", str(tmp_path / "env-state"))
    monkeypatch.setattr("ai_monitor_agent.__main__.NvidiaCollector.available", lambda self: False)
    monkeypatch.setattr("ai_monitor_agent.__main__.AscendCollector.available", lambda self: False)
    rt = build_runtime(parse_args(["--config", str(cfg)]))
    assert rt.config.loki_url == "http://env-loki:3100"
    assert rt.config.state_dir == str(tmp_path / "env-state")
    assert [s.name for s in rt.sources] == ["manual", "process"]
    assert [s.name for s in rt.sources[0].discover()] == ["q"]
