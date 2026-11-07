"""WebSocket layer: public seat stream and authenticated hold sockets."""

from __future__ import annotations

import asyncio

import pytest
from channels.layers import get_channel_layer
from channels.testing import WebsocketCommunicator
from common.realtime import seat_group
from web.config.asgi import application

pytestmark = pytest.mark.django_db


def test_seat_socket_receives_broadcasts():
    async def scenario() -> None:
        communicator = WebsocketCommunicator(application, "/ws/events/42/seats")
        connected, _ = await communicator.connect()
        assert connected
        assert (await communicator.receive_json_from())["type"] == "connected"

        layer = get_channel_layer()
        await layer.group_send(
            seat_group(42),
            {
                "type": "broadcast",
                "payload": {
                    "type": "seat_update",
                    "seats": [{"seat_id": 7, "label": "B12", "status": "held"}],
                },
            },
        )
        message = await communicator.receive_json_from()
        assert message["seats"][0]["label"] == "B12"
        assert message["seats"][0]["status"] == "held"
        await communicator.disconnect()

    asyncio.run(scenario())


def test_hold_socket_requires_a_token():
    async def scenario() -> None:
        communicator = WebsocketCommunicator(
            application, "/ws/holds/00000000-0000-0000-0000-000000000000"
        )
        connected, code = await communicator.connect()
        assert connected is False
        assert code == 4401

    asyncio.run(scenario())


@pytest.fixture(autouse=True)
def _fresh_channel_layer():
    """Each asyncio.run gets its own event loop, so start from a fresh layer."""
    from channels.layers import channel_layers

    channel_layers.backends.clear()
    yield
    channel_layers.backends.clear()
