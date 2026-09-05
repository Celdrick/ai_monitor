import pytest
from httpx import ASGITransport, AsyncClient

from ai_monitor_server.auth.security import hash_password
from ai_monitor_server.config import Settings
from ai_monitor_server.main import create_app
from ai_monitor_server.models import User


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        jwt_secret="test-secret-that-is-at-least-32-bytes-long!!",
        admin_username="admin",
        admin_password="admin123",
        vm_url="http://vm.test:8428",
        loki_url="http://loki.test:3100",
        file_sd_path=str(tmp_path / "file_sd" / "agents.json"),
        artifacts_dir=str(tmp_path / "artifacts"),
        debug_poll_interval_seconds=0.05,
        agent_offline_seconds=120,
    )


@pytest.fixture
def app(settings):
    return create_app(settings)


@pytest.fixture
async def client(app):
    # Drive the lifespan manually so startup (create_all + admin) runs.
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


async def _login(client, username: str, password: str) -> dict:
    resp = await client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.fixture
async def admin_headers(client, settings):
    return await _login(client, settings.admin_username, settings.admin_password)


@pytest.fixture
async def viewer_headers(client, app):
    async with app.state.sessionmaker() as session:
        session.add(
            User(
                username="viewer",
                password_hash=hash_password("viewer123"),
                role="viewer",
            )
        )
        await session.commit()
    return await _login(client, "viewer", "viewer123")
