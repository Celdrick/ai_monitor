import asyncio

import httpx
import respx

from ai_monitor_server.agents.tokens import decrypt_agent_token, encrypt_agent_token, hash_agent_token
from ai_monitor_server.models import Agent


async def _register_online(client, admin_headers, settings, host="gpu-01"):
    created = await client.post("/api/agents", headers=admin_headers, json={"host": host})
    assert created.status_code == 201, created.text
    token = created.json()["token"]
    hb = await client.post(
        "/api/agents/heartbeat",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "host": host,
            "ip": "10.0.0.11",
            "listen_port": 9400,
            "advertise_address": "10.0.0.11:9400",
            "agent_version": "0.1.0",
            "hardware_vendor": "nvidia",
            "device_count": 1,
            "services": [
                {
                    "name": "vllm-0",
                    "source": "manual",
                    "port": 8000,
                    "metrics_url": "http://127.0.0.1:8000",
                    "scrape_ok": True,
                    "log_source": "none",
                }
            ],
        },
    )
    assert hb.status_code == 204
    services = await client.get("/api/services", headers=admin_headers)
    svc = next(s for s in services.json() if s["name"] == "vllm-0")
    return token, svc["id"], created.json()["id"]


def test_encrypt_roundtrip():
    secret = "x" * 32
    token = "abc"
    enc = encrypt_agent_token(token, secret)
    assert enc != token
    assert decrypt_agent_token(enc, secret) == token
    assert hash_agent_token(token)


async def test_create_stores_token_enc(client, admin_headers, app, settings):
    created = await client.post("/api/agents", headers=admin_headers, json={"host": "h1"})
    token = created.json()["token"]
    async with app.state.sessionmaker() as session:
        agent = await session.get(Agent, created.json()["id"])
        assert agent.token_enc
        assert decrypt_agent_token(agent.token_enc, settings.jwt_secret) == token


async def test_viewer_cannot_create_debug_task(client, viewer_headers):
    r = await client.post(
        "/api/debug/tasks",
        headers=viewer_headers,
        json={"service_id": 1, "type": "pyspy_dump"},
    )
    assert r.status_code == 403


async def test_offline_agent_409(client, admin_headers, settings):
    created = await client.post("/api/agents", headers=admin_headers, json={"host": "off"})
    # no heartbeat
    r = await client.post(
        "/api/debug/tasks",
        headers=admin_headers,
        json={"service_id": 1, "type": "pyspy_dump"},
    )
    assert r.status_code in (404, 409)


@respx.mock
async def test_debug_task_poll_and_download(client, admin_headers, settings):
    respx.route(host="10.0.0.11").mock(side_effect=_agent_side_effect)
    token, svc_id, _agent_id = await _register_online(client, admin_headers, settings)
    r = await client.post(
        "/api/debug/tasks",
        headers=admin_headers,
        json={"service_id": svc_id, "type": "pyspy_dump"},
    )
    assert r.status_code == 202, r.text
    task_id = r.json()["id"]
    body = None
    for _ in range(40):
        got = await client.get(f"/api/debug/tasks/{task_id}", headers=admin_headers)
        body = got.json()
        if body["status"] in ("succeeded", "failed"):
            break
        await asyncio.sleep(0.05)
    assert body["status"] == "succeeded", body
    assert body["artifacts"]
    art_id = body["artifacts"][0]["id"]
    dl = await client.get(f"/api/debug/artifacts/{art_id}", headers=admin_headers)
    assert dl.status_code == 200
    assert dl.content == b"FAKE DUMP"
    forbidden = await client.get(f"/api/debug/artifacts/{art_id}")
    assert forbidden.status_code == 401


_TASK = {"polls": 0}


def _agent_side_effect(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/control/pyspy":
        return httpx.Response(202, json={"id": "agt1", "status": "pending"})
    if path == "/control/tasks/agt1":
        _TASK["polls"] += 1
        if _TASK["polls"] < 2:
            return httpx.Response(200, json={"id": "agt1", "status": "running", "artifacts": [], "error": None})
        return httpx.Response(
            200,
            json={
                "id": "agt1",
                "status": "succeeded",
                "error": None,
                "artifacts": [{"id": "a1", "type": "pyspy_dump", "filename": "dump.txt", "size_bytes": 9}],
            },
        )
    if path == "/control/artifacts/a1":
        return httpx.Response(200, content=b"FAKE DUMP")
    if path == "/control/capabilities":
        return httpx.Response(200, json={"profile": True, "pyspy": True, "nsys": True, "msprof": True})
    return httpx.Response(404)


@respx.mock
async def test_capabilities_hides_msprof_on_nvidia(client, admin_headers, settings):
    respx.get("http://10.0.0.11:9400/control/capabilities").mock(
        return_value=httpx.Response(200, json={"profile": True, "pyspy": True, "nsys": True, "msprof": True})
    )
    _token, svc_id, _ = await _register_online(client, admin_headers, settings, host="gpu-02")
    r = await client.get(f"/api/debug/capabilities?service_id={svc_id}", headers=admin_headers)
    assert r.status_code == 200, r.text
    assert r.json()["nsys"] is True
    assert r.json()["msprof"] is False
