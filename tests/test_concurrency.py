"""The acceptance test that matters: 50 buyers, 1 seat, exactly 1 winner."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from api.app.db import session_factory
from api.app.models import EventSeat
from api.app.services import holds
from api.app.services.errors import SeatUnavailable

from tests.factories import UserFactory

pytestmark = pytest.mark.django_db(transaction=True)


def test_fifty_threads_one_seat_exactly_one_winner(seated_event):
    buyers = [UserFactory() for _ in range(50)]
    seat_id = seated_event.seats[0].id
    barrier = threading.Barrier(len(buyers))

    def attempt(user_id: int) -> str:
        session = session_factory()()
        try:
            barrier.wait(timeout=10)
            holds.create_hold(
                session, user_id=user_id, event_id=seated_event.event.pk, seat_ids=[seat_id]
            )
            return "won"
        except SeatUnavailable:
            return "conflict"
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=len(buyers)) as pool:
        results = list(pool.map(attempt, [buyer.pk for buyer in buyers]))

    assert results.count("won") == 1
    assert results.count("conflict") == 49

    with session_factory()() as session:
        seat = (
            session.query(EventSeat)
            .filter(EventSeat.event_id == seated_event.event.pk, EventSeat.seat_id == seat_id)
            .one()
        )
        assert seat.status == "held"
        assert seat.version == 1


def test_general_admission_never_sells_more_than_stock(ga_event):
    buyers = [UserFactory() for _ in range(40)]
    barrier = threading.Barrier(len(buyers))

    def attempt(user_id: int) -> str:
        session = session_factory()()
        try:
            barrier.wait(timeout=10)
            holds.create_hold(
                session,
                user_id=user_id,
                event_id=ga_event.event.pk,
                ticket_type_id=ga_event.ticket_type.pk,
                quantity=2,
            )
            return "won"
        except Exception as exc:
            return type(exc).__name__
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=len(buyers)) as pool:
        results = list(pool.map(attempt, [buyer.pk for buyer in buyers]))

    assert results.count("won") == 5  # 10 tickets / 2 per order
    from api.app.models import GAInventory

    with session_factory()() as session:
        inventory = (
            session.query(GAInventory).filter_by(ticket_type_id=ga_event.ticket_type.pk).one()
        )
        assert inventory.available == 0
