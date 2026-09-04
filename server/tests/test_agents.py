import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from ai_monitor_server.agents.tokens import generate_agent_token, hash_agent_token
from ai_monitor_server.models import Agent

HEARTBEAT = {
    "host": "gpu-01",
    "ip": "10.0.0.11",
    "listen_port": 9400,
    "advertise_address": "10.0.0.11:9400",
    "agent_version": "0.1.0",
    "hardware_vendor": "nvidia",
    "device_count": 8,
    "services": [],
}


def test_token_helpers():
    t1 = generate_agent_token()
    t2 = generate_agent_token()
    assert t1 != t2
    assert len(t1) >= 32
    h = hash_agent_token(t1)
    assert len(h) == 64
    assert h == hash_agent_token(t1)
    assert h != hash_agent_token(t2)


async def create_agent(client, admin_headers, host="gpu-01"):
    resp = await client.post("/api/agents", json={"host": host}, headers=admin_headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def test_create_agent(client, admin_headers):
    body = await create_agent(client, admin_headers)
    assert set(body) == {"id", "host", "token"}
    assert body["host"] == "gpu-01"
    assert body["token"]


async def test_create_agent_requires_auth(client):
    resp = await client.post("/api/agents", json={"host": "gpu-01"})
    assert resp.status_code == 401


async def test_create_duplicate_host_conflict(client, admin_headers):
    await create_agent(client, admin_headers)
    resp = await client.post("/api/agents", json={"host": "gpu-01"}, headers=admin_headers)
    assert resp.status_code == 409


async def test_list_never_before_heartbeat(client, admin_headers):
    created = await create_agent(client, admin_headers)
    resp = await client.get("/api/agents", headers=admin_headers)
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 1
    item = items[0]
    assert item["id"] == created["id"]
    assert item["status"] == "never"
    assert item["last_seen_at"] is None
    assert item["device_count"] == 0
    assert "token" not in item
    assert "token_hash" not in item


async def test_heartbeat_wrong_token(client, admin_headers):
    await create_agent(client, admin_headers)
    resp = await client.post(
        "/api/agents/heartbeat",
        json=HEARTBEAT,
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    assert resp.status_code == 401
    resp = await client.post("/api/agents/heartbeat", json=HEARTBEAT)
    assert resp.status_code == 401


async def test_heartbeat_with_jwt_rejected(client, admin_headers):
    """A user JWT is not an agent token."""
    await create_agent(client, admin_headers)
    resp = await client.post("/api/agents/heartbeat", json=HEARTBEAT, headers=admin_headers)
    assert resp.status_code == 401


async def test_heartbeat_updates_and_online(client, admin_headers, settings):
    created = await create_agent(client, admin_headers)
    resp = await client.post(
        "/api/agents/heartbeat",
        json=HEARTBEAT,
        headers={"Authorization": f"Bearer {created['token']}"},
    )
    assert resp.status_code == 204

    resp = await client.get(f"/api/agents/{created['id']}", headers=admin_headers)
    assert resp.status_code == 200
    item = resp.json()
    assert item["status"] == "online"
    assert item["ip"] == "10.0.0.11"
    assert item["advertise_address"] == "10.0.0.11:9400"
    assert item["version"] == "0.1.0"
    assert item["hardware_vendor"] == "nvidia"
    assert item["device_count"] == 8
    assert item["last_seen_at"] is not None
    datetime.fromisoformat(item["last_seen_at"])

    # file_sd written
    with open(settings.file_sd_path) as f:
        data = json.load(f)
    assert data == [{"targets": ["10.0.0.11:9400"], "labels": {"host": "gpu-01"}}]


async def test_heartbeat_visible_to_viewer(client, admin_headers, viewer_headers):
    created = await create_agent(client, admin_headers)
    await client.post(
        "/api/agents/heartbeat",
        json=HEARTBEAT,
        headers={"Authorization": f"Bearer {created['token']}"},
    )
    resp = await client.get("/api/agents", headers=viewer_headers)
    assert resp.status_code == 200
    assert resp.json()[0]["status"] == "online"


async def test_offline_after_timeout(client, admin_headers, app):
    created = await create_agent(client, admin_headers)
    await client.post(
        "/api/agents/heartbeat",
        json=HEARTBEAT,
        headers={"Authorization": f"Bearer {created['token']}"},
    )
    async with app.state.sessionmaker() as session:
        agent = (
            await session.execute(select(Agent).where(Agent.id == created["id"]))
        ).scalar_one()
        agent.last_seen_at = datetime.now(timezone.utc) - timedelta(minutes=5)
        await session.commit()

    resp = await client.get("/api/agents", headers=admin_headers)
    assert resp.json()[0]["status"] == "offline"


async def test_get_agent_404(client, admin_headers):
    resp = await client.get("/api/agents/9999", headers=admin_headers)
    assert resp.status_code == 404


async def test_rotate_token(client, admin_headers):
    created = await create_agent(client, admin_headers)
    resp = await client.post(
        f"/api/agents/{created['id']}/rotate-token", headers=admin_headers
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == created["id"]
    assert body["host"] == "gpu-01"
    assert body["token"] != created["token"]

    old = await client.post(
        "/api/agents/heartbeat",
        json=HEARTBEAT,
        headers={"Authorization": f"Bearer {created['token']}"},
    )
    assert old.status_code == 401
    new = await client.post(
        "/api/agents/heartbeat",
        json=HEARTBEAT,
        headers={"Authorization": f"Bearer {body['token']}"},
    )
    assert new.status_code == 204


async def test_rotate_token_404(client, admin_headers):
    resp = await client.post("/api/agents/9999/rotate-token", headers=admin_headers)
    assert resp.status_code == 404


async def test_rotate_token_viewer_forbidden(client, admin_headers, viewer_headers):
    created = await create_agent(client, admin_headers)
    resp = await client.post(
        f"/api/agents/{created['id']}/rotate-token", headers=viewer_headers
    )
    assert resp.status_code == 403


async def test_delete_agent(client, admin_headers):
    created = await create_agent(client, admin_headers)
    resp = await client.delete(f"/api/agents/{created['id']}", headers=admin_headers)
    assert resp.status_code == 204
    resp = await client.get(f"/api/agents/{created['id']}", headers=admin_headers)
    assert resp.status_code == 404
    resp = await client.delete(f"/api/agents/{created['id']}", headers=admin_headers)
    assert resp.status_code == 404


async def test_delete_agent_viewer_forbidden(client, admin_headers, viewer_headers):
    created = await create_agent(client, admin_headers)
    resp = await client.delete(f"/api/agents/{created['id']}", headers=viewer_headers)
    assert resp.status_code == 403
