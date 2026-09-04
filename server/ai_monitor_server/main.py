import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, select

from . import models  # noqa: F401  (register tables on Base.metadata)
from .agents.router import router as agents_router
from .auth.router import router as auth_router
from .auth.security import hash_password
from .config import Settings, get_settings
from .db import Base, make_engine, make_sessionmaker
from .metrics.router import router as metrics_router
from .metrics.vm_client import VMClient
from .models import User

log = logging.getLogger(__name__)


async def ensure_admin(sessionmaker, settings: Settings) -> None:
    async with sessionmaker() as session:
        count = (await session.execute(select(func.count(User.id)))).scalar_one()
        if count == 0:
            session.add(
                User(
                    username=settings.admin_username,
                    password_hash=hash_password(settings.admin_password),
                    role="admin",
                )
            )
            await session.commit()
            log.info("created initial admin user %r", settings.admin_username)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = make_engine(settings.database_url)
        app.state.engine = engine
        app.state.sessionmaker = make_sessionmaker(engine)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await ensure_admin(app.state.sessionmaker, settings)
        try:
            yield
        finally:
            await app.state.vm_client.aclose()
            await engine.dispose()

    app = FastAPI(title="ai-monitor-server", lifespan=lifespan)
    app.state.settings = settings
    app.state.vm_client = VMClient(settings.vm_url)

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    @app.get("/api/health")
    async def health() -> dict:
        return {"status": "ok"}

    app.include_router(auth_router)
    app.include_router(agents_router)
    app.include_router(metrics_router)
    return app


app = create_app()
