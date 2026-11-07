"""Database engines: one writer (checkout), one read-only (core)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, cast

from sqlalchemy import create_engine
from sqlalchemy.engine import CursorResult, Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from api.app.config import settings


class Base(DeclarativeBase):
    pass


_write_engine: Engine | None = None
_read_engine: Engine | None = None
_write_sessionmaker: sessionmaker[Session] | None = None


def write_engine() -> Engine:
    global _write_engine
    if _write_engine is None:
        _write_engine = create_engine(
            settings.checkout_url,
            pool_pre_ping=True,
            pool_size=10,
            max_overflow=20,
            pool_recycle=1800,
            isolation_level="READ COMMITTED",
        )
    return _write_engine


def read_engine() -> Engine:
    """Read-only connection to the core database. FastAPI never writes there."""
    global _read_engine
    if _read_engine is None:
        _read_engine = create_engine(
            settings.core_url,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=5,
            pool_recycle=1800,
        )
    return _read_engine


def session_factory() -> sessionmaker[Session]:
    global _write_sessionmaker
    if _write_sessionmaker is None:
        _write_sessionmaker = sessionmaker(bind=write_engine(), expire_on_commit=False, future=True)
    return _write_sessionmaker


def get_session() -> Iterator[Session]:
    """FastAPI dependency: one session per request, always closed."""
    session = session_factory()()
    try:
        yield session
    finally:
        session.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    session = session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def affected(result: Any) -> int:
    """Typed access to ``rowcount`` for conditional UPDATEs."""
    return int(cast(CursorResult[Any], result).rowcount)


def reset_engines() -> None:
    """Test hook: drop cached engines so a different database can be used."""
    global _write_engine, _read_engine, _write_sessionmaker
    if _write_engine is not None:
        _write_engine.dispose()
    if _read_engine is not None:
        _read_engine.dispose()
    _write_engine = None
    _read_engine = None
    _write_sessionmaker = None
