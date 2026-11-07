"""Authenticated checkout: holds, payment start and the customer's orders."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.app.db import get_session
from api.app.models import Order, Ticket
from api.app.realtime import seat_updates
from api.app.schemas import (
    HoldCreate,
    HoldOut,
    OrderOut,
    PaymentOut,
    RefundOut,
    RefundRequest,
    ScanRequest,
    TicketOut,
)
from api.app.security import Principal, require_verified
from api.app.services import holds as hold_service
from api.app.services import payments as payment_service
from api.app.services import tickets as ticket_service
from api.app.services.errors import NotFound, TicketAlreadyUsed

router = APIRouter(tags=["checkout"])


@router.post("/holds", response_model=HoldOut, status_code=201)
def create_hold(
    payload: HoldCreate,
    principal: Annotated[Principal, Depends(require_verified)],
    session: Annotated[Session, Depends(get_session)],
) -> HoldOut:
    view = hold_service.create_hold(
        session,
        user_id=principal.user_id,
        event_id=payload.event_id,
        seat_ids=payload.seat_ids,
        ticket_type_id=payload.ticket_type_id,
        quantity=payload.quantity,
    )
    seat_updates(view.event_id, hold_service.seat_changes_for_hold(session, view.id, "held"))
    return HoldOut.model_validate(view)


@router.get("/holds/{hold_id}", response_model=HoldOut)
def get_hold(
    hold_id: str,
    principal: Annotated[Principal, Depends(require_verified)],
    session: Annotated[Session, Depends(get_session)],
) -> HoldOut:
    return HoldOut.model_validate(
        hold_service.get_hold_view(session, hold_id, user_id=principal.user_id)
    )


@router.delete("/holds/{hold_id}", response_model=HoldOut)
def delete_hold(
    hold_id: str,
    principal: Annotated[Principal, Depends(require_verified)],
    session: Annotated[Session, Depends(get_session)],
) -> HoldOut:
    view = hold_service.cancel_hold(session, hold_id, user_id=principal.user_id)
    seat_updates(view.event_id, hold_service.seat_changes_for_hold(session, hold_id, "available"))
    return HoldOut.model_validate(view)


@router.post("/checkout/{hold_id}/pay", response_model=PaymentOut)
def start_payment(
    hold_id: str,
    principal: Annotated[Principal, Depends(require_verified)],
    session: Annotated[Session, Depends(get_session)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=120)],
) -> PaymentOut:
    result = payment_service.start_payment(
        session,
        user_id=principal.user_id,
        hold_id=hold_id,
        idempotency_key=idempotency_key,
    )
    return PaymentOut(
        order_id=result.order_id,
        payment_intent_id=result.payment_intent_id,
        client_secret=result.client_secret,
        amount_cents=result.amount_cents,
        currency=result.currency,
        status=result.status,
    )


@router.get("/me/orders", response_model=list[OrderOut])
def my_orders(
    principal: Annotated[Principal, Depends(require_verified)],
    session: Annotated[Session, Depends(get_session)],
) -> list[OrderOut]:
    orders = payment_service.orders_for_user(session, principal.user_id)
    return [_order_out(session, order) for order in orders]


def _order_out(session: Session, order: Order) -> OrderOut:
    tickets = list(session.scalars(select(Ticket).where(Ticket.order_id == order.id)).all())
    return OrderOut(
        id=order.id,
        event_id=order.event_id,
        status=order.status,
        total_cents=order.total_cents,
        currency=order.currency,
        created_at=order.created_at,
        tickets=[TicketOut.model_validate(ticket) for ticket in tickets],
    )


@router.get("/me/tickets/{ticket_id}/pdf")
def download_ticket_pdf(
    ticket_id: str,
    principal: Annotated[Principal, Depends(require_verified)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    from worker.pdf import render_ticket_pdf

    payload = ticket_service.ticket_pdf_payload(
        session, user_id=principal.user_id, ticket_id=ticket_id
    )
    pdf = render_ticket_pdf(**payload)
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="ticket-{ticket_id}.pdf"'},
    )


@router.post("/me/orders/{order_id}/refund-request", response_model=RefundOut, status_code=201)
def request_refund(
    order_id: str,
    payload: RefundRequest,
    principal: Annotated[Principal, Depends(require_verified)],
    session: Annotated[Session, Depends(get_session)],
) -> RefundOut:
    refund = payment_service.request_refund(
        session, user_id=principal.user_id, order_id=order_id, reason=payload.reason
    )
    return RefundOut(
        id=refund.id,
        order_id=refund.order_id,
        amount_cents=refund.amount_cents,
        status=refund.status,
        reason=refund.reason,
    )


@router.post("/scan")
def scan(
    payload: ScanRequest,
    principal: Annotated[Principal, Depends(require_verified)],
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, str | int | None]:
    result = ticket_service.scan_ticket(
        session, qr_token=payload.qr_token, staff_user_id=principal.user_id
    )
    if result.get("result") == "already_used":
        raise TicketAlreadyUsed(
            "This ticket was already scanned.",
            {
                "checked_in_at": result.get("checked_in_at"),
                "checked_in_by": result.get("checked_in_by"),
            },
        )
    return result


@router.post("/dev/simulate-payment/{order_id}")
def simulate_payment(
    order_id: str,
    principal: Annotated[Principal, Depends(require_verified)],
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, str]:
    """Local-demo helper: emit the Stripe event the fake gateway never sends.

    Disabled the moment a real Stripe key is configured, so it can never touch
    production money.
    """
    from api.app.services.stripe_gateway import FakeGateway, get_gateway

    if not isinstance(get_gateway(), FakeGateway):
        raise NotFound("Not available in live payment mode.")
    order = session.get(Order, order_id)
    if order is None or order.user_id != principal.user_id or not order.stripe_payment_intent_id:
        raise NotFound("Order not found.")
    event_id = f"evt_sim_{order.id}"
    payload = {
        "id": event_id,
        "type": "payment_intent.succeeded",
        "data": {
            "object": {"id": order.stripe_payment_intent_id, "metadata": {"order_id": order.id}}
        },
    }
    result = payment_service.handle_webhook_event(
        session,
        stripe_event_id=event_id,
        event_type="payment_intent.succeeded",
        payload=payload,
    )
    from api.app.services.notifications import broadcast_paid_order

    broadcast_paid_order(session, order.stripe_payment_intent_id)
    return {"status": result["status"], "result": str(result["result"]), "order_id": order.id}
