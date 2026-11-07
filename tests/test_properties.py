"""Property-based test: no sequence of holds/cancels/expiries can oversell."""

from __future__ import annotations

from datetime import timedelta

import pytest
from api.app.db import session_factory
from api.app.models import EventSeat, Hold, utcnow
from api.app.services import holds, inventory
from api.app.services.errors import DomainError
from conftest import truncate_checkout
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from tests.factories import (
    EventFactory,
    OrganizerFactory,
    SeatFactory,
    SectionFactory,
    TicketTypeFactory,
    UserFactory,
    VenueFactory,
)

pytestmark = pytest.mark.django_db(transaction=True)

OPERATION = st.tuples(
    st.integers(min_value=0, max_value=2),
    st.sampled_from(["hold", "cancel", "expire"]),
)


def _setup():
    organizer = OrganizerFactory()
    venue = VenueFactory()
    section = SectionFactory(venue=venue)
    seats = [SeatFactory(section=section, row="A", number=str(number)) for number in range(1, 4)]
    event = EventFactory(organizer=organizer, venue=venue)
    TicketTypeFactory(
        event=event, price_cents=1000, quantity_total=3, inventory_mode="seated", max_per_order=1
    )
    users = [UserFactory() for _ in range(3)]
    return event, seats, users


@settings(
    max_examples=20,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)
@given(operations=st.lists(OPERATION, max_size=6))
def test_inventory_invariants_hold_under_random_histories(operations):
    truncate_checkout()
    event, seats, users = _setup()
    inventory.seat_map(event.pk)
    session = session_factory()()
    try:
        for user_index, action in operations:
            user = users[user_index]
            active = (
                session.query(Hold).filter(Hold.event_id == event.pk, Hold.status == "active").all()
            )
            if action == "hold":
                seat = seats[user_index % len(seats)]
                try:
                    holds.create_hold(
                        session, user_id=user.pk, event_id=event.pk, seat_ids=[seat.id]
                    )
                except DomainError:
                    session.rollback()
            elif action == "cancel" and active:
                try:
                    holds.cancel_hold(session, active[0].id, user_id=active[0].user_id)
                except DomainError:
                    session.rollback()
            elif action == "expire" and active:
                active[0].expires_at = utcnow() - timedelta(seconds=1)
                session.commit()
                holds.expire_holds()
            session.expire_all()

        rows = session.query(EventSeat).filter(EventSeat.event_id == event.pk).all()
        statuses = [row.status for row in rows]
        assert len(rows) == len(seats)
        assert set(statuses) <= {"available", "held", "sold", "blocked"}
        assert statuses.count("held") + statuses.count("sold") + statuses.count("available") == len(
            seats
        )

        active_ids = {
            hold.id
            for hold in session.query(Hold)
            .filter(Hold.event_id == event.pk, Hold.status == "active")
            .all()
        }
        for row in rows:
            if row.status == "held":
                assert row.hold_id in active_ids
            else:
                assert row.hold_id is None or row.status == "sold"

        active_holds = session.query(Hold).filter(Hold.status == "active").all()
        keys = [(hold.user_id, hold.event_id) for hold in active_holds]
        assert len(keys) == len(set(keys)), "one active hold per user per event"
    finally:
        session.close()
