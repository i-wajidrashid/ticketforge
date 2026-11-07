"""Edge cases for the two concurrency-critical services (holds + payments).

The spec asks for 100% coverage on ``services/holds.py`` and
``services/payments.py``; every test here pins down a real behaviour (late
payments, lost seats, replayed and out-of-order webhooks, provider failures)
rather than just executing a line.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from api.app.models import (
    EventSeat,
    GAInventory,
    Hold,
    HoldItem,
    Order,
    StripeEvent,
    Ticket,
    utcnow,
)
from api.app.services import holds, payments
from api.app.services.errors import (
    EventNotSellable,
    IdempotencyConflict,
    InvalidQuantity,
    PaymentFailed,
    RefundNotAllowed,
    SoldOut,
)
from api.app.services.stripe_gateway import FakeGateway
from django.utils import timezone
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from tests.factories import EventFactory, UserFactory
from tests.helpers import make_paid_order, make_refund

pytestmark = pytest.mark.django_db(transaction=True)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _succeeded(db_session, event_id: str, intent_id: str, *, metadata: dict | None = None):
    obj: dict = {"id": intent_id}
    if metadata is not None:
        obj["metadata"] = metadata
    return payments.handle_webhook_event(
        db_session,
        stripe_event_id=event_id,
        event_type="payment_intent.succeeded",
        payload={"id": event_id, "type": "payment_intent.succeeded", "data": {"object": obj}},
    )


def _refunded(db_session, event_id: str, intent_id: str):
    return payments.handle_webhook_event(
        db_session,
        stripe_event_id=event_id,
        event_type="charge.refunded",
        payload={
            "id": event_id,
            "type": "charge.refunded",
            "data": {"object": {"payment_intent": intent_id}},
        },
    )


def _backdate_hold(db_session, hold_id: str) -> None:
    db_session.execute(
        update(Hold).where(Hold.id == hold_id).values(expires_at=utcnow() - timedelta(minutes=1))
    )
    db_session.commit()
    db_session.expire_all()


def _seat_hold(db_session, event, customer, index: int = 0):
    return holds.create_hold(
        db_session,
        user_id=customer.pk,
        event_id=event.event.pk,
        seat_ids=[event.seats[index].id],
    )


def _ga_hold(db_session, event, customer, quantity: int = 2):
    return holds.create_hold(
        db_session,
        user_id=customer.pk,
        event_id=event.event.pk,
        ticket_type_id=event.ticket_type.pk,
        quantity=quantity,
    )


def _start(db_session, customer, hold_id: str, key: str, gateway=None):
    return payments.start_payment(
        db_session, user_id=customer.pk, hold_id=hold_id, idempotency_key=key, gateway=gateway
    )


# ---------------------------------------------------------------------------
# holds
# ---------------------------------------------------------------------------


def test_explicit_seated_ticket_type_is_accepted(seated_event, db_session, customer):
    hold = holds.create_hold(
        db_session,
        user_id=customer.pk,
        event_id=seated_event.event.pk,
        seat_ids=[seated_event.seats[0].id],
        ticket_type_id=seated_event.ticket_type.pk,
    )
    assert hold.items[0].ticket_type_id == seated_event.ticket_type.pk


def test_ticket_type_from_another_event_is_rejected(seated_event, ga_event, db_session, customer):
    # Make the section map to no type, so the client-supplied type is consulted.
    from tests.factories import TicketTypeFactory

    seated_event.ticket_type.name = "Standard"
    seated_event.ticket_type.save(update_fields=["name"])
    TicketTypeFactory(event=seated_event.event, name="VIP", inventory_mode="seated")
    with pytest.raises(EventNotSellable):
        holds.create_hold(
            db_session,
            user_id=customer.pk,
            event_id=seated_event.event.pk,
            seat_ids=[seated_event.seats[0].id],
            ticket_type_id=ga_event.ticket_type.pk,
        )


def test_event_without_ticket_types_is_not_sellable(db_session, customer):
    event = EventFactory()
    with pytest.raises(EventNotSellable):
        holds.create_hold(db_session, user_id=customer.pk, event_id=event.pk, quantity=1)


def test_releasing_a_finished_hold_is_a_noop(seated_event, db_session, customer):
    view = _seat_hold(db_session, seated_event, customer)
    holds.cancel_hold(db_session, view.id, user_id=customer.pk)
    hold = db_session.get(Hold, view.id)
    assert holds.release_hold(db_session, hold)["released"] is False


def test_stale_hold_is_expired_inline_before_a_new_one(seated_event, db_session, customer):
    first = _seat_hold(db_session, seated_event, customer, 0)
    _backdate_hold(db_session, first.id)

    second = _seat_hold(db_session, seated_event, customer, 1)

    assert db_session.get(Hold, first.id).status == "expired"
    assert second.status == "active"
    seat = db_session.scalars(
        select(EventSeat).where(EventSeat.seat_id == seated_event.seats[0].id)
    ).one()
    assert seat.status == "available"


@pytest.mark.parametrize(
    ("ticket_type", "quantity", "message"),
    [
        ("ga", 0, "at least one"),
        (None, 1, "required for general admission"),
        ("missing", 1, None),
        ("seated", 1, "requires seat selection"),
    ],
)
def test_general_admission_request_validation(
    ga_event, seated_event, db_session, customer, ticket_type, quantity, message
):
    ids = {
        "ga": ga_event.ticket_type.pk,
        "seated": seated_event.ticket_type.pk,
        "missing": 999_999,
        None: None,
    }
    event_id = seated_event.event.pk if ticket_type == "seated" else ga_event.event.pk
    expected = EventNotSellable if ticket_type == "missing" else InvalidQuantity
    with pytest.raises(expected) as excinfo:
        holds.create_hold(
            db_session,
            user_id=customer.pk,
            event_id=event_id,
            ticket_type_id=ids[ticket_type],
            quantity=quantity,
        )
    if message:
        assert message in str(excinfo.value)


def test_seat_changes_for_unknown_hold_is_empty(db_session):
    assert (
        holds.seat_changes_for_hold(db_session, "00000000-0000-0000-0000-000000000000", "held")
        == []
    )


def test_sweeper_releases_due_holds(ga_event, db_session, customer):
    view = _ga_hold(db_session, ga_event, customer, quantity=3)
    _backdate_hold(db_session, view.id)

    released = holds.expire_holds()

    assert [item["hold_id"] for item in released] == [view.id]
    db_session.expire_all()
    inventory = db_session.scalars(
        select(GAInventory).where(GAInventory.ticket_type_id == ga_event.ticket_type.pk)
    ).one()
    assert inventory.available == 10


# ---------------------------------------------------------------------------
# start_payment
# ---------------------------------------------------------------------------


def test_hold_total_skips_unknown_ticket_types(seated_event, db_session, customer):
    view = _seat_hold(db_session, seated_event, customer)
    db_session.add(
        HoldItem(hold_id=view.id, event_seat_id=None, ticket_type_id=999_999, quantity=3)
    )
    db_session.commit()
    hold = db_session.get(Hold, view.id)
    db_session.refresh(hold)

    assert payments.hold_total(db_session, hold) == (5000, "USD")


def test_idempotency_key_cannot_be_reused_by_another_customer(seated_event, db_session, customer):
    view = _seat_hold(db_session, seated_event, customer)
    _start(db_session, customer, view.id, "shared-key")

    with pytest.raises(IdempotencyConflict):
        _start(db_session, UserFactory(), view.id, "shared-key")


def test_concurrent_insert_of_the_same_order_is_a_conflict(
    seated_event, db_session, customer, monkeypatch
):
    """Two racing pay requests can both pass the read checks; the unique keys
    on ``orders`` are the last line of defence and must surface as 409."""
    view = _seat_hold(db_session, seated_event, customer)
    real_flush = db_session.flush

    def racing_flush(*args, **kwargs):
        if any(isinstance(obj, Order) for obj in db_session.new):
            raise IntegrityError("INSERT INTO orders", {}, Exception("Duplicate entry"))
        return real_flush(*args, **kwargs)

    monkeypatch.setattr(db_session, "flush", racing_flush)
    with pytest.raises(IdempotencyConflict):
        _start(db_session, customer, view.id, "race-key")


def test_a_provider_outage_leaves_the_hold_payable(seated_event, db_session, customer):
    class DownGateway(FakeGateway):
        def create_payment_intent(self, **kwargs):
            raise ConnectionError("stripe is down")

    view = _seat_hold(db_session, seated_event, customer)
    with pytest.raises(PaymentFailed):
        _start(db_session, customer, view.id, "down-key", gateway=DownGateway())

    assert db_session.scalars(select(Order).where(Order.hold_id == view.id)).first() is None
    assert db_session.get(Hold, view.id).status == "active"

    # The browser retries with the same key once the provider is back.
    retried = _start(db_session, customer, view.id, "down-key")
    assert retried.status == "pending"
    assert retried.client_secret


# ---------------------------------------------------------------------------
# webhooks
# ---------------------------------------------------------------------------


def test_order_is_found_through_metadata_when_the_intent_id_differs(
    seated_event, db_session, customer
):
    view = _seat_hold(db_session, seated_event, customer)
    payment = _start(db_session, customer, view.id, "meta-key")

    result = _succeeded(
        db_session, "evt_meta", "pi_unrelated", metadata={"order_id": payment.order_id}
    )

    assert result["result"] == "fulfilled"
    assert db_session.get(Order, payment.order_id).status == "paid"


def test_second_success_event_for_a_paid_order_is_a_noop(seated_event, db_session, customer):
    order = make_paid_order(db_session, seated_event, customer)
    result = _succeeded(db_session, "evt_again", order.stripe_payment_intent_id)
    assert result["result"] == "already_paid"
    assert len(payments.tickets_for_order(db_session, order.id)) == 1


def test_lost_seat_rolls_the_webhook_back_so_stripe_retries(seated_event, db_session, customer):
    view = _seat_hold(db_session, seated_event, customer)
    payment = _start(db_session, customer, view.id, "lost-key")
    db_session.execute(
        update(EventSeat)
        .where(EventSeat.hold_id == view.id)
        .values(status="available", hold_id=None)
    )
    db_session.commit()

    with pytest.raises(SoldOut):
        _succeeded(db_session, "evt_lost", payment.payment_intent_id)

    assert db_session.scalars(select(StripeEvent)).all() == []
    assert db_session.get(Order, payment.order_id).status == "pending"


def test_late_seated_payment_reacquires_a_still_free_seat(seated_event, db_session, customer):
    view = _seat_hold(db_session, seated_event, customer)
    payment = _start(db_session, customer, view.id, "late-seat")
    _backdate_hold(db_session, view.id)

    result = _succeeded(db_session, "evt_late_seat", payment.payment_intent_id)

    assert result["result"] == "fulfilled_after_expiry"
    seat = db_session.scalars(
        select(EventSeat).where(EventSeat.seat_id == seated_event.seats[0].id)
    ).one()
    assert seat.status == "sold"


def test_late_ga_payment_reacquires_inventory_after_the_sweeper_ran(ga_event, db_session, customer):
    view = _ga_hold(db_session, ga_event, customer, quantity=2)
    payment = _start(db_session, customer, view.id, "late-ga")
    _backdate_hold(db_session, view.id)
    holds.expire_holds()
    db_session.expire_all()

    result = _succeeded(db_session, "evt_late_ga", payment.payment_intent_id)

    assert result["result"] == "fulfilled_after_expiry"
    assert len(payments.tickets_for_order(db_session, payment.order_id)) == 2
    inventory = db_session.scalars(
        select(GAInventory).where(GAInventory.ticket_type_id == ga_event.ticket_type.pk)
    ).one()
    assert inventory.available == 8


def test_late_ga_payment_is_refunded_when_inventory_is_gone(ga_event, db_session, customer):
    view = _ga_hold(db_session, ga_event, customer, quantity=2)
    payment = _start(db_session, customer, view.id, "late-ga-gone")
    _backdate_hold(db_session, view.id)
    holds.expire_holds()
    db_session.execute(
        update(GAInventory)
        .where(GAInventory.ticket_type_id == ga_event.ticket_type.pk)
        .values(available=0)
    )
    db_session.commit()
    db_session.expire_all()

    result = _succeeded(db_session, "evt_late_ga_gone", payment.payment_intent_id)

    assert result["result"] == "refunded_late_payment"
    assert db_session.get(Order, payment.order_id).status == "refunded"


def test_payment_failed_for_unknown_intent(db_session):
    result = payments.handle_webhook_event(
        db_session,
        stripe_event_id="evt_failed_unknown",
        event_type="payment_intent.payment_failed",
        payload={"data": {"object": {"id": "pi_ghost"}}},
    )
    assert result["result"] == "unknown_order"


def test_refund_webhook_refunds_tickets_and_frees_seats(seated_event, db_session, customer):
    order = make_paid_order(db_session, seated_event, customer)

    assert (
        _refunded(db_session, "evt_refund_ok", order.stripe_payment_intent_id)["result"]
        == "refunded"
    )

    db_session.expire_all()
    assert {t.status for t in payments.tickets_for_order(db_session, order.id)} == {"refunded"}
    seat = db_session.scalars(
        select(EventSeat).where(EventSeat.seat_id == seated_event.seats[0].id)
    ).one()
    assert seat.status == "available"


# ---------------------------------------------------------------------------
# refunds
# ---------------------------------------------------------------------------


def test_refund_after_start_keeps_seats_sold(seated_event, db_session, customer):
    order = make_paid_order(db_session, seated_event, customer)
    refund = make_refund(db_session, order)
    seated_event.event.starts_at = timezone.now() - timedelta(minutes=5)
    seated_event.event.save(update_fields=["starts_at"])

    payments.process_refund(db_session, refund_id=refund.id, actor_id=None)

    db_session.expire_all()
    assert db_session.get(Order, order.id).status == "refunded"
    seat = db_session.scalars(
        select(EventSeat).where(EventSeat.seat_id == seated_event.seats[0].id)
    ).one()
    assert seat.status == "sold"


def test_ga_refund_returns_inventory(ga_event, db_session, customer):
    order = make_paid_order(db_session, ga_event, customer, quantity=3)
    refund = make_refund(db_session, order)

    payments.process_refund(db_session, refund_id=refund.id, actor_id=None)

    db_session.expire_all()
    inventory = db_session.scalars(
        select(GAInventory).where(GAInventory.ticket_type_id == ga_event.ticket_type.pk)
    ).one()
    assert inventory.available == 10
    tickets = db_session.scalars(select(Ticket).where(Ticket.order_id == order.id)).all()
    assert len(tickets) == 3


def test_rejected_refund_cannot_be_processed(seated_event, db_session, customer):
    order = make_paid_order(db_session, seated_event, customer)
    refund = make_refund(db_session, order)
    payments.reject_refund(db_session, refund_id=refund.id, actor_id=None, reason="no")

    with pytest.raises(RefundNotAllowed):
        payments.process_refund(db_session, refund_id=refund.id, actor_id=None)


def test_refund_for_an_order_stripe_already_refunded_is_refused(seated_event, db_session, customer):
    order = make_paid_order(db_session, seated_event, customer)
    refund = make_refund(db_session, order)
    _refunded(db_session, "evt_out_of_band", order.stripe_payment_intent_id)

    with pytest.raises(RefundNotAllowed):
        payments.process_refund(db_session, refund_id=refund.id, actor_id=None)


# ---------------------------------------------------------------------------
# reconciliation against a live provider
# ---------------------------------------------------------------------------


def test_reconcile_reports_intents_missing_locally(seated_event, db_session, customer, monkeypatch):
    order = make_paid_order(db_session, seated_event, customer)

    class RemoteGateway(FakeGateway):
        def list_recent_payment_intents(self, limit: int = 100) -> list[str]:
            return [order.stripe_payment_intent_id, "pi_only_in_stripe"]

    monkeypatch.setattr(payments.settings, "stripe_gateway", "live")
    result = payments.reconcile_payments(db_session, gateway=RemoteGateway())

    assert result["problems"] == [
        {"payment_intent_id": "pi_only_in_stripe", "problem": "missing_locally"}
    ]


# ---------------------------------------------------------------------------
# server-side seat pricing
# ---------------------------------------------------------------------------


def test_section_pricing_ignores_a_cheaper_client_supplied_type(seated_event, db_session, customer):
    """A Stalls seat always sells at the Stalls price, whatever the browser sends."""
    from tests.factories import TicketTypeFactory

    balcony = TicketTypeFactory(
        event=seated_event.event, name="Balcony", price_cents=1500, inventory_mode="seated"
    )
    hold = holds.create_hold(
        db_session,
        user_id=customer.pk,
        event_id=seated_event.event.pk,
        seat_ids=[seated_event.seats[0].id],
        ticket_type_id=balcony.pk,
    )
    assert hold.items[0].ticket_type_id == seated_event.ticket_type.pk
    assert hold.total_cents == 5000


def test_mixed_sections_price_each_seat_separately(seated_event, db_session, customer):
    from tests.factories import SeatFactory, SectionFactory, TicketTypeFactory

    balcony_section = SectionFactory(venue=seated_event.venue, name="Balcony")
    balcony_seat = SeatFactory(section=balcony_section, row="D", number="1")
    balcony = TicketTypeFactory(
        event=seated_event.event, name="Balcony", price_cents=1500, inventory_mode="seated"
    )
    hold = holds.create_hold(
        db_session,
        user_id=customer.pk,
        event_id=seated_event.event.pk,
        seat_ids=[seated_event.seats[0].id, balcony_seat.id],
    )
    assert sorted(item.ticket_type_id for item in hold.items) == sorted(
        [seated_event.ticket_type.pk, balcony.pk]
    )
    assert hold.total_cents == 6500


def test_per_type_order_limit_applies_to_mapped_seats(seated_event, db_session, customer):
    seated_event.ticket_type.max_per_order = 2
    seated_event.ticket_type.save(update_fields=["max_per_order"])
    with pytest.raises(InvalidQuantity):
        holds.create_hold(
            db_session,
            user_id=customer.pk,
            event_id=seated_event.event.pk,
            seat_ids=[seat.id for seat in seated_event.seats[:3]],
        )


def test_seat_map_exposes_server_pricing(api_client, seated_event):
    response = api_client.get(f"/api/v1/events/{seated_event.event.slug}/seats")
    seat = response.json()["sections"][0]["seats"][0]
    assert seat["ticket_type_id"] == seated_event.ticket_type.pk
    assert seat["price_cents"] == 5000


def test_ambiguous_sections_use_the_type_the_buyer_names(seated_event, db_session, customer):
    from tests.factories import TicketTypeFactory

    seated_event.ticket_type.name = "Standard"
    seated_event.ticket_type.save(update_fields=["name"])
    vip = TicketTypeFactory(
        event=seated_event.event, name="VIP", price_cents=9000, inventory_mode="seated"
    )
    hold = holds.create_hold(
        db_session,
        user_id=customer.pk,
        event_id=seated_event.event.pk,
        seat_ids=[seated_event.seats[0].id],
        ticket_type_id=vip.pk,
    )
    assert hold.total_cents == 9000


def test_seat_map_is_read_only_and_reports_untouched_seats_as_available(seated_event, db_session):
    from api.app.models import EventSeat
    from api.app.services import inventory

    sections = inventory.seat_map(seated_event.event.pk)
    statuses = {seat["status"] for section in sections for seat in section["seats"]}
    assert statuses == {"available"}
    assert sum(len(section["seats"]) for section in sections) == len(seated_event.seats)
    assert db_session.query(EventSeat).count() == 0  # nothing written on a read
