from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from ai_monitor_server.models import Agent, Service

SVC_A = {
    "name": "qwen-72b",
    "source": "docker",
    "port": 8001,
    "metrics_url": "http://127.0.0.1:8001",
    "pid": 4242,
    "container_id": "abc123",
    "container_name": "qwen-72b",
    "log_source": "docker",
    "log_path": None,
    "profiler_dir": "/data/profile/qwen-72b",
    "model": "Qwen/Qwen2.5-72B-Instruct",
    "vllm_version": "0.11.0",
    "started_at": 1_757_000_000.5,
    "cmdline": "python -m vllm.entrypoints.openai.api_server --port 8001",
    "cwd": "/workspace",
    "env": {"HF_TOKEN": "***", "PATH": "/usr/bin"},
    "scrape_ok": True,
    "some_future_field": "ignored",
}

SVC_B = {
    "name": "vllm-8002",
    "source": "process",
    "port": 8002,
    "metrics_url": "http://127.0.0.1:8002",
    "pid": 5151,
    "scrape_ok": True,
}


def heartbeat(services, host="gpu-01"):
    return {
        "host": host,
        "ip": "10.0.0.11",
        "listen_port": 9400,
        "advertise_address": "10.0.0.11:9400",
        "agent_version": "0.2.0",
        "hardware_vendor": "nvidia",
        "device_count": 8,
        "services": services,
    }


async def register(client, admin_headers, host="gpu-01"):
    resp = await client.post("/api/agents", json={"host": host}, headers=admin_headers)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    return body["id"], {"Authorization": f"Bearer {body['token']}"}


async def beat(client, agent_headers, services, host="gpu-01"):
    resp = await client.post(
        "/api/agents/heartbeat", json=heartbeat(services, host), headers=agent_headers
    )
    assert resp.status_code == 204, resp.text


async def test_heartbeat_creates_running_services(client, admin_headers):
    agent_id, agent_headers = await register(client, admin_headers)
    await beat(client, agent_headers, [SVC_A, SVC_B])

    resp = await client.get("/api/services", headers=admin_headers)
    assert resp.status_code == 200, resp.text
    items = resp.json()
    assert [i["name"] for i in items] == ["qwen-72b", "vllm-8002"]
    assert all(i["status"] == "running" for i in items)
    assert all(i["active"] is True for i in items)
    assert all(i["agent_id"] == agent_id for i in items)
    assert all(i["host"] == "gpu-01" for i in items)

    a = items[0]
    assert a["source"] == "docker"
    assert a["port"] == 8001
    assert a["metrics_url"] == "http://127.0.0.1:8001"
    assert a["pid"] == 4242
    assert a["container_name"] == "qwen-72b"
    assert a["model"] == "Qwen/Qwen2.5-72B-Instruct"
    assert a["vllm_version"] == "0.11.0"
    assert a["log_source"] == "docker"
    assert a["scrape_ok"] is True
    assert a["last_seen_at"] is not None
    started = datetime.fromisoformat(a["started_at"])
    assert started == datetime.fromtimestamp(1_757_000_000.5, tz=timezone.utc)
    # ServiceOut must not leak detail-only fields
    for key in ("cmdline", "env", "container_id", "log_path", "profiler_dir", "first_seen_at"):
        assert key not in a

    b = items[1]
    assert b["log_source"] == "none"
    assert b["started_at"] is None
    assert b["model"] is None


async def test_missing_service_becomes_stopped_and_active_filter(client, admin_headers):
    _, agent_headers = await register(client, admin_headers)
    await beat(client, agent_headers, [SVC_A, SVC_B])
    await beat(client, agent_headers, [SVC_A])

    resp = await client.get("/api/services", headers=admin_headers)
    items = {i["name"]: i for i in resp.json()}
    assert len(items) == 2
    assert items["qwen-72b"]["status"] == "running"
    assert items["qwen-72b"]["active"] is True
    assert items["vllm-8002"]["status"] == "stopped"
    assert items["vllm-8002"]["active"] is False

    resp = await client.get("/api/services", params={"active": "true"}, headers=admin_headers)
    assert [i["name"] for i in resp.json()] == ["qwen-72b"]

    resp = await client.get("/api/services", params={"active": "false"}, headers=admin_headers)
    assert [i["name"] for i in resp.json()] == ["vllm-8002"]

    resp = await client.get("/api/services", params={"active": "maybe"}, headers=admin_headers)
    assert resp.status_code == 422


async def test_reappearing_service_becomes_active_again(client, admin_headers):
    _, agent_headers = await register(client, admin_headers)
    await beat(client, agent_headers, [SVC_A, SVC_B])
    await beat(client, agent_headers, [SVC_A])
    await beat(client, agent_headers, [SVC_A, SVC_B])

    resp = await client.get("/api/services", headers=admin_headers)
    items = resp.json()
    assert len(items) == 2  # upsert by (agent, name): no duplicate rows
    assert all(i["status"] == "running" for i in items)


