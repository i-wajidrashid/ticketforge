"""End-to-end HTTP tests through FastAPI (including the error envelope)."""

from __future__ import annotations

import pytest
from api.app.models import Ticket

from tests.factories import StaffAssignmentFactory, UserFactory
from tests.test_holds import *  # noqa: F403 - reuse fixtures only

pytestmark = pytest.mark.django_db(transaction=True)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_public_catalogue_endpoints(api_client, seated_event):
    listing = api_client.get("/api/v1/events")
    assert listing.status_code == 200
    body = listing.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["slug"] == seated_event.event.slug
    assert body["items"][0]["ticket_types"][0]["price_cents"] == 5000

    detail = api_client.get(f"/api/v1/events/{seated_event.event.slug}")
    assert detail.status_code == 200
    assert detail.json()["title"] == seated_event.event.title

    seats = api_client.get(f"/api/v1/events/{seated_event.event.slug}/seats")
    assert seats.status_code == 200
    assert len(seats.json()["sections"][0]["seats"]) == 5

    missing = api_client.get("/api/v1/events/nope")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "NOT_FOUND"


def test_unauthenticated_requests_use_the_error_envelope(api_client):
    response = api_client.get("/api/v1/me/orders")
    assert response.status_code == 401
    body = response.json()
    assert body["error"]["code"] == "UNAUTHORIZED"
    assert "message" in body["error"]


def test_email_verification_is_required_before_buying(api_client, seated_event):
    from common.security import encode_token

    unverified = UserFactory(is_verified=False)
    response = api_client.post(
        "/api/v1/holds",
        json={"event_id": seated_event.event.pk, "seat_ids": [seated_event.seats[0].id]},
        headers={"Authorization": f"Bearer {encode_token(unverified.pk, unverified.role)}"},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "EMAIL_NOT_VERIFIED"


def test_full_purchase_flow_over_http(api_client, seated_event, customer, customer_token):
    hold_response = api_client.post(
        "/api/v1/holds",
        json={"event_id": seated_event.event.pk, "seat_ids": [seated_event.seats[0].id]},
        headers=_auth(customer_token),
    )
    assert hold_response.status_code == 201, hold_response.text
    hold = hold_response.json()
    assert hold["total_cents"] == 5000
    assert 0 < hold["seconds_left"] <= 600

    payment = api_client.post(
        f"/api/v1/checkout/{hold['id']}/pay",
        headers={**_auth(customer_token), "Idempotency-Key": "http-flow-key-1"},
    )
    assert payment.status_code == 200, payment.text
    order_id = payment.json()["order_id"]

    simulated = api_client.post(
        f"/api/v1/dev/simulate-payment/{order_id}", headers=_auth(customer_token)
    )
    assert simulated.status_code == 200, simulated.text
    assert simulated.json()["result"] == "fulfilled"

    orders = api_client.get("/api/v1/me/orders", headers=_auth(customer_token))
    assert orders.status_code == 200
    assert orders.json()[0]["status"] == "paid"
    ticket_id = orders.json()[0]["tickets"][0]["id"]

    pdf = api_client.get(f"/api/v1/me/tickets/{ticket_id}/pdf", headers=_auth(customer_token))
    assert pdf.status_code == 200
    assert pdf.content.startswith(b"%PDF")

    # The hold has been converted and can no longer be cancelled.
    gone = api_client.delete(f"/api/v1/holds/{hold['id']}", headers=_auth(customer_token))
    assert gone.status_code == 200
    assert gone.json()["status"] == "converted"


def test_scan_endpoint_reports_already_used(api_client, seated_event, customer, customer_token):
    hold = api_client.post(
        "/api/v1/holds",
        json={"event_id": seated_event.event.pk, "seat_ids": [seated_event.seats[1].id]},
        headers=_auth(customer_token),
    ).json()
    payment = api_client.post(
        f"/api/v1/checkout/{hold['id']}/pay",
        headers={**_auth(customer_token), "Idempotency-Key": "scan-http-key"},
    ).json()
    api_client.post(
        f"/api/v1/dev/simulate-payment/{payment['order_id']}", headers=_auth(customer_token)
    )

    from api.app.db import session_factory
    from common.security import encode_token

    staff = UserFactory(role="door_staff")
    StaffAssignmentFactory(user=staff, event=seated_event.event)
    with session_factory()() as session:
        ticket = session.query(Ticket).filter_by(order_id=payment["order_id"]).one()
        qr_token = ticket.qr_token

    headers = {"Authorization": f"Bearer {encode_token(staff.pk, staff.role)}"}
    first = api_client.post("/api/v1/scan", json={"qr_token": qr_token}, headers=headers)
    assert first.status_code == 200
    assert first.json()["result"] == "valid"

    second = api_client.post("/api/v1/scan", json={"qr_token": qr_token}, headers=headers)
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "TICKET_ALREADY_USED"
    assert second.json()["error"]["details"]["checked_in_at"] == first.json()["checked_in_at"]


def test_validation_errors_are_enveloped(api_client, seated_event, customer_token):
    response = api_client.post(
        "/api/v1/holds",
        json={"event_id": seated_event.event.pk, "seat_ids": [], "quantity": 2},
        headers=_auth(customer_token),
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_readiness_reports_both_dependencies(api_client):
    ready = api_client.get("/api/v1/ready")
    assert ready.status_code == 200
    assert ready.json()["checks"] == {"mysql": "ok", "redis": "ok"}


def test_unknown_routes_and_methods_use_the_error_envelope(api_client):
    missing = api_client.get("/api/v1/no-such-thing")
    assert missing.status_code == 404
    assert missing.json() == {"error": {"code": "NOT_FOUND", "message": "Not Found", "details": {}}}

    wrong_method = api_client.put("/api/v1/health")
    assert wrong_method.status_code == 405
    assert wrong_method.json()["error"]["code"] == "METHOD_NOT_ALLOWED"
    assert "GET" in wrong_method.headers["allow"]
