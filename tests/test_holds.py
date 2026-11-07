from __future__ import annotations

from datetime import timedelta

import pytest
from api.app.models import EventSeat, GAInventory, Hold, utcnow
from api.app.services import holds
from api.app.services.errors import (
    HoldExists,
    InvalidQuantity,
    SaleWindowClosed,
    SeatUnavailable,
    SoldOut,
)

pytestmark = pytest.mark.django_db(transaction=True)


def test_seat_hold_reserves_every_selected_seat(seated_event, db_session, customer):
    view = holds.create_hold(
        db_session,
        user_id=customer.pk,
        event_id=seated_event.event.pk,
        seat_ids=[seated_event.seats[0].id, seated_event.seats[1].id],
    )

    assert view.status == "active"
    assert view.total_cents == 10_000
    assert view.currency == "USD"
    assert 590 <= view.seconds_left <= 600
    assert {item.seat_label for item in view.items} == {"A1", "A2"}

    rows = db_session.query(EventSeat).filter(EventSeat.event_id == seated_event.event.pk).all()
    by_seat = {row.seat_id: row for row in rows}
    assert by_seat[seated_event.seats[0].id].status == "held"
    assert by_seat[seated_event.seats[0].id].hold_id == view.id
    assert by_seat[seated_event.seats[2].id].status == "available"


def test_only_one_active_hold_per_user_and_event(seated_event, db_session, customer):
    holds.create_hold(
        db_session,
        user_id=customer.pk,
        event_id=seated_event.event.pk,
        seat_ids=[seated_event.seats[0].id],
    )
    with pytest.raises(HoldExists):
        holds.create_hold(
            db_session,
            user_id=customer.pk,
            event_id=seated_event.event.pk,
            seat_ids=[seated_event.seats[1].id],
        )


def test_taking_a_taken_seat_rolls_back_the_whole_hold(seated_event, db_session, customer):
    from tests.factories import UserFactory

    other = UserFactory()
    holds.create_hold(
        db_session,
        user_id=other.pk,
        event_id=seated_event.event.pk,
        seat_ids=[seated_event.seats[2].id],
    )

    with pytest.raises(SeatUnavailable):
        holds.create_hold(
            db_session,
            user_id=customer.pk,
            event_id=seated_event.event.pk,
            seat_ids=[seated_event.seats[0].id, seated_event.seats[2].id],
        )

    # No partial state: the first seat must be free again.
    db_session.expire_all()
    seat = db_session.query(EventSeat).filter(EventSeat.seat_id == seated_event.seats[0].id).one()
    assert seat.status == "available"
    assert seat.hold_id is None


def test_general_admission_decrements_and_restores_inventory(ga_event, db_session, customer):
    view = holds.create_hold(
        db_session,
        user_id=customer.pk,
        event_id=ga_event.event.pk,
        ticket_type_id=ga_event.ticket_type.pk,
        quantity=3,
    )
    assert view.total_cents == 7500
    inventory = (
        db_session.query(GAInventory).filter_by(ticket_type_id=ga_event.ticket_type.pk).one()
    )
    assert inventory.available == 7

    holds.cancel_hold(db_session, view.id, user_id=customer.pk)
    db_session.expire_all()
    inventory = (
        db_session.query(GAInventory).filter_by(ticket_type_id=ga_event.ticket_type.pk).one()
    )
    assert inventory.available == 10


def test_sold_out_general_admission(ga_event, db_session):
    from tests.factories import UserFactory

    with pytest.raises(InvalidQuantity):
        holds.create_hold(
            db_session,
            user_id=UserFactory().pk,
            event_id=ga_event.event.pk,
            ticket_type_id=ga_event.ticket_type.pk,
            quantity=5,  # max_per_order is 4
        )

    for _ in range(2):
        holds.create_hold(
            db_session,
            user_id=UserFactory().pk,
            event_id=ga_event.event.pk,
            ticket_type_id=ga_event.ticket_type.pk,
            quantity=4,
        )
    # 10 total, 8 held: only 2 remain, so a 4-ticket order must fail.
    with pytest.raises(SoldOut):
        holds.create_hold(
            db_session,
            user_id=UserFactory().pk,
            event_id=ga_event.event.pk,
            ticket_type_id=ga_event.ticket_type.pk,
            quantity=4,
        )


def test_sale_window_is_enforced(ga_event, db_session, customer):
    from django.utils import timezone as django_timezone

    ga_event.ticket_type.sale_ends_at = django_timezone.now() - timedelta(minutes=1)
    ga_event.ticket_type.save(update_fields=["sale_ends_at"])

    with pytest.raises(SaleWindowClosed):
        holds.create_hold(
            db_session,
            user_id=customer.pk,
            event_id=ga_event.event.pk,
            ticket_type_id=ga_event.ticket_type.pk,
            quantity=1,
        )


def test_expiry_sweeper_releases_seats(seated_event, db_session, customer):
    view = holds.create_hold(
        db_session,
        user_id=customer.pk,
        event_id=seated_event.event.pk,
        seat_ids=[seated_event.seats[0].id],
    )
    hold = db_session.get(Hold, view.id)
    hold.expires_at = utcnow() - timedelta(seconds=1)
    db_session.commit()

    released = holds.expire_holds()

    assert len(released) == 1
    assert released[0]["seats"][0]["status"] == "available"
    db_session.expire_all()
    assert db_session.get(Hold, view.id).status == "expired"
    seat = db_session.query(EventSeat).filter(EventSeat.seat_id == seated_event.seats[0].id).one()
    assert seat.status == "available"


def test_expired_hold_view_reports_expired(seated_event, db_session, customer):
    view = holds.create_hold(
        db_session,
        user_id=customer.pk,
        event_id=seated_event.event.pk,
        seat_ids=[seated_event.seats[0].id],
    )
    hold = db_session.get(Hold, view.id)
    hold.expires_at = utcnow() - timedelta(seconds=1)
    db_session.commit()

    refreshed = holds.get_hold_view(db_session, view.id, user_id=customer.pk)
    assert refreshed.status == "expired"


def test_seat_map_exposes_statuses(seated_event, db_session):
    from api.app.services import inventory

    sections = inventory.seat_map(seated_event.event.pk)
    assert len(sections) == 1
    assert len(sections[0]["seats"]) == 5
    assert {seat["status"] for seat in sections[0]["seats"]} == {"available"}
