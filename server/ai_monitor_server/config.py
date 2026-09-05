from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AIM_", extra="ignore")

    database_url: str = "sqlite+aiosqlite:///./dev.db"
    jwt_secret: str = "change-me"
    access_token_minutes: int = 60
    refresh_token_days: int = 7
    admin_username: str = "admin"
    admin_password: str = "admin123"
    vm_url: str = "http://victoriametrics:8428"
    loki_url: str = "http://loki:3100"
    file_sd_path: str = "/file_sd/agents.json"
    agent_offline_seconds: int = 120
    cors_origins: list[str] = []
    artifacts_dir: str = "/data/artifacts"
    debug_poll_interval_seconds: float = 2.0
    artifact_max_bytes: int = 512 * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()
