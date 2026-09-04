import asyncio
import json

import httpx
import pytest
import respx

from ai_monitor_agent import __version__
from ai_monitor_agent.config import AgentConfig
from ai_monitor_agent.heartbeat import HeartbeatClient, detect_ip

SERVER = "http://server.test:8000"


def _config(**overrides) -> AgentConfig:
    base = dict(server_url=SERVER, agent_token="tok-123", heartbeat_interval_seconds=1)
    base.update(overrides)
    return AgentConfig(**base)


def _inventory():
    return {"hardware_vendor": "nvidia", "device_count": 8, "services": []}


def test_detect_ip_returns_string():
    ip = detect_ip()
    assert isinstance(ip, str)
    assert ip.count(".") == 3


def test_detect_ip_fallback(monkeypatch):
    import socket

    class BrokenSocket:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def connect(self, addr):
            raise OSError("network down")

    monkeypatch.setattr(socket, "socket", BrokenSocket)
    assert detect_ip() == "127.0.0.1"


@respx.mock
async def test_send_once_posts_expected_body(monkeypatch):
    monkeypatch.setattr("ai_monitor_agent.heartbeat.detect_ip", lambda: "10.0.0.11")
    route = respx.post(f"{SERVER}/api/agents/heartbeat").mock(return_value=httpx.Response(204))

    client = HeartbeatClient(_config(), host="gpu-01", inventory=_inventory)
    async with httpx.AsyncClient() as http:
        ok = await client.send_once(http)

    assert ok is True
    assert route.called
    request = route.calls.last.request
    assert request.headers["Authorization"] == "Bearer tok-123"
    body = json.loads(request.content)
    assert body == {
        "host": "gpu-01",
        "ip": "10.0.0.11",
        "listen_port": 9400,
        "advertise_address": "10.0.0.11:9400",
        "agent_version": __version__,
        "hardware_vendor": "nvidia",
        "device_count": 8,
        "services": [],
    }


@respx.mock
async def test_send_once_uses_configured_advertise_address(monkeypatch):
    monkeypatch.setattr("ai_monitor_agent.heartbeat.detect_ip", lambda: "10.0.0.11")
    route = respx.post(f"{SERVER}/api/agents/heartbeat").mock(return_value=httpx.Response(200))
    cfg = _config(advertise_address="agent-fake:9400", listen="0.0.0.0:9500")
    client = HeartbeatClient(cfg, host="fake", inventory=_inventory)
    async with httpx.AsyncClient() as http:
        assert await client.send_once(http) is True
    body = json.loads(route.calls.last.request.content)
    assert body["advertise_address"] == "agent-fake:9400"
    assert body["listen_port"] == 9500


@respx.mock
async def test_send_once_handles_server_error_without_raising(caplog):
    respx.post(f"{SERVER}/api/agents/heartbeat").mock(return_value=httpx.Response(500, text="boom"))
    client = HeartbeatClient(_config(), host="gpu-01", inventory=_inventory)
    async with httpx.AsyncClient() as http:
        with caplog.at_level("WARNING"):
            ok = await client.send_once(http)
    assert ok is False
    assert any("heartbeat" in r.getMessage().lower() for r in caplog.records)


@respx.mock
async def test_send_once_handles_connection_error(caplog):
    respx.post(f"{SERVER}/api/agents/heartbeat").mock(side_effect=httpx.ConnectError("refused"))
    client = HeartbeatClient(_config(), host="gpu-01", inventory=_inventory)
    async with httpx.AsyncClient() as http:
        with caplog.at_level("WARNING"):
            ok = await client.send_once(http)
    assert ok is False


@respx.mock
async def test_send_once_handles_inventory_exception(caplog):
    route = respx.post(f"{SERVER}/api/agents/heartbeat").mock(return_value=httpx.Response(204))

    def bad_inventory():
        raise RuntimeError("inventory broke")

    client = HeartbeatClient(_config(), host="gpu-01", inventory=bad_inventory)
    async with httpx.AsyncClient() as http:
        with caplog.at_level("WARNING"):
            ok = await client.send_once(http)
    assert ok is False
    assert not route.called


@respx.mock
async def test_run_loops_until_stopped():
    route = respx.post(f"{SERVER}/api/agents/heartbeat").mock(return_value=httpx.Response(204))
    cfg = _config(heartbeat_interval_seconds=1)
    client = HeartbeatClient(cfg, host="gpu-01", inventory=_inventory)
    client.interval = 0.05  # speed up for test

    stop = asyncio.Event()
    task = asyncio.create_task(client.run(stop))
    await asyncio.sleep(0.2)
    stop.set()
    await asyncio.wait_for(task, timeout=2)
    assert route.call_count >= 2


@respx.mock
async def test_run_survives_cancellation():
    respx.post(f"{SERVER}/api/agents/heartbeat").mock(return_value=httpx.Response(204))
    client = HeartbeatClient(_config(), host="gpu-01", inventory=_inventory)
    stop = asyncio.Event()
    task = asyncio.create_task(client.run(stop))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
