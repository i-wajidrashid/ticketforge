"""Database URL helpers shared by the checkout service and read-only consumers."""

from __future__ import annotations

from urllib.parse import quote_plus

from common.env import env


def mysql_url(database: str) -> str:
    user = quote_plus(env("MYSQL_USER", "ticketforge"))
    password = quote_plus(env("MYSQL_PASSWORD", "ticketforge"))
    host = env("MYSQL_HOST", "127.0.0.1")
    port = env("MYSQL_PORT", "3306")
    return f"mysql+pymysql://{user}:{password}@{host}:{port}/{database}?charset=utf8mb4"


def core_url() -> str:
    return mysql_url(env("MYSQL_DATABASE", "core"))


def checkout_url() -> str:
    return mysql_url(env("CHECKOUT_DATABASE", "checkout"))
