"""MySQL named locks for cross-process critical sections.

Row locks cannot protect "at most one active hold per user per event" when no
row exists yet; a non-blocking named lock closes that race without adding a
Redis dependency to the correctness path.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import text
from sqlalchemy.orm import Session

from api.app.services.errors import DomainError


class LockTimeout(DomainError):
    status_code = 503
    code = "LOCK_TIMEOUT"


@contextmanager
def named_lock(session: Session, name: str, timeout_seconds: int = 5) -> Iterator[None]:
    # MySQL caps lock names at 64 characters; hashing keeps every key bounded.
    lock_name = "tf:" + hashlib.sha256(name.encode()).hexdigest()[:56]
    acquired = session.execute(
        text("SELECT GET_LOCK(:name, :timeout)"), {"name": lock_name, "timeout": timeout_seconds}
    ).scalar()
    if acquired != 1:
        raise LockTimeout("The server is busy. Please retry.")
    try:
        yield
    finally:
        session.execute(text("SELECT RELEASE_LOCK(:name)"), {"name": lock_name})
