"""Stripe webhook receiver."""

from __future__ import annotations

import json
import logging
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.app.config import settings
from api.app.db import get_session
from api.app.models import Order
from api.app.services import payments
from api.app.services.errors import InvalidSignature
from api.app.services.notifications import broadcast_paid_order, broadcast_refunded_order

logger = logging.getLogger(__name__)

router = APIRouter(tags=["webhooks"])


def _parse_event(raw: bytes, signature: str | None) -> dict[str, Any]:
    """Verify the signature with Stripe in live mode.

    In explicit fake-gateway mode (local demo / tests) there is no Stripe account
    to sign anything, so the JSON body is trusted. That is never the case in production.
    """
    if settings.stripe_gateway.lower() != "fake" and settings.stripe_secret_key != "sk_test_xxx":
        import stripe

        if not signature:
            raise InvalidSignature("Missing Stripe-Signature header.")
        try:
            event = stripe.Webhook.construct_event(raw, signature, settings.stripe_webhook_secret)
        except Exception as exc:
            raise InvalidSignature("Invalid Stripe signature.") from exc
        return cast(dict[str, Any], dict(event))

    try:
        return cast(dict[str, Any], json.loads(raw))
    except json.JSONDecodeError as exc:
        raise InvalidSignature("Malformed JSON body.") from exc


@router.post("/webhooks/stripe")
async def stripe_webhook(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, Any]:
    raw = await request.body()
    event = _parse_event(raw, request.headers.get("stripe-signature"))
    event_id = str(event.get("id", ""))
    event_type = str(event.get("type", ""))
    if not event_id or not event_type:
        raise InvalidSignature("Stripe event is missing id or type.")

    result = payments.handle_webhook_event(
        session, stripe_event_id=event_id, event_type=event_type, payload=event
    )

    if result["status"] == "processed" and event_type == "payment_intent.succeeded":
        payment_intent_id = str(event.get("data", {}).get("object", {}).get("id", ""))
        broadcast_paid_order(session, payment_intent_id)
        if result.get("result") == "refunded_late_payment":
            from sqlalchemy import select as sa_select

            from api.app.models import Order

            order = session.scalars(
                sa_select(Order).where(Order.stripe_payment_intent_id == payment_intent_id)
            ).first()
            if order is not None:
                from worker.tasks import send_refund_email

                send_refund_email.delay(order.id)

    if result.get("result") == "refunded" and event_type in {"charge.refunded", "refund.updated"}:
        intent_id = str(event.get("data", {}).get("object", {}).get("payment_intent") or "")
        refunded = session.scalars(
            select(Order.id).where(Order.stripe_payment_intent_id == intent_id)
        ).first()
        if refunded is not None:
            broadcast_refunded_order(session, refunded)
        _refresh_availability_for_intent(
            session, str(event.get("data", {}).get("object", {}).get("payment_intent") or "")
        )

    return {"received": True, **result}


def _refresh_availability_for_intent(session: Session, payment_intent_id: str) -> None:
    """A refund confirmed by Stripe may return seats and re-open a sold-out event."""
    from sqlalchemy import select as sa_select

    event_id = session.scalar(
        sa_select(Order.event_id).where(Order.stripe_payment_intent_id == payment_intent_id)
    )
    if event_id is None:
        return
    from worker.tasks import refresh_event_availability

    try:
        refresh_event_availability.delay(int(event_id))
    except Exception:
        logger.warning("could not enqueue availability refresh", exc_info=True)
