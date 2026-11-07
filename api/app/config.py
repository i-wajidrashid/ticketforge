"""Checkout service configuration (12-factor, environment driven)."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Values come from process env (docker compose env_file); we deliberately do
    # not read .env here so host-side tests and containers cannot drift.
    model_config = SettingsConfigDict(extra="ignore", case_sensitive=False)

    mysql_host: str = "127.0.0.1"
    mysql_port: int = 3306
    mysql_user: str = "ticketforge"
    mysql_password: str = "ticketforge"
    mysql_database: str = "core"
    tf_core_database: str | None = None
    checkout_database: str = "checkout"

    jwt_secret: str = "dev-jwt-secret-change-me-please-32bytes"
    qr_secret: str = "dev-qr-secret-change-me-please-32bytes"

    redis_url: str = "redis://127.0.0.1:6379/0"
    stripe_secret_key: str = "sk_test_xxx"
    stripe_webhook_secret: str = "whsec_xxx"
    stripe_gateway: str = "auto"

    hold_ttl_minutes: int = 10
    hold_rate_limit_per_minute: int = 10
    max_active_holds_per_event: int = 1

    site_origin: str = "https://localhost"

    @property
    def checkout_url(self) -> str:
        return (
            f"mysql+pymysql://{self.mysql_user}:{self.mysql_password}"
            f"@{self.mysql_host}:{self.mysql_port}/{self.checkout_database}?charset=utf8mb4"
        )

    @property
    def core_url(self) -> str:
        return (
            f"mysql+pymysql://{self.mysql_user}:{self.mysql_password}"
            f"@{self.mysql_host}:{self.mysql_port}/{self.tf_core_database or self.mysql_database}?charset=utf8mb4"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
