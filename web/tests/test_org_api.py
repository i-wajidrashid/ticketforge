from __future__ import annotations

from datetime import timedelta

import pytest
from common.security import encode_token
from django.utils import timezone
from rest_framework.test import APIClient
from tests.factories import (
    EventFactory,
    OrganizerFactory,
    UserFactory,
    VenueFactory,
)

pytestmark = pytest.mark.django_db


def _client_for(user) -> APIClient:
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {encode_token(user.pk, user.role)}")
    return client


def _event_payload(venue) -> dict:
    now = timezone.now()
    return {
        "title": "Midnight Static",
        "description": "A loud night.",
        "category": "music",
        "venue": venue.pk,
        "starts_at": (now + timedelta(days=10)).isoformat(),
        "ends_at": (now + timedelta(days=10, hours=3)).isoformat(),
    }


def test_customer_cannot_use_the_organizer_api():
    customer = UserFactory(role="customer")
    response = _client_for(customer).get("/org/api/v1/events/")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "PERMISSION_DENIED"


def test_organizer_creates_event_ticket_types_and_publishes():
    organizer = OrganizerFactory()
    venue = VenueFactory()
    client = _client_for(organizer.user)

    created = client.post("/org/api/v1/events/", _event_payload(venue), format="json")
    assert created.status_code == 201, created.content
    event_id = created.json()["id"]
    assert created.json()["status"] == "draft"

    ticket_type = client.post(
        f"/org/api/v1/events/{event_id}/ticket-types/",
        {
            "name": "General",
            "price_cents": 2500,
            "quantity_total": 100,
            "max_per_order": 4,
            "inventory_mode": "ga",
        },
        format="json",
    )
    assert ticket_type.status_code == 201, ticket_type.content

    published = client.post(f"/org/api/v1/events/{event_id}/publish/")
    assert published.status_code == 200
    assert published.json()["status"] == "published"

    listing = client.get("/org/api/v1/events/")
    assert listing.status_code == 200
    assert listing.json()["count"] == 1


def test_publish_requires_a_ticket_type():
    organizer = OrganizerFactory()
    venue = VenueFactory()
    client = _client_for(organizer.user)
    event_id = client.post("/org/api/v1/events/", _event_payload(venue), format="json").json()["id"]

    response = client.post(f"/org/api/v1/events/{event_id}/publish/")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_events_are_scoped_to_the_owning_organizer():
    owner = OrganizerFactory()
    intruder = OrganizerFactory()
    event = EventFactory(organizer=owner)

    response = _client_for(intruder.user).get(f"/org/api/v1/events/{event.pk}/")
    assert response.status_code == 404

    listing = _client_for(intruder.user).get("/org/api/v1/events/")
    assert listing.json()["count"] == 0


def test_staff_assignment_promotes_the_user():
    organizer = OrganizerFactory()
    event = EventFactory(organizer=organizer)
    staff = UserFactory(role="customer")
    client = _client_for(organizer.user)

    response = client.post(
        f"/org/api/v1/events/{event.pk}/staff/", {"email": staff.email}, format="json"
    )
    assert response.status_code == 201
    staff.refresh_from_db()
    assert staff.role == "door_staff"

    listing = client.get(f"/org/api/v1/events/{event.pk}/staff/")
    assert len(listing.json()) == 1

    removed = client.delete(
        f"/org/api/v1/events/{event.pk}/staff/", {"email": staff.email}, format="json"
    )
    assert removed.status_code == 200


def test_sales_summary_is_empty_for_a_fresh_event():
    organizer = OrganizerFactory()
    event = EventFactory(organizer=organizer)
    response = _client_for(organizer.user).get(f"/org/api/v1/events/{event.pk}/sales/")
    assert response.status_code == 200
    body = response.json()
    assert body["revenue_cents"] == 0
    assert body["tickets_sold"] == 0
    assert body["check_in_rate"] == 0.0


def test_refund_approval_enqueues_a_celery_task(monkeypatch):
    from api.app.db import session_factory
    from api.app.models import Hold, Order, Refund, utcnow

    organizer = OrganizerFactory()
    event = EventFactory(organizer=organizer)
    with session_factory()() as session:
        hold = Hold(user_id=1, event_id=event.pk, expires_at=utcnow(), status="converted")
        session.add(hold)
        session.flush()
        order = Order(
            user_id=1,
            event_id=event.pk,
            hold_id=hold.id,
            total_cents=5000,
            currency="USD",
            status="paid",
            idempotency_key="test-refund-key",
            stripe_payment_intent_id="pi_test_refund",
        )
        session.add(order)
        session.flush()
        refund = Refund(order_id=order.id, amount_cents=5000, status="requested", reason="test")
        session.add(refund)
        session.commit()
        refund_id = refund.id

    called: list[tuple] = []
    import worker.tasks

    monkeypatch.setattr(
        worker.tasks.process_refund, "delay", lambda *args, **kwargs: called.append(args)
    )

    response = _client_for(organizer.user).post(f"/org/api/v1/refunds/{refund_id}/approve/")
    assert response.status_code == 202
    assert called and called[0][0] == refund_id


def test_refund_listing_is_scoped_to_the_organizer():
    organizer = OrganizerFactory()
    response = _client_for(organizer.user).get("/org/api/v1/refunds/")
    assert response.status_code == 200
    assert response.json() == []
