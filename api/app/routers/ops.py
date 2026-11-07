"""Liveness and readiness probes used by Docker and Nginx."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Response
from sqlalchemy import text
from sqlalchemy.orm import Session

from api.app.db import get_session
from api.app.services import rate_limit

router = APIRouter(tags=["ops"])


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "api"}


@router.get("/ready")
def ready(
    session: Annotated[Session, Depends(get_session)], response: Response
) -> dict[str, object]:
    checks: dict[str, str] = {}
    try:
        session.execute(text("SELECT 1"))
        checks["mysql"] = "ok"
    except Exception:
        checks["mysql"] = "unavailable"
    try:
        rate_limit.client().ping()
        checks["redis"] = "ok"
    except Exception:
        checks["redis"] = "unavailable"
    healthy = all(value == "ok" for value in checks.values())
    if not healthy:
        response.status_code = 503
    return {"status": "ok" if healthy else "degraded", "checks": checks}
