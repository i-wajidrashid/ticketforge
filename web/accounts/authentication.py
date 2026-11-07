"""DRF authentication backed by the shared HS256 JWT implementation."""

from __future__ import annotations

from typing import Any

from common.security import TokenError, decode_token
from django.contrib.auth.models import AnonymousUser
from rest_framework import authentication, exceptions

from web.accounts.models import User


def user_from_token(raw_token: str) -> User:
    try:
        payload = decode_token(raw_token)
    except TokenError as exc:
        raise exceptions.AuthenticationFailed("Invalid or expired token.") from exc
    user = User.objects.filter(pk=payload.user_id, is_active=True).first()
    if user is None:
        raise exceptions.AuthenticationFailed("User no longer exists.")
    return user


class JWTAuthentication(authentication.BaseAuthentication):
    keyword = "Bearer"

    def authenticate(self, request: Any) -> tuple[User, Any] | None:
        header = authentication.get_authorization_header(request).decode()
        if not header:
            return None
        parts = header.split()
        if len(parts) != 2 or parts[0].lower() != self.keyword.lower():
            raise exceptions.AuthenticationFailed("Authorization header must be 'Bearer <token>'.")
        return user_from_token(parts[1]), None

    def authenticate_header(self, request: Any) -> str:
        return self.keyword


class AnonymousIfNoToken(JWTAuthentication):
    """Same as JWTAuthentication but treats a missing header as anonymous."""

    def authenticate(self, request: Any) -> tuple[Any, Any] | None:
        result = super().authenticate(request)
        if result is None:
            return (AnonymousUser(), None)
        return result
