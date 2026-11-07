"""JWT authentication dependencies for FastAPI."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated

from common.security import TokenError, decode_token
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from api.app import core_repo
from api.app.services.errors import EmailNotVerified, Forbidden, Unauthorized

bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True, slots=True)
class Principal:
    user_id: int
    role: str
    is_verified: bool


def get_principal(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> Principal:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise Unauthorized("A bearer token is required.")
    try:
        payload = decode_token(credentials.credentials)
    except TokenError as exc:
        raise Unauthorized(str(exc)) from exc
    user = core_repo.get_user(payload.user_id)
    if user is None:
        raise Unauthorized("User no longer exists.")
    return Principal(user_id=user.id, role=user.role, is_verified=user.is_verified)


def require_verified(principal: Annotated[Principal, Depends(get_principal)]) -> Principal:
    if not principal.is_verified:
        raise EmailNotVerified("Verify your email before buying tickets.")
    return principal


def require_roles(*roles: str) -> Callable[[Principal], Principal]:
    def dependency(principal: Annotated[Principal, Depends(get_principal)]) -> Principal:
        if principal.role not in roles:
            raise Forbidden("Your account cannot perform this action.")
        return principal

    return dependency


def require_staff(principal: Annotated[Principal, Depends(get_principal)]) -> Principal:
    if principal.role not in {"door_staff", "organizer", "admin"}:
        raise Forbidden("Door staff access only.")
    return principal
