"""Authenticate WebSocket connections with a short-lived JWT (query string)."""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from common.security import TokenError, decode_token


class JWTAuthMiddleware:
    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        query = parse_qs(scope.get("query_string", b"").decode())
        token = (query.get("token") or [None])[0]
        if token:
            try:
                payload = decode_token(token)
            except TokenError:
                payload = None
            if payload is not None and await self._user_exists(payload.user_id):
                scope["user_id"] = payload.user_id
                scope["role"] = payload.role
        if scope.get("path", "").startswith("/ws/holds/") and "user_id" not in scope:
            await send({"type": "websocket.close", "code": 4401})
            return
        await self.app(scope, receive, send)

    @database_sync_to_async
    def _user_exists(self, user_id: int) -> bool:
        from web.accounts.models import User

        return User.objects.filter(pk=user_id, is_active=True).exists()
