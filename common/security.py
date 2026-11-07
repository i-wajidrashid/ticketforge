"""Cross-service security primitives.

Django mints the tokens (it owns accounts); FastAPI, the WebSocket layer and the
workers verify them. One shared implementation avoids "two JWT libraries, two
opinions" drift.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from dataclasses import dataclass
from typing import Any, Literal

import jwt

from common.env import env, env_int

TokenType = Literal["access", "refresh"]

JWT_ALGORITHM = "HS256"


class TokenError(Exception):
    """Raised when a JWT is missing, expired, of the wrong type or badly signed."""


class QRTokenError(Exception):
    """Raised when a ticket QR payload fails signature verification."""


def _secret() -> str:
    return env("JWT_SECRET", "dev-jwt-secret-change-me-please-32bytes")


def _qr_secret() -> str:
    return env("QR_SECRET", "dev-qr-secret-change-me-please-32bytes")


@dataclass(frozen=True, slots=True)
class TokenPayload:
    user_id: int
    role: str
    token_type: TokenType


def encode_token(
    user_id: int,
    role: str,
    token_type: TokenType = "access",
    *,
    ttl_seconds: int | None = None,
    secret: str | None = None,
) -> str:
    """Create a signed HS256 JWT. Access tokens are short, refresh tokens are long."""
    if ttl_seconds is None:
        if token_type == "access":
            ttl_seconds = env_int("JWT_ACCESS_TTL_SECONDS", 15 * 60)
        else:
            ttl_seconds = env_int("JWT_REFRESH_TTL_SECONDS", 7 * 24 * 60 * 60)
    now = int(time.time())
    claims: dict[str, Any] = {
        "sub": str(user_id),
        "role": role,
        "type": token_type,
        "iat": now,
        "exp": now + ttl_seconds,
        "jti": base64.urlsafe_b64encode(hashlib.sha256(f"{user_id}{now}".encode()).digest())[
            :16
        ].decode(),
    }
    return jwt.encode(claims, secret or _secret(), algorithm=JWT_ALGORITHM)


def decode_token(
    token: str,
    *,
    expected_type: TokenType = "access",
    secret: str | None = None,
) -> TokenPayload:
    """Verify signature, expiry and token type; return the identity inside."""
    try:
        claims = jwt.decode(
            token,
            secret or _secret(),
            algorithms=[JWT_ALGORITHM],
            options={"require": ["exp", "sub", "type"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("token has expired") from exc
    except jwt.InvalidTokenError as exc:
        raise TokenError("token is invalid") from exc

    if claims.get("type") != expected_type:
        raise TokenError(f"expected a {expected_type} token")
    try:
        user_id = int(claims["sub"])
    except (KeyError, TypeError, ValueError) as exc:
        raise TokenError("token is missing a subject") from exc
    return TokenPayload(
        user_id=user_id, role=str(claims.get("role", "customer")), token_type=expected_type
    )


# ---------------------------------------------------------------------------
# Ticket QR tokens: opaque, signed, and impossible to guess without the secret.
# A token reveals nothing on its own -- the server derives the ticket id locally.
# ---------------------------------------------------------------------------


def make_qr_token(ticket_id: str, secret: str | None = None) -> str:
    digest = hmac.new(
        (secret or _qr_secret()).encode(), ticket_id.encode(), hashlib.sha256
    ).hexdigest()
    return f"{ticket_id}.{digest}"


def verify_qr_token(qr_token: str, secret: str | None = None) -> str:
    """Return the ticket id encoded in a QR token, or raise QRTokenError."""
    ticket_id, _, digest = qr_token.partition(".")
    if not ticket_id or not digest:
        raise QRTokenError("malformed QR token")
    expected = hmac.new(
        (secret or _qr_secret()).encode(), ticket_id.encode(), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, digest):
        raise QRTokenError("QR signature mismatch")
    return ticket_id
