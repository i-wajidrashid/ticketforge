"""Authenticated WebSocket consumers."""

from __future__ import annotations

import asyncio

import pytest
from api.app.services import holds
from channels.layers import get_channel_layer
from channels.testing import WebsocketCommunicator
from common.realtime import sales_group
from common.security import encode_token
from web.config.asgi import application

from tests.factories import OrganizerFactory, UserFactory

pytestmark = pytest.mark.django_db(transaction=True)


def test_hold_consumer_sends_ticks_to_the_owner(seated_event, db_session, customer):
    hold = holds.create_hold(
        db_session,
        user_id=customer.pk,
        event_id=seated_event.event.pk,
        seat_ids=[seated_event.seats[0].id],
    )
    token = encode_token(customer.pk, customer.role)

    async def scenario() -> None:
        communicator = WebsocketCommunicator(application, f"/ws/holds/{hold.id}?token={token}")
        connected, _ = await communicator.connect()
        assert connected
        message = await communicator.receive_json_from(timeout=3)
        assert message["type"] == "hold_tick"
        assert 0 < message["seconds_left"] <= 600
        await communicator.disconnect()

    asyncio.run(scenario())


def test_hold_consumer_rejects_a_foreign_token(seated_event, db_session, customer):
    hold = holds.create_hold(
        db_session,
        user_id=customer.pk,
        event_id=seated_event.event.pk,
        seat_ids=[seated_event.seats[0].id],
    )
    intruder = UserFactory()
    token = encode_token(intruder.pk, intruder.role)

    async def scenario() -> None:
        communicator = WebsocketCommunicator(application, f"/ws/holds/{hold.id}?token={token}")
        connected, code = await communicator.connect()
        assert connected is False
        assert code == 4403

    asyncio.run(scenario())


def test_org_sales_consumer_only_accepts_the_owner(seated_event):
    organizer = OrganizerFactory()
    token = encode_token(organizer.user.pk, organizer.user.role)

    async def scenario() -> None:
        communicator = WebsocketCommunicator(
            application, f"/ws/org/events/{seated_event.event.pk}/sales?token={token}"
        )
        connected, code = await communicator.connect()
        assert connected is False
        assert code == 4403

    asyncio.run(scenario())


def test_org_sales_consumer_receives_sales(seated_event):
    token = encode_token(seated_event.organizer_user.pk, seated_event.organizer_user.role)

    async def scenario() -> None:
        communicator = WebsocketCommunicator(
            application, f"/ws/org/events/{seated_event.event.pk}/sales?token={token}"
        )
        connected, _ = await communicator.connect()
        assert connected
        assert (await communicator.receive_json_from())["type"] == "connected"

        await get_channel_layer().group_send(
            sales_group(seated_event.event.pk),
            {"type": "broadcast", "payload": {"type": "sale", "tickets": 2, "revenue_cents": 5000}},
        )
        message = await communicator.receive_json_from()
        assert message == {"type": "sale", "tickets": 2, "revenue_cents": 5000}
        await communicator.disconnect()

    asyncio.run(scenario())


@pytest.fixture(autouse=True)
def _fresh_channel_layer():
    """Each asyncio.run gets its own event loop, so start from a fresh layer."""
    from channels.layers import channel_layers

    channel_layers.backends.clear()
    yield
    channel_layers.backends.clear()
