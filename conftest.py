"""Shared pytest setup.

Test topology mirrors production:
* ``test_core``     -- created by pytest-django, read by both services;
* ``test_checkout`` -- created here and migrated with Alembic.
"""

from __future__ import annotations

import os

os.environ["DJANGO_SETTINGS_MODULE"] = "web.config.settings_test"
os.environ.setdefault("MYSQL_HOST", "127.0.0.1")
os.environ.setdefault("MYSQL_PORT", "3306")
os.environ.setdefault("MYSQL_USER", "ticketforge")
os.environ.setdefault("MYSQL_PASSWORD", "ticketforge")
os.environ.setdefault("CHECKOUT_DATABASE", "test_checkout")
os.environ.setdefault("TF_CORE_DATABASE", "test_core")
os.environ.setdefault("CELERY_TASK_ALWAYS_EAGER", "1")
os.environ.setdefault("SEARCH_ENABLED", "0")
os.environ.setdefault("HOLD_RATE_LIMIT_PER_MINUTE", "1000")
os.environ.setdefault("STRIPE_GATEWAY", "fake")
# Separate logical Redis DB + Meilisearch index so the test suite can run
# while the development stack is up without sharing state.
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:6379/5")
os.environ.setdefault("MEILI_INDEX", "events_test")

from pathlib import Path

import pytest
from alembic.config import Config
from common.db import mysql_url
from sqlalchemy import create_engine, text

from alembic import command

ROOT = Path(__file__).resolve().parent
CHECKOUT_TEST_DB = os.environ["CHECKOUT_DATABASE"]


def _admin_engine():
    return create_engine(mysql_url("core"), isolation_level="AUTOCOMMIT", pool_pre_ping=True)


def _checkout_tables() -> list[str]:
    from api.app.db import Base

    return [table.name for table in Base.metadata.sorted_tables]


def truncate_checkout() -> None:
    from api.app.db import write_engine

    tables = _checkout_tables()
    with write_engine().connect() as conn:
        conn.execute(text("SET FOREIGN_KEY_CHECKS = 0"))
        for table in tables:
            conn.execute(text(f"TRUNCATE TABLE `{table}`"))
        conn.execute(text("SET FOREIGN_KEY_CHECKS = 1"))


@pytest.fixture(scope="session", autouse=True)
def _core_database(django_db_setup):
    """pytest-django creates + migrates test_core exactly once per session."""
    yield


@pytest.fixture(scope="session")
def checkout_database():
    engine = _admin_engine()
    with engine.connect() as conn:
        conn.execute(text(f"DROP DATABASE IF EXISTS `{CHECKOUT_TEST_DB}`"))
        conn.execute(
            text(
                f"CREATE DATABASE `{CHECKOUT_TEST_DB}` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci"
            )
        )

    config = Config(str(ROOT / "api" / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "api" / "alembic"))
    command.upgrade(config, "head")

    yield CHECKOUT_TEST_DB

    from api.app.db import reset_engines

    reset_engines()
    with engine.connect() as conn:
        conn.execute(text(f"DROP DATABASE IF EXISTS `{CHECKOUT_TEST_DB}`"))
    engine.dispose()


@pytest.fixture(autouse=True)
def _clean_checkout(checkout_database):
    truncate_checkout()
    yield


@pytest.fixture
def db_session(checkout_database):
    from api.app.db import session_factory

    session = session_factory()()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def api_client():
    from api.app.main import app
    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        yield client


@pytest.fixture(autouse=True)
def _reset_rate_limits():
    from api.app.services import rate_limit
    from django.core.cache import cache

    rate_limit.client().flushdb()
    cache.clear()
    yield


# ---------------------------------------------------------------------------
# Domain fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def customer(db):
    from tests.factories import UserFactory

    return UserFactory(role="customer", is_verified=True)


@pytest.fixture
def organizer_user(db):
    from tests.factories import OrganizerFactory

    return OrganizerFactory().user


@pytest.fixture
def seated_event(db):
    from types import SimpleNamespace

    from tests.factories import (
        EventFactory,
        OrganizerFactory,
        SeatFactory,
        SectionFactory,
        TicketTypeFactory,
        VenueFactory,
    )

    organizer = OrganizerFactory()
    venue = VenueFactory(capacity=50)
    section = SectionFactory(venue=venue, name="Stalls")
    seats = [SeatFactory(section=section, row="A", number=str(number)) for number in range(1, 6)]
    event = EventFactory(organizer=organizer, venue=venue)
    ticket_type = TicketTypeFactory(
        event=event,
        name="Stalls",
        price_cents=5000,
        quantity_total=5,
        inventory_mode="seated",
        max_per_order=4,
    )
    return SimpleNamespace(
        event=event,
        venue=venue,
        seats=seats,
        ticket_type=ticket_type,
        organizer=organizer,
        organizer_user=organizer.user,
    )


@pytest.fixture
def ga_event(db):
    from types import SimpleNamespace

    from tests.factories import EventFactory, OrganizerFactory, TicketTypeFactory, VenueFactory

    organizer = OrganizerFactory()
    venue = VenueFactory()
    event = EventFactory(organizer=organizer, venue=venue)
    ticket_type = TicketTypeFactory(
        event=event,
        name="General",
        price_cents=2500,
        quantity_total=10,
        max_per_order=4,
        inventory_mode="ga",
    )
    return SimpleNamespace(
        event=event,
        venue=venue,
        ticket_type=ticket_type,
        organizer=organizer,
        organizer_user=organizer.user,
    )


def auth_token(user) -> str:
    from common.security import encode_token

    return encode_token(user.pk, user.role, "access")


@pytest.fixture
def customer_token(customer) -> str:
    return auth_token(customer)


@pytest.fixture
def organizer_token(seated_event) -> str:
    return auth_token(seated_event.organizer_user)
