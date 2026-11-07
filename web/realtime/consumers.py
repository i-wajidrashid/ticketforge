"""WebSocket consumers: seat maps, hold countdowns and live sales."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from common.realtime import hold_group, sales_group, seat_group


class SeatConsumer(AsyncJsonWebsocketConsumer):
    """Public, read-only stream of seat status updates for one event."""

    async def connect(self) -> None:
        self.event_id = int(self.scope["url_route"]["kwargs"]["event_id"])
        self.group = seat_group(self.event_id)
        await self.channel_layer.group_add(self.group, self.channel_name)
        await self.accept()
        await self.send_json({"type": "connected", "event_id": self.event_id})

    async def disconnect(self, code: int) -> None:
        if hasattr(self, "group"):
            await self.channel_layer.group_discard(self.group, self.channel_name)

    async def receive_json(self, content: dict[str, Any], **kwargs: Any) -> None:
        # Clients may ping to keep proxies awake.
        if content.get("type") == "ping":
            await self.send_json({"type": "pong"})

    async def broadcast(self, event: dict[str, Any]) -> None:
        payload = event.get("payload", {})
        await self.send_json(payload)


class HoldConsumer(AsyncJsonWebsocketConsumer):
    """Live countdown for the owner of a hold."""

    async def connect(self) -> None:
        self.hold_id = str(self.scope["url_route"]["kwargs"]["hold_id"])
        user_id = self.scope.get("user_id")
        if user_id is None or not await self._owns_hold(user_id):
            await self.close(code=4403)
            return
        self.group = hold_group(self.hold_id)
        await self.channel_layer.group_add(self.group, self.channel_name)
        await self.accept()
        self._task = asyncio.create_task(self._tick_loop())

    async def disconnect(self, code: int) -> None:
        if hasattr(self, "_task"):
            self._task.cancel()
        if hasattr(self, "group"):
            await self.channel_layer.group_discard(self.group, self.channel_name)

    async def receive_json(self, content: dict[str, Any], **kwargs: Any) -> None:
        if content.get("type") == "ping":
            await self.send_json({"type": "pong"})

    async def broadcast(self, event: dict[str, Any]) -> None:
        await self.send_json(event.get("payload", {}))

    async def _tick_loop(self) -> None:
        try:
            while True:
                state = await self._hold_state()
                if state is None or state["status"] != "active" or state["seconds_left"] <= 0:
                    await self.send_json({"type": "hold_expired"})
                    await self.close()
                    return
                await self.send_json({"type": "hold_tick", "seconds_left": state["seconds_left"]})
                await asyncio.sleep(1)
        except asyncio.CancelledError:  # pragma: no cover - normal disconnect
            raise

    @database_sync_to_async
    def _owns_hold(self, user_id: int) -> bool:
        from api.app.db import session_scope
        from api.app.models import Hold

        with session_scope() as session:
            hold = session.get(Hold, self.hold_id)
            return bool(hold and hold.user_id == user_id)

    @database_sync_to_async
    def _hold_state(self) -> dict[str, Any] | None:
        from api.app.db import session_scope
        from api.app.models import Hold

        with session_scope() as session:
            hold = session.get(Hold, self.hold_id)
            if hold is None:
                return None
            now = datetime.now(UTC).replace(tzinfo=None)
            return {
                "status": hold.status,
                "seconds_left": max(0, int((hold.expires_at - now).total_seconds())),
            }


class OrgSalesConsumer(AsyncJsonWebsocketConsumer):
    """Live sales counter for the organizer who owns the event."""

    async def connect(self) -> None:
        self.event_id = int(self.scope["url_route"]["kwargs"]["event_id"])
        user_id = self.scope.get("user_id")
        if user_id is None or not await self._owns_event(user_id):
            await self.close(code=4403)
            return
        self.group = sales_group(self.event_id)
        await self.channel_layer.group_add(self.group, self.channel_name)
        await self.accept()
        await self.send_json({"type": "connected", "event_id": self.event_id})

    async def disconnect(self, code: int) -> None:
        if hasattr(self, "group"):
            await self.channel_layer.group_discard(self.group, self.channel_name)

    async def receive_json(self, content: dict[str, Any], **kwargs: Any) -> None:
        if content.get("type") == "ping":
            await self.send_json({"type": "pong"})

    async def broadcast(self, event: dict[str, Any]) -> None:
        await self.send_json(event.get("payload", {}))

    @database_sync_to_async
    def _owns_event(self, user_id: int) -> bool:
        from web.events.models import Event

        return Event.objects.filter(pk=self.event_id, organizer__user_id=user_id).exists()
