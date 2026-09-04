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
