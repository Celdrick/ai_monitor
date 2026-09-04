from ai_monitor_server.auth.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)


def test_password_hash_roundtrip():
    h = hash_password("s3cret")
    assert h != "s3cret"
    assert verify_password("s3cret", h)
    assert not verify_password("wrong", h)


SECRET = "unit-test-secret-that-is-at-least-32-bytes-long"


def test_token_roundtrip():
    access = decode_token(create_access_token(7, "admin", SECRET, 60), SECRET)
    assert access["sub"] == "7"
    assert access["role"] == "admin"
    assert access["typ"] == "access"
    assert "exp" in access
    refresh = decode_token(create_refresh_token(7, SECRET, 7), SECRET)
    assert refresh["sub"] == "7"
    assert refresh["typ"] == "refresh"


async def test_login_success(client):
    resp = await client.post(
        "/api/auth/login", json={"username": "admin", "password": "admin123"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert body["refresh_token"]
    assert body["user"]["username"] == "admin"
    assert body["user"]["role"] == "admin"
    assert isinstance(body["user"]["id"], int)


async def test_login_wrong_password(client):
    resp = await client.post(
        "/api/auth/login", json={"username": "admin", "password": "nope"}
    )
    assert resp.status_code == 401


async def test_login_unknown_user(client):
    resp = await client.post(
        "/api/auth/login", json={"username": "ghost", "password": "nope"}
    )
    assert resp.status_code == 401


async def test_me_requires_token(client):
    resp = await client.get("/api/auth/me")
    assert resp.status_code == 401
    resp = await client.get("/api/auth/me", headers={"Authorization": "Bearer junk"})
    assert resp.status_code == 401


async def test_me(client, admin_headers):
    resp = await client.get("/api/auth/me", headers=admin_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["username"] == "admin"
    assert body["role"] == "admin"
    assert set(body) == {"id", "username", "role"}


async def test_refresh_returns_new_access(client):
    login = await client.post(
        "/api/auth/login", json={"username": "admin", "password": "admin123"}
    )
    refresh_token = login.json()["refresh_token"]
    resp = await client.post("/api/auth/refresh", json={"refresh_token": refresh_token})
    assert resp.status_code == 200
    body = resp.json()
    assert body["token_type"] == "bearer"
    me = await client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"}
    )
    assert me.status_code == 200


async def test_refresh_rejects_access_token(client, admin_headers):
    access = admin_headers["Authorization"].split()[1]
    resp = await client.post("/api/auth/refresh", json={"refresh_token": access})
    assert resp.status_code == 401


async def test_refresh_token_cannot_be_used_as_access(client):
    login = await client.post(
        "/api/auth/login", json={"username": "admin", "password": "admin123"}
    )
    refresh_token = login.json()["refresh_token"]
    resp = await client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {refresh_token}"}
    )
    assert resp.status_code == 401


async def test_viewer_forbidden_on_admin_endpoint(client, viewer_headers):
    resp = await client.post("/api/agents", json={"host": "x"}, headers=viewer_headers)
    assert resp.status_code == 403
