"""HTTP-level webhook behaviour: parsing, signatures, duplicates."""

from __future__ import annotations

import pytest
from api.app.models import Order, Ticket
from api.app.services import holds, payments

pytestmark = pytest.mark.django_db(transaction=True)


def _hold_and_pay(db_session, event, customer):
    hold = holds.create_hold(
        db_session, user_id=customer.pk, event_id=event.event.pk, seat_ids=[event.seats[0].id]
    )
    return payments.start_payment(
        db_session, user_id=customer.pk, hold_id=hold.id, idempotency_key="hook-key"
    )


def test_webhook_duplicate_is_acknowledged(api_client, seated_event, db_session, customer):
    payment = _hold_and_pay(db_session, seated_event, customer)
    body = {
        "id": "evt_http_dup",
        "type": "payment_intent.succeeded",
        "data": {"object": {"id": payment.payment_intent_id}},
    }
    first = api_client.post("/api/v1/webhooks/stripe", json=body)
    second = api_client.post("/api/v1/webhooks/stripe", json=body)

    assert first.status_code == 200
    assert first.json()["status"] == "processed"
    assert second.json()["status"] == "duplicate"
    assert db_session.query(Order).count() == 1
    assert db_session.query(Ticket).count() == 1


def test_malformed_webhook_body_is_rejected(api_client):
    response = api_client.post(
        "/api/v1/webhooks/stripe",
        content=b"{not json",
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_SIGNATURE"


def test_missing_signature_is_rejected_in_live_mode(api_client, monkeypatch):
    from api.app.config import settings

    monkeypatch.setattr(settings, "stripe_gateway", "live")
    monkeypatch.setattr(settings, "stripe_secret_key", "sk_test_realish")
    response = api_client.post(
        "/api/v1/webhooks/stripe", json={"id": "evt", "type": "payment_intent.succeeded"}
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_SIGNATURE"


def test_invalid_signature_is_rejected_in_live_mode(api_client, monkeypatch):
    import stripe
    from api.app.config import settings

    monkeypatch.setattr(settings, "stripe_gateway", "live")
    monkeypatch.setattr(settings, "stripe_secret_key", "sk_test_realish")

    def boom(*args, **kwargs):
        raise ValueError("bad signature")

    monkeypatch.setattr(stripe.Webhook, "construct_event", boom)
    response = api_client.post(
        "/api/v1/webhooks/stripe",
        json={"id": "evt", "type": "payment_intent.succeeded"},
        headers={"Stripe-Signature": "t=1,v1=deadbeef"},
    )
    assert response.status_code == 400


def test_unauthenticated_scan_is_rejected(api_client):
    response = api_client.post("/api/v1/scan", json={"qr_token": "abc.def"})
    assert response.status_code == 401
