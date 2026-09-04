import json

import httpx
import respx
from fastapi.testclient import TestClient
from prometheus_client import CONTENT_TYPE_LATEST

from ai_monitor_agent.app import create_app
from ai_monitor_agent.collectors.fake import FakeCollector
from ai_monitor_agent.collectors.host import HostCollector
from ai_monitor_agent.config import AgentConfig

SERVER = "http://server.test:8000"


def _config(**overrides) -> AgentConfig:
    base = dict(server_url=SERVER, agent_token="tok", heartbeat_interval_seconds=1)
    base.update(overrides)
    return AgentConfig(**base)


class BrokenCollector:
    name = "broken"

    def available(self):
        return True

    def collect(self):
        raise RuntimeError("nope")


@respx.mock
def test_metrics_and_healthz():
    respx.post(f"{SERVER}/api/agents/heartbeat").mock(return_value=httpx.Response(204))
    app = create_app(
        _config(),
        device_collectors=[FakeCollector("nvidia", 2, seed=3), BrokenCollector()],
        host_collector=HostCollector(),
        host="h1",
    )
    with TestClient(app) as client:
        r = client.get("/metrics")
        assert r.status_code == 200
        assert r.headers["content-type"] == CONTENT_TYPE_LATEST
        assert "accel_util_percent{" in r.text
        assert 'host="h1"' in r.text
        assert "host_cpu_percent" in r.text
        assert 'agent_collector_up{collector="broken"} 0.0' in r.text
        assert "python_info" not in r.text

        h = client.get("/healthz")
        assert h.status_code == 200
        body = h.json()
        assert body["status"] == "ok"
        assert body["host"] == "h1"
        assert body["collectors"] == {"fake-nvidia": True, "broken": True, "host": True}


@respx.mock
def test_lifespan_starts_heartbeat(monkeypatch):
    monkeypatch.setattr("ai_monitor_agent.heartbeat.detect_ip", lambda: "10.1.1.1")
    route = respx.post(f"{SERVER}/api/agents/heartbeat").mock(return_value=httpx.Response(204))
    app = create_app(
        _config(),
        device_collectors=[FakeCollector("ascend", 3, seed=1)],
        host_collector=None,
        host="npu-01",
    )
    with TestClient(app) as client:
        client.get("/healthz")
    assert route.called
    body = json.loads(route.calls.last.request.content)
    assert body["host"] == "npu-01"
    assert body["hardware_vendor"] == "ascend"
    assert body["device_count"] == 3
    assert body["services"] == []
    assert body["advertise_address"] == "10.1.1.1:9400"
    assert app.state.heartbeat_task.done()


@respx.mock
def test_inventory_vendor_none_without_devices():
    route = respx.post(f"{SERVER}/api/agents/heartbeat").mock(return_value=httpx.Response(204))
    app = create_app(_config(), device_collectors=[], host_collector=None, host="cpu-only")
    with TestClient(app):
        pass
    body = json.loads(route.calls.last.request.content)
    assert body["hardware_vendor"] == "none"
    assert body["device_count"] == 0
