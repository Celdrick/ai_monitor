import time

import httpx
import respx
from fastapi.testclient import TestClient

from ai_monitor_agent.app import create_app
from ai_monitor_agent.config import AgentConfig, ManualService
from ai_monitor_agent.control.profile import PROFILE_HINT
from ai_monitor_agent.control.runners import clamp_duration
from ai_monitor_agent.discovery.manual import ManualDiscovery
from ai_monitor_agent.discovery.registry import ServiceRegistry
from ai_monitor_agent.fake.vllm import FakeVllmServer, fake_vllm_manual_services
from ai_monitor_agent.services import ServiceInfo


def _wait(pred, timeout=8.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.05)
    return pred()


def _cfg(tmp_path, **kw):
    return AgentConfig(
        server_url="http://server.test:8000",
        agent_token="tok",
        state_dir=str(tmp_path / "state"),
        artifacts_dir=str(tmp_path / "arts"),
        **kw,
    )


def _headers():
    return {"Authorization": "Bearer tok"}


def test_clamp_duration():
    assert clamp_duration(None) == 15
    assert clamp_duration(1) == 5
    assert clamp_duration(99) == 60
    assert clamp_duration(20) == 20


def test_control_requires_token(tmp_path):
    app = create_app(_cfg(tmp_path), [], None, host="h")
    with TestClient(app) as client:
        assert client.get("/control/capabilities").status_code == 401
        assert client.get("/control/capabilities", headers=_headers()).json()["profile"] is True
        caps = client.get("/control/capabilities", headers=_headers()).json()
        assert caps["pyspy"] is False
        assert caps["nsys"] is False


def test_capabilities_fake_debug(tmp_path):
    app = create_app(_cfg(tmp_path), [], None, host="h", fake_debug=True)
    with TestClient(app) as client:
        caps = client.get("/control/capabilities", headers=_headers()).json()
        assert caps == {"profile": True, "pyspy": True, "nsys": True, "msprof": True}


def test_unknown_service_404(tmp_path):
    app = create_app(_cfg(tmp_path), [], None, host="h")
    with TestClient(app) as client:
        r = client.post("/control/pyspy", headers=_headers(), json={"service": "nope", "mode": "dump"})
        assert r.status_code == 404


def test_profile_404_message(tmp_path):
    svc = ServiceInfo(name="s", source="manual", port=1, metrics_url="http://vllm.test")

    class Fixed:
        name = "manual"

        def discover(self):
            return [svc]

    registry = ServiceRegistry([Fixed()], probe=lambda url: (None, None))
    cfg = _cfg(tmp_path, discovery={"interval_seconds": 60})
    with respx.mock(assert_all_mocked=False) as mock:
        mock.post("http://server.test:8000/api/agents/heartbeat").mock(return_value=httpx.Response(204))
        mock.post("http://vllm.test/start_profile").mock(return_value=httpx.Response(404))
        app = create_app(cfg, [], None, host="h", registry=registry)
        with TestClient(app) as client:
            r = client.post("/control/profile/start", headers=_headers(), json={"service": "s"})
            assert r.status_code == 202
            tid = r.json()["id"]
            assert _wait(lambda: client.get(f"/control/tasks/{tid}", headers=_headers()).json()["status"] == "failed")
            body = client.get(f"/control/tasks/{tid}", headers=_headers()).json()
            assert "--profiler-config" in body["error"]
            assert PROFILE_HINT.split("Launch")[0] in body["error"] or "404" in body["error"]


def test_fake_profile_and_pyspy(tmp_path):
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    state = tmp_path / "state"
    manuals = fake_vllm_manual_services(1, str(state), base_port=port)
    fake = [FakeVllmServer(0, port, manuals[0].log_path)]
    fake[0].profiler_dir = manuals[0].profiler_dir
    registry = ServiceRegistry([ManualDiscovery(manuals)])
    cfg = _cfg(tmp_path, discovery={"interval_seconds": 1})
    with respx.mock(assert_all_mocked=False) as mock:
        mock.post("http://server.test:8000/api/agents/heartbeat").mock(return_value=httpx.Response(204))
        mock.route(host="127.0.0.1").pass_through()
        app = create_app(cfg, [], None, host="h", registry=registry, fake_vllm=fake, fake_debug=True)
        with TestClient(app) as client:
            assert _wait(lambda: client.get("/healthz").json()["services"] >= 1)
            r = client.post("/control/profile/start", headers=_headers(), json={"service": "fake-vllm-0"})
            assert r.status_code == 202
            tid = r.json()["id"]
            assert _wait(lambda: client.get(f"/control/tasks/{tid}", headers=_headers()).json()["status"] == "succeeded")
            busy = client.post("/control/pyspy", headers=_headers(), json={"service": "fake-vllm-0", "mode": "dump"})
            assert busy.status_code == 409
            r = client.post("/control/profile/stop", headers=_headers(), json={"service": "fake-vllm-0"})
            tid = r.json()["id"]
            assert _wait(lambda: client.get(f"/control/tasks/{tid}", headers=_headers()).json()["status"] == "succeeded")
            body = client.get(f"/control/tasks/{tid}", headers=_headers()).json()
            assert body["artifacts"]
            aid = body["artifacts"][0]["id"]
            dl = client.get(f"/control/artifacts/{aid}", headers=_headers())
            assert dl.status_code == 200
            assert b"traceEvents" in dl.content

            r = client.post("/control/pyspy", headers=_headers(), json={"service": "fake-vllm-0", "mode": "dump"})
            tid = r.json()["id"]
            assert _wait(lambda: client.get(f"/control/tasks/{tid}", headers=_headers()).json()["status"] == "succeeded")
            dump = client.get(f"/control/tasks/{tid}", headers=_headers()).json()
            assert dump["artifacts"][0]["filename"].endswith(".txt")
