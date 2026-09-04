import logging
from datetime import datetime, timezone

from ai_monitor_agent.discovery.docker import DockerDiscovery, container_to_service


def _attrs(
    *,
    name="qwen",
    cid="abc123",
    image="vllm/vllm-openai:latest",
    entrypoint=None,
    cmd=None,
    args=None,
    env=None,
    network_mode="bridge",
    ports=None,
    ip="172.17.0.5",
    pid=4242,
    started="2026-09-04T08:00:00.123456789Z",
    status="running",
):
    return {
        "Id": cid,
        "Name": f"/{name}",
        "Path": (entrypoint or ["python3"])[0],
        "Args": args if args is not None else (cmd or []),
        "State": {"Status": status, "Pid": pid, "StartedAt": started},
        "Config": {
            "Image": image,
            "Entrypoint": entrypoint,
            "Cmd": cmd,
            "Env": env or [],
        },
        "HostConfig": {"NetworkMode": network_mode},
        "NetworkSettings": {
            "Ports": ports or {},
            "IPAddress": ip,
            "Networks": {"bridge": {"IPAddress": ip}} if ip else {},
        },
    }


def test_host_network_uses_localhost_and_cmd_port():
    svc = container_to_service(
        _attrs(
            entrypoint=["python3", "-m", "vllm.entrypoints.openai.api_server"],
            cmd=["--model", "Qwen/Qwen2.5-7B", "--port", "8001"],
            network_mode="host",
        )
    )
    assert svc is not None
    assert svc.name == "qwen"
    assert svc.source == "docker"
    assert svc.port == 8001
    assert svc.metrics_url == "http://127.0.0.1:8001"
    assert svc.pid == 4242
    assert svc.container_id == "abc123"
    assert svc.container_name == "qwen"
    assert svc.log_source == "docker"
    expected = datetime(2026, 9, 4, 8, 0, 0, 123456, tzinfo=timezone.utc).timestamp()
    assert svc.started_at is not None and abs(svc.started_at - expected) < 0.001
    assert "vllm.entrypoints.openai.api_server" in (svc.cmdline or "")
    assert "--port 8001" in (svc.cmdline or "")


def test_port_mapping_uses_host_port():
    svc = container_to_service(
        _attrs(
            entrypoint=["vllm", "serve", "m"],
            cmd=None,
            ports={"8000/tcp": [{"HostIp": "0.0.0.0", "HostPort": "8001"}]},
        )
    )
    assert svc is not None
    assert svc.port == 8001
    assert svc.metrics_url == "http://127.0.0.1:8001"


def test_container_ip_fallback():
    svc = container_to_service(_attrs(entrypoint=["vllm", "serve", "m"], ports={}, ip="172.17.0.5"))
    assert svc is not None
    assert svc.port == 8000
    assert svc.metrics_url == "http://172.17.0.5:8000"


def test_vllm_port_env_beats_default_but_not_cmd():
    by_env = container_to_service(_attrs(entrypoint=["vllm", "serve", "m"], env=["VLLM_PORT=9000"], network_mode="host"))
    assert by_env is not None and by_env.port == 9000
    by_cmd = container_to_service(
        _attrs(entrypoint=["vllm", "serve", "m", "--port=9100"], env=["VLLM_PORT=9000"], network_mode="host")
    )
    assert by_cmd is not None and by_cmd.port == 9100


def test_image_name_alone_detects_vllm_and_env_redacted():
    svc = container_to_service(
        _attrs(image="ghcr.io/org/vllm-custom:1", entrypoint=["/start.sh"], env=["HF_TOKEN=abc", "FOO=bar"], network_mode="host")
    )
    assert svc is not None
    assert svc.env == {"HF_TOKEN": "***", "FOO": "bar"}


def test_non_vllm_container_is_none():
    assert container_to_service(_attrs(image="nginx:latest", entrypoint=["nginx", "-g", "daemon off;"])) is None


def test_not_running_container_is_none():
    assert container_to_service(_attrs(entrypoint=["vllm", "serve", "m"], status="exited")) is None


class _FakeContainer:
    def __init__(self, attrs):
        self.attrs = attrs


class _FakeContainers:
    def __init__(self, items):
        self._items = items

    def list(self, **kw):
        return [_FakeContainer(a) for a in self._items]


class _FakeClient:
    def __init__(self, items, ping_ok=True):
        self.containers = _FakeContainers(items)
        self._ping_ok = ping_ok

    def ping(self):
        if not self._ping_ok:
            raise ConnectionError("no socket")
        return True


def test_discovery_lists_running_vllm_containers_and_tracks_pids():
    client = _FakeClient(
        [
            _attrs(name="a", cid="1", entrypoint=["vllm", "serve", "m"], network_mode="host", pid=11),
            _attrs(name="web", cid="2", image="nginx", entrypoint=["nginx"], pid=22),
            _attrs(
                name="b",
                cid="3",
                entrypoint=["vllm", "serve", "m", "--port", "8002"],
                ports={"8002/tcp": [{"HostIp": "0.0.0.0", "HostPort": "18002"}]},
                pid=33,
            ),
        ]
    )
    disc = DockerDiscovery(client_factory=lambda: client)
    assert disc.name == "docker"
    assert disc.available() is True
    services = disc.discover()
    assert [s.name for s in services] == ["a", "b"]
    assert services[1].port == 18002
    assert disc.last_pids == {11, 33}


def test_discovery_disabled_when_client_unavailable(caplog):
    def factory():
        raise FileNotFoundError("/var/run/docker.sock")

    disc = DockerDiscovery(client_factory=factory)
    with caplog.at_level(logging.WARNING):
        assert disc.available() is False
        assert disc.available() is False  # second call must not re-warn
    assert disc.discover() == []
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1


def test_discovery_disabled_when_ping_fails():
    disc = DockerDiscovery(client_factory=lambda: _FakeClient([], ping_ok=False))
    assert disc.available() is False
    assert disc.discover() == []
