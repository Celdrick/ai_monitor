import json
import os
import re
import socket
import time

import httpx
import respx
from fastapi.testclient import TestClient
from prometheus_client import CONTENT_TYPE_LATEST

from ai_monitor_agent.app import create_app
from ai_monitor_agent.collectors.fake import FakeCollector
from ai_monitor_agent.collectors.host import HostCollector
from ai_monitor_agent.config import AgentConfig
from ai_monitor_agent.discovery.manual import ManualDiscovery
from ai_monitor_agent.discovery.registry import ServiceRegistry
from ai_monitor_agent.fake.vllm import FakeVllmServer, fake_vllm_manual_services
from ai_monitor_agent.services import ServiceInfo

SERVER = "http://server.test:8000"
LOKI = "http://loki.test:3100"


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


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait(pred, timeout=8.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.05)
    return pred()


def test_healthz_reports_services_and_logs_flags(tmp_path):
    with respx.mock(assert_all_mocked=False, assert_all_called=False) as mock:
        mock.post(f"{SERVER}/api/agents/heartbeat").mock(return_value=httpx.Response(204))
        cfg = _config(state_dir=str(tmp_path / "state"))
        app = create_app(cfg, device_collectors=[], host_collector=None, host="h")
        with TestClient(app) as client:
            body = client.get("/healthz").json()
            assert body["services"] == 0
            assert body["logs"] is False  # no loki_url → logs disabled
            assert client.get("/metrics").text.count('agent_vllm_services{host="h"} 0.0') == 1
        assert (tmp_path / "state").is_dir()  # state_dir auto-created


def test_app_with_fake_vllm_end_to_end(tmp_path):
    port = _free_port()
    state_dir = str(tmp_path / "state")
    fake_services = fake_vllm_manual_services(1, state_dir, base_port=port)
    fake = [FakeVllmServer(0, port, fake_services[0].log_path)]
    fake[0].log_interval = 0.05
    registry = ServiceRegistry([ManualDiscovery(fake_services)])
    cfg = _config(
        loki_url=LOKI,
        state_dir=state_dir,
        scrape_interval_seconds=1,
        discovery={"docker": False, "process": False, "interval_seconds": 1},
        logs={"batch_interval_seconds": 0.1},
    )
    with respx.mock(assert_all_mocked=False, assert_all_called=False) as mock:
        hb = mock.post(f"{SERVER}/api/agents/heartbeat").mock(return_value=httpx.Response(204))
        loki = mock.post(f"{LOKI}/loki/api/v1/push").mock(return_value=httpx.Response(204))
        mock.route(host="127.0.0.1").pass_through()
        app = create_app(cfg, device_collectors=[FakeCollector("nvidia", 1)], host_collector=None, host="h", registry=registry, fake_vllm=fake)
        with TestClient(app) as client:
            assert _wait(lambda: 'service="fake-vllm-0"' in client.get("/metrics").text)
            text = client.get("/metrics").text
            assert re.search(r'^vllm:num_requests_running\{host="h",service="fake-vllm-0",model_name="[^"]+"\} [0-9.]+$', text, re.M), text
            assert "vllm:e2e_request_latency_seconds_bucket{" in text
            assert 'agent_vllm_scrape_success{host="h",service="fake-vllm-0"} 1.0' in text
            assert 'agent_vllm_services{host="h"} 1.0' in text
            assert "accel_util_percent{" in text  # hardware metrics still present
            assert "python_" not in text
            assert text.count("# TYPE vllm:num_requests_running gauge") == 1

            health = client.get("/healthz").json()
            assert health["services"] == 1
            assert health["logs"] is True

            # heartbeat carries the discovered service
            def hb_has_service():
                for call in hb.calls:
                    body = json.loads(call.request.content)
                    if body["services"]:
                        return True
                return False

            assert _wait(hb_has_service)
            body = next(json.loads(c.request.content) for c in hb.calls if json.loads(c.request.content)["services"])
            svc = body["services"][0]
            assert svc["name"] == "fake-vllm-0"
            assert svc["source"] == "manual"
            assert svc["port"] == port
            assert svc["log_source"] == "file"
            assert svc["log_path"] == fake_services[0].log_path
            assert set(svc) == set(ServiceInfo.__dataclass_fields__)

            # logs from the fake's file reach Loki with host/service/source/level labels
            assert _wait(lambda: loki.called)
            payload = json.loads(loki.calls.last.request.content)
            stream = payload["streams"][0]["stream"]
            assert stream["host"] == "h" and stream["service"] == "fake-vllm-0" and stream["source"] == "file"
            assert stream["level"] in ("info", "warning", "error")
            assert os.path.exists(os.path.join(state_dir, "fake-vllm-0.log"))
    # cursors persisted at shutdown
    assert os.path.exists(os.path.join(state_dir, "log_cursors.json"))
    # model/version probed from the fake
    assert svc["model"] == "fake/Qwen2.5-7B-Instruct" or registry.snapshot()[0].model == "fake/Qwen2.5-7B-Instruct"
