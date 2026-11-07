from __future__ import annotations

from datetime import timedelta

import pytest
from api.app.models import Hold
from api.app.services import holds, rate_limit
from api.app.services.errors import (
    EventNotSellable,
    HoldNotFound,
    InvalidQuantity,
    RateLimited,
    SaleWindowClosed,
    SeatUnavailable,
)
from django.utils import timezone

from tests.factories import TicketTypeFactory, UserFactory

pytestmark = pytest.mark.django_db(transaction=True)


def test_unknown_event_is_not_found(db_session, customer):
    with pytest.raises(Exception):
        holds.create_hold(db_session, user_id=customer.pk, event_id=999_999, seat_ids=[1])


def test_draft_and_started_events_cannot_be_held(seated_event, db_session, customer):
    seated_event.event.status = "draft"
    seated_event.event.save(update_fields=["status"])
    with pytest.raises(EventNotSellable):
        holds.create_hold(
            db_session,
            user_id=customer.pk,
            event_id=seated_event.event.pk,
            seat_ids=[seated_event.seats[0].id],
        )

    seated_event.event.status = "published"
    seated_event.event.starts_at = timezone.now() - timedelta(hours=1)
    seated_event.event.save(update_fields=["status", "starts_at"])
    with pytest.raises(EventNotSellable):
        holds.create_hold(
            db_session,
            user_id=customer.pk,
            event_id=seated_event.event.pk,
            seat_ids=[seated_event.seats[0].id],
        )


def test_multiple_seated_types_require_an_explicit_type(seated_event, db_session, customer):
    TicketTypeFactory(
        event=seated_event.event, name="VIP", price_cents=9000, inventory_mode="seated"
    )
    with pytest.raises(InvalidQuantity):
        holds.create_hold(
            db_session,
            user_id=customer.pk,
            event_id=seated_event.event.pk,
            seat_ids=[seated_event.seats[0].id],
        )


def test_cannot_mix_seats_and_quantity(seated_event, db_session, customer):
    with pytest.raises(InvalidQuantity):
        holds.create_hold(
            db_session,
            user_id=customer.pk,
            event_id=seated_event.event.pk,
            seat_ids=[seated_event.seats[0].id],
            quantity=2,
        )


def test_max_seats_per_order(seated_event, db_session, customer):
    seat_ids = [seat.id for seat in seated_event.seats]
    with pytest.raises(InvalidQuantity):
        holds.create_hold(
            db_session, user_id=customer.pk, event_id=seated_event.event.pk, seat_ids=seat_ids
        )


def test_unknown_seat_is_rejected(seated_event, db_session, customer):
    with pytest.raises(SeatUnavailable):
        holds.create_hold(
            db_session, user_id=customer.pk, event_id=seated_event.event.pk, seat_ids=[999_999]
        )


def test_sale_not_started(ga_event, db_session, customer):
    ga_event.ticket_type.sale_starts_at = timezone.now() + timedelta(days=1)
    ga_event.ticket_type.save(update_fields=["sale_starts_at"])
    with pytest.raises(SaleWindowClosed):
        holds.create_hold(
            db_session,
            user_id=customer.pk,
            event_id=ga_event.event.pk,
            ticket_type_id=ga_event.ticket_type.pk,
            quantity=1,
        )


def test_get_hold_wrong_owner_and_cancel_converted(seated_event, db_session, customer):
    view = holds.create_hold(
        db_session,
        user_id=customer.pk,
        event_id=seated_event.event.pk,
        seat_ids=[seated_event.seats[0].id],
    )
    with pytest.raises(HoldNotFound):
        holds.get_hold_view(db_session, view.id, user_id=UserFactory().pk)

    hold = db_session.get(Hold, view.id)
    hold.status = "converted"
    db_session.commit()
    assert holds.cancel_hold(db_session, view.id, user_id=customer.pk).status == "converted"


def test_seat_changes_and_active_count(seated_event, db_session, customer):
    view = holds.create_hold(
        db_session,
        user_id=customer.pk,
        event_id=seated_event.event.pk,
        seat_ids=[seated_event.seats[0].id],
    )
    changes = holds.seat_changes_for_hold(db_session, view.id, "held")
    assert changes[0]["label"] == "A1"
    assert holds.active_hold_count(seated_event.event.pk) == 1
    assert holds.expire_holds() == []


def test_hold_rate_limit(seated_event, db_session, customer, monkeypatch):
    monkeypatch.setattr("api.app.services.holds.settings.hold_rate_limit_per_minute", 1)
    holds.create_hold(
        db_session,
        user_id=customer.pk,
        event_id=seated_event.event.pk,
        seat_ids=[seated_event.seats[0].id],
    )
    with pytest.raises(RateLimited):
        holds.create_hold(
            db_session,
            user_id=customer.pk,
            event_id=seated_event.event.pk,
            seat_ids=[seated_event.seats[1].id],
        )


def test_generic_rate_limiter(monkeypatch):
    rate_limit.enforce("unit-test-key", 1)
    with pytest.raises(RateLimited):
        rate_limit.enforce("unit-test-key", 1)
