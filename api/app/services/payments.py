"""Checkout, Stripe webhooks and refunds.

Idempotency is enforced at three layers:
1. ``Order.idempotency_key`` -- a retried pay request reuses one order;
2. ``StripeEvent.stripe_event_id`` -- a replayed webhook is a no-op;
3. ``Refund.stripe_refund_id`` -- a retried refund never double-refunds.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from common.security import make_qr_token
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from api.app import core_repo
from api.app.config import settings
from api.app.db import affected
from api.app.models import (
    AuditLog,
    EventSeat,
    GAInventory,
    Hold,
    Order,
    Refund,
    StripeEvent,
    Ticket,
    new_uuid,
    utcnow,
)
from api.app.services import rate_limit
from api.app.services.errors import (
    HoldExpired,
    HoldNotActive,
    IdempotencyConflict,
    NotFound,
    PaymentFailed,
    RefundNotAllowed,
    SoldOut,
)
from api.app.services.holds import load_hold, release_hold
from api.app.services.stripe_gateway import PaymentGateway, get_gateway

logger = logging.getLogger(__name__)


class _ReacquireFailed(Exception):
    pass


@dataclass(slots=True)
class PaymentStart:
    order_id: str
    payment_intent_id: str
    client_secret: str
    amount_cents: int
    currency: str
    status: str


def audit(
    session: Session,
    *,
    actor_id: int | None,
    action: str,
    entity: str,
    entity_id: str,
    data: dict[str, Any],
) -> None:
    session.add(
        AuditLog(
            actor_id=actor_id, action=action, entity=entity, entity_id=str(entity_id), data=data
        )
    )


def hold_total(session: Session, hold: Hold) -> tuple[int, str]:
    ticket_types = {t.id: t for t in core_repo.ticket_types_for_event(hold.event_id)}
    total = 0
    currency = "USD"
    for item in hold.items:
        ticket_type = ticket_types.get(item.ticket_type_id)
        if ticket_type is None:
            continue
        total += ticket_type.price_cents * item.quantity
        currency = ticket_type.currency
    return total, currency


def _order_payment_start(order: Order) -> PaymentStart:
    return PaymentStart(
        order_id=order.id,
        payment_intent_id=order.stripe_payment_intent_id or "",
        client_secret=order.client_secret or "",
        amount_cents=order.total_cents,
        currency=order.currency,
        status=order.status,
    )


def start_payment(
    session: Session,
    *,
    user_id: int,
    hold_id: str,
    idempotency_key: str,
    gateway: PaymentGateway | None = None,
) -> PaymentStart:
    gateway = gateway or get_gateway()
    rate_limit.enforce(f"pay:{user_id}", 30)

    existing = session.scalars(
        select(Order).where(Order.idempotency_key == idempotency_key)
    ).first()
    if existing is not None:
        if existing.user_id != user_id:
            raise IdempotencyConflict("That idempotency key belongs to another customer.")
        return _order_payment_start(existing)

    hold = load_hold(session, hold_id, user_id=user_id)
    # Serialise on the hold row: two concurrent "pay" clicks produce one order.
    hold = session.get(Hold, hold.id, with_for_update=True) or hold

    existing_for_hold = session.scalars(select(Order).where(Order.hold_id == hold.id)).first()
    if existing_for_hold is not None:
        return _order_payment_start(existing_for_hold)

    now = utcnow()
    if hold.status == "expired" or (hold.status == "active" and hold.expires_at <= now):
        if hold.status == "active":
            release_hold(session, hold, status="expired")
            session.commit()
        raise HoldExpired("Your hold expired. Pick your seats again.")
    if hold.status != "active":
        raise HoldNotActive("This hold can no longer be paid.")

    total, currency = hold_total(session, hold)
    order = Order(
        user_id=user_id,
        event_id=hold.event_id,
        hold_id=hold.id,
        total_cents=total,
        currency=currency,
        status="pending",
        idempotency_key=idempotency_key,
    )
    session.add(order)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        raise IdempotencyConflict("This payment was already started.") from None

    try:
        intent = gateway.create_payment_intent(
            amount_cents=total,
            currency=currency,
            metadata={"order_id": order.id, "hold_id": hold.id, "event_id": str(hold.event_id)},
            idempotency_key=idempotency_key,
        )
    except Exception as exc:
        order.status = "failed"
        session.commit()
        logger.exception("stripe payment intent creation failed")
        raise PaymentFailed("The payment provider rejected the request. Please retry.") from exc

    order.stripe_payment_intent_id = intent.id
    order.client_secret = intent.client_secret
    audit(
        session,
        actor_id=user_id,
        action="payment_started",
        entity="order",
        entity_id=order.id,
        data={"amount_cents": total},
    )
    session.commit()
    return PaymentStart(
        order_id=order.id,
        payment_intent_id=intent.id,
        client_secret=intent.client_secret,
        amount_cents=total,
        currency=currency,
        status=order.status,
    )


# ---------------------------------------------------------------------------
# Webhooks
# ---------------------------------------------------------------------------


def handle_webhook_event(
    session: Session,
    *,
    stripe_event_id: str,
    event_type: str,
    payload: dict[str, Any],
    gateway: PaymentGateway | None = None,
) -> dict[str, Any]:
    gateway = gateway or get_gateway()
    record = StripeEvent(stripe_event_id=stripe_event_id, type=event_type, payload=payload)
    session.add(record)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        logger.info(
            "duplicate stripe event ignored",
            extra={"extra_fields": {"stripe_event_id": stripe_event_id}},
        )
        return {"status": "duplicate", "result": "already_handled"}

    result = "ignored"
    try:
        if event_type == "payment_intent.succeeded":
            result = _fulfill(session, payload, gateway)
        elif event_type == "payment_intent.payment_failed":
            result = _fail_payment(session, payload)
        elif event_type in {"charge.refunded", "refund.updated"}:
            result = _refund_from_webhook(session, payload)
        record.processed_at = utcnow()
        session.commit()
    except Exception:
        session.rollback()
        raise
    return {"status": "processed", "result": result}


def _object(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get("data", {})
    obj = data.get("object", {}) if isinstance(data, dict) else {}
    return obj if isinstance(obj, dict) else {}


def _find_order_by_intent(
    session: Session, payment_intent_id: str, payload: dict[str, Any]
) -> Order | None:
    order = session.scalars(
        select(Order).where(Order.stripe_payment_intent_id == payment_intent_id).with_for_update()
    ).first()
    if order is not None:
        return order
    metadata = _object(payload).get("metadata") or {}
    order_id = metadata.get("order_id") if isinstance(metadata, dict) else None
    if order_id:
        return session.get(Order, str(order_id), with_for_update=True)
    return None


def _fulfill(session: Session, payload: dict[str, Any], gateway: PaymentGateway) -> str:
    obj = _object(payload)
    intent_id = str(obj.get("id", ""))
    order = _find_order_by_intent(session, intent_id, payload)
    if order is None:
        logger.warning(
            "webhook for unknown payment intent", extra={"extra_fields": {"intent": intent_id}}
        )
        return "unknown_order"
    if order.status == "paid":
        return "already_paid"
    if order.status in {"refunded", "partially_refunded"}:
        return "already_refunded"

    hold = session.get(Hold, order.hold_id, with_for_update=True)
    if hold is None:
        return "missing_hold"

    now = utcnow()
    if hold.status == "active" and hold.expires_at > now:
        _convert(session, order, hold)
        return "fulfilled"

    if hold.status == "active":
        release_hold(session, hold, status="expired")
        session.flush()

    if _try_reacquire(session, hold):
        hold.status = "active"
        _convert(session, order, hold)
        return "fulfilled_after_expiry"

    _refund_late_payment(session, order, gateway)
    return "refunded_late_payment"


def _try_reacquire(session: Session, hold: Hold) -> bool:
    try:
        with session.begin_nested():
            for item in hold.items:
                if item.event_seat_id is not None:
                    result = session.execute(
                        update(EventSeat)
                        .where(EventSeat.id == item.event_seat_id, EventSeat.status == "available")
                        .values(status="held", hold_id=hold.id, version=EventSeat.version + 1)
                    )
                    if affected(result) != 1:
                        raise _ReacquireFailed
                else:
                    result = session.execute(
                        update(GAInventory)
                        .where(
                            GAInventory.ticket_type_id == item.ticket_type_id,
                            GAInventory.available >= item.quantity,
                        )
                        .values(available=GAInventory.available - item.quantity)
                    )
                    if affected(result) != 1:
                        raise _ReacquireFailed
        return True
    except _ReacquireFailed:
        return False


def _convert(session: Session, order: Order, hold: Hold) -> None:
    ticket_count = 0
    revenue = order.total_cents
    for item in hold.items:
        if item.event_seat_id is not None:
            result = session.execute(
                update(EventSeat)
                .where(
                    EventSeat.id == item.event_seat_id,
                    EventSeat.hold_id == hold.id,
                    EventSeat.status == "held",
                )
                .values(status="sold", ticket_type_id=item.ticket_type_id)
            )
            if affected(result) != 1:
                raise SoldOut("A held seat was lost before payment completed.")
            session.add(_ticket(order, item.ticket_type_id, item.event_seat_id))
            ticket_count += 1
        else:
            for _ in range(item.quantity):
                session.add(_ticket(order, item.ticket_type_id, None))
                ticket_count += 1

    hold.status = "converted"
    order.status = "paid"
    audit(
        session,
        actor_id=order.user_id,
        action="order_paid",
        entity="order",
        entity_id=order.id,
        data={"tickets": ticket_count, "total_cents": revenue},
    )


def _ticket(order: Order, ticket_type_id: int, event_seat_id: int | None) -> Ticket:
    ticket_id = new_uuid()
    return Ticket(
        id=ticket_id,
        order_id=order.id,
        ticket_type_id=ticket_type_id,
        event_seat_id=event_seat_id,
        qr_token=make_qr_token(ticket_id),
        status="valid",
    )


def _refund_late_payment(session: Session, order: Order, gateway: PaymentGateway) -> None:
    intent_id = order.stripe_payment_intent_id or ""
    result = gateway.create_refund(
        payment_intent_id=intent_id,
        amount_cents=order.total_cents,
        idempotency_key=f"late-{order.id}",
    )
    session.add(
        Refund(
            order_id=order.id,
            amount_cents=order.total_cents,
            status="succeeded",
            stripe_refund_id=result.id,
            reason="payment succeeded after the hold expired and inventory was resold",
            processed_at=utcnow(),
        )
    )
    order.status = "refunded"
    audit(
        session,
        actor_id=None,
        action="late_payment_refunded",
        entity="order",
        entity_id=order.id,
        data={"stripe_refund_id": result.id, "amount_cents": order.total_cents},
    )


def _fail_payment(session: Session, payload: dict[str, Any]) -> str:
    obj = _object(payload)
    order = _find_order_by_intent(session, str(obj.get("id", "")), payload)
    if order is None:
        return "unknown_order"
    if order.status == "pending":
        order.status = "failed"
        audit(
            session,
            actor_id=None,
            action="payment_failed",
            entity="order",
            entity_id=order.id,
            data={},
        )
    return "failed"


def _refund_from_webhook(session: Session, payload: dict[str, Any]) -> str:
    obj = _object(payload)
    intent_id = str(obj.get("payment_intent") or "")
    order = session.scalars(
        select(Order).where(Order.stripe_payment_intent_id == intent_id).with_for_update()
    ).first()
    if order is None:
        return "unknown_order"
    _mark_order_refunded(session, order, reason="refund confirmed by Stripe webhook")
    return "refunded"


def _mark_order_refunded(session: Session, order: Order, *, reason: str) -> None:
    order.status = "refunded"
    for ticket in session.scalars(select(Ticket).where(Ticket.order_id == order.id)).all():
        ticket.status = "refunded"
    release_order_seats(session, order)
    audit(
        session,
        actor_id=None,
        action="order_refunded",
        entity="order",
        entity_id=order.id,
        data={"reason": reason},
    )


def release_order_seats(session: Session, order: Order) -> None:
    """Return sold inventory to sale -- only while the event has not started."""
    event = core_repo.get_event(order.event_id)
    if event is None or event.starts_at <= utcnow():
        return
    seat_ids: list[int] = []
    ga_counts: dict[int, int] = {}
    for ticket in session.scalars(select(Ticket).where(Ticket.order_id == order.id)).all():
        if ticket.event_seat_id is not None:
            seat_ids.append(ticket.event_seat_id)
        else:
            ga_counts[ticket.ticket_type_id] = ga_counts.get(ticket.ticket_type_id, 0) + 1

    if seat_ids:
        session.execute(
            update(EventSeat)
            .where(EventSeat.id.in_(seat_ids), EventSeat.status == "sold")
            .values(status="available", hold_id=None, version=EventSeat.version + 1)
        )
    for ticket_type_id, count in ga_counts.items():
        session.execute(
            update(GAInventory)
            .where(GAInventory.ticket_type_id == ticket_type_id)
            .values(available=GAInventory.available + count)
        )


# ---------------------------------------------------------------------------
# Refunds
# ---------------------------------------------------------------------------


def request_refund(session: Session, *, user_id: int, order_id: str, reason: str = "") -> Refund:
    order = session.get(Order, order_id, with_for_update=True)
    if order is None or order.user_id != user_id:
        raise NotFound("Order not found.")
    if order.status != "paid":
        raise RefundNotAllowed("Only paid orders can be refunded.")

    event = core_repo.get_event(order.event_id)
    if event is not None and event.starts_at <= utcnow():
        raise RefundNotAllowed("This event has already started.")

    existing = session.scalars(
        select(Refund).where(
            Refund.order_id == order.id, Refund.status.in_(["requested", "processing", "succeeded"])
        )
    ).first()
    if existing is not None:
        raise RefundNotAllowed("A refund for this order is already in progress.")

    refund = Refund(
        order_id=order.id, amount_cents=order.total_cents, status="requested", reason=reason[:255]
    )
    session.add(refund)
    session.flush()
    audit(
        session,
        actor_id=user_id,
        action="refund_requested",
        entity="refund",
        entity_id=str(refund.id),
        data={"order_id": order.id},
    )
    session.commit()
    return refund


def process_refund(
    session: Session,
    *,
    refund_id: int,
    actor_id: int | None,
    gateway: PaymentGateway | None = None,
) -> dict[str, Any]:
    gateway = gateway or get_gateway()
    refund = session.get(Refund, refund_id, with_for_update=True)
    if refund is None:
        raise NotFound("Refund not found.")
    if refund.status == "succeeded":
        return {"status": "already_refunded", "refund_id": refund.id}
    if refund.status not in {"requested", "processing", "failed"}:
        raise RefundNotAllowed(f"Cannot process a refund in state '{refund.status}'.")

    order = session.get(Order, refund.order_id, with_for_update=True)
    if order is None:
        raise NotFound("Order not found.")
    if order.status not in {"paid", "partially_refunded"}:
        raise RefundNotAllowed("This order is not refundable.")

    result = gateway.create_refund(
        payment_intent_id=order.stripe_payment_intent_id or "",
        amount_cents=refund.amount_cents,
        idempotency_key=f"refund-{refund.id}",
    )
    refund.stripe_refund_id = result.id
    refund.status = "succeeded"
    refund.processed_at = utcnow()
    order.status = "refunded"
    for ticket in session.scalars(select(Ticket).where(Ticket.order_id == order.id)).all():
        ticket.status = "refunded"
    release_order_seats(session, order)
    audit(
        session,
        actor_id=actor_id,
        action="refund_completed",
        entity="refund",
        entity_id=str(refund.id),
        data={"stripe_refund_id": result.id, "amount_cents": refund.amount_cents},
    )
    session.commit()
    return {"status": "refunded", "refund_id": refund.id, "stripe_refund_id": result.id}


def reject_refund(
    session: Session, *, refund_id: int, actor_id: int | None, reason: str = ""
) -> dict[str, Any]:
    refund = session.get(Refund, refund_id, with_for_update=True)
    if refund is None:
        raise NotFound("Refund not found.")
    if refund.status in {"succeeded", "rejected"}:
        return {"status": refund.status, "refund_id": refund.id}
    refund.status = "rejected"
    refund.reason = (reason or refund.reason)[:255]
    refund.processed_at = utcnow()
    audit(
        session,
        actor_id=actor_id,
        action="refund_rejected",
        entity="refund",
        entity_id=str(refund.id),
        data={"reason": reason},
    )
    session.commit()
    return {"status": "rejected", "refund_id": refund.id}


def refunds_for_event(
    session: Session, event_id: int, *, status: str = "requested"
) -> list[Refund]:
    order_ids = select(Order.id).where(Order.event_id == event_id)
    return list(
        session.scalars(
            select(Refund).where(Refund.order_id.in_(order_ids), Refund.status == status)
        ).all()
    )


def reconcile_payments(session: Session, gateway: PaymentGateway | None = None) -> dict[str, Any]:
    """Nightly consistency check between local orders and the payment provider."""
    gateway = gateway or get_gateway()
    problems: list[dict[str, Any]] = []

    paid_without_tickets = (
        session.execute(
            select(Order.id)
            .outerjoin(Ticket, Ticket.order_id == Order.id)
            .where(Order.status == "paid")
            .group_by(Order.id)
            .having(func.count(Ticket.id) == 0)
        )
        .scalars()
        .all()
    )
    problems.extend(
        {"order_id": order_id, "problem": "paid_without_tickets"}
        for order_id in paid_without_tickets
    )

    remote_ids = (
        set(gateway.list_recent_payment_intents()) if settings.stripe_gateway != "fake" else set()
    )
    if remote_ids:
        local_ids = set(
            session.scalars(
                select(Order.stripe_payment_intent_id).where(
                    Order.stripe_payment_intent_id.is_not(None), Order.status == "paid"
                )
            ).all()
        )
        problems.extend(
            {"payment_intent_id": intent_id, "problem": "missing_locally"}
            for intent_id in remote_ids - local_ids
        )

    for problem in problems:
        audit(
            session,
            actor_id=None,
            action="reconciliation_mismatch",
            entity="order",
            entity_id=str(problem.get("order_id", "?")),
            data=problem,
        )
    session.commit()
    return {"checked": True, "problems": problems}


def orders_for_user(session: Session, user_id: int) -> list[Order]:
    return list(
        session.scalars(
            select(Order)
            .where(Order.user_id == user_id)
            .order_by(Order.created_at.desc())
            .limit(100)
        ).all()
    )


def tickets_for_order(session: Session, order_id: str) -> list[Ticket]:
    return list(session.scalars(select(Ticket).where(Ticket.order_id == order_id)).all())