async def test_empty_services_list_deactivates_all(client, admin_headers):
    _, agent_headers = await register(client, admin_headers)
    await beat(client, agent_headers, [SVC_A, SVC_B])
    await beat(client, agent_headers, [])

    resp = await client.get("/api/services", headers=admin_headers)
    items = resp.json()
    assert len(items) == 2
    assert all(i["status"] == "stopped" and i["active"] is False for i in items)


async def test_scrape_failure_is_degraded(client, admin_headers):
    _, agent_headers = await register(client, admin_headers)
    await beat(client, agent_headers, [{**SVC_A, "scrape_ok": False}])

    resp = await client.get("/api/services", headers=admin_headers)
    item = resp.json()[0]
    assert item["scrape_ok"] is False
    assert item["status"] == "degraded"


async def test_offline_agent_makes_services_unknown(client, admin_headers, app):
    agent_id, agent_headers = await register(client, admin_headers)
    await beat(client, agent_headers, [SVC_A, {**SVC_B, "scrape_ok": False}])

    async with app.state.sessionmaker() as session:
        agent = (await session.execute(select(Agent).where(Agent.id == agent_id))).scalar_one()
        agent.last_seen_at = datetime.now(timezone.utc) - timedelta(minutes=10)
        await session.commit()

    resp = await client.get("/api/services", headers=admin_headers)
    items = resp.json()
    assert len(items) == 2
    assert all(i["status"] == "unknown" for i in items)


async def test_service_detail(client, admin_headers, viewer_headers):
    _, agent_headers = await register(client, admin_headers)
    await beat(client, agent_headers, [SVC_A])
    listed = (await client.get("/api/services", headers=admin_headers)).json()[0]

    resp = await client.get(f"/api/services/{listed['id']}", headers=viewer_headers)
    assert resp.status_code == 200, resp.text
    d = resp.json()
    assert d["id"] == listed["id"]
    assert d["status"] == "running"
    assert d["cmdline"] == SVC_A["cmdline"]
    assert d["cwd"] == "/workspace"
    assert d["env"] == {"HF_TOKEN": "***", "PATH": "/usr/bin"}
    assert d["container_id"] == "abc123"
    assert d["log_path"] is None
    assert d["profiler_dir"] == "/data/profile/qwen-72b"
    first_seen = datetime.fromisoformat(d["first_seen_at"])
    assert first_seen.tzinfo is not None
    assert "some_future_field" not in d


async def test_first_seen_preserved_on_update(client, admin_headers):
    _, agent_headers = await register(client, admin_headers)
    await beat(client, agent_headers, [SVC_A])
    d1 = (await client.get("/api/services", headers=admin_headers)).json()[0]
    detail1 = (await client.get(f"/api/services/{d1['id']}", headers=admin_headers)).json()

    await beat(client, agent_headers, [{**SVC_A, "vllm_version": "0.12.0", "pid": 9}])
    detail2 = (await client.get(f"/api/services/{d1['id']}", headers=admin_headers)).json()
    assert detail2["first_seen_at"] == detail1["first_seen_at"]
    assert detail2["vllm_version"] == "0.12.0"
    assert detail2["pid"] == 9
    assert detail2["last_seen_at"] >= detail1["last_seen_at"]


async def test_service_detail_404(client, admin_headers):
    resp = await client.get("/api/services/9999", headers=admin_headers)
    assert resp.status_code == 404


async def test_services_require_login(client):
    assert (await client.get("/api/services")).status_code == 401
    assert (await client.get("/api/services/1")).status_code == 401
    assert (await client.get("/api/agents/1/services")).status_code == 401


async def test_agent_services_endpoint(client, admin_headers):
    id1, h1 = await register(client, admin_headers, host="gpu-01")
    id2, h2 = await register(client, admin_headers, host="gpu-02")
    await beat(client, h1, [SVC_A], host="gpu-01")
    await beat(client, h2, [SVC_B], host="gpu-02")

    resp = await client.get(f"/api/agents/{id1}/services", headers=admin_headers)
    assert resp.status_code == 200
    assert [i["name"] for i in resp.json()] == ["qwen-72b"]
    assert resp.json()[0]["host"] == "gpu-01"

    resp = await client.get(f"/api/agents/{id2}/services", headers=admin_headers)
    assert [i["name"] for i in resp.json()] == ["vllm-8002"]

    # same service name on two hosts is allowed (identity is (agent, name))
    await beat(client, h2, [SVC_A, SVC_B], host="gpu-02")
    resp = await client.get("/api/services", headers=admin_headers)
    assert len(resp.json()) == 3

    resp = await client.get("/api/agents/9999/services", headers=admin_headers)
    assert resp.status_code == 404


async def test_delete_agent_cascades_services(client, admin_headers, app):
    agent_id, agent_headers = await register(client, admin_headers)
    await beat(client, agent_headers, [SVC_A, SVC_B])
    assert len((await client.get("/api/services", headers=admin_headers)).json()) == 2

    resp = await client.delete(f"/api/agents/{agent_id}", headers=admin_headers)
    assert resp.status_code == 204

    assert (await client.get("/api/services", headers=admin_headers)).json() == []
    async with app.state.sessionmaker() as session:
        rows = (await session.execute(select(Service))).scalars().all()
    assert rows == []
