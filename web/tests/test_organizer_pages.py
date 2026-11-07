"""Server-rendered organizer dashboard, its actions, and the door scanner page.

These run with ``transaction=True`` because checkout data is written through
SQLAlchemy on its own connection, which must see committed Django rows.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from api.app.models import Order, Refund, Ticket, utcnow
from django.contrib.messages import get_messages
from django.urls import reverse
from django.utils import timezone
from tests.factories import (
    EventFactory,
    OrganizerFactory,
    StaffAssignmentFactory,
    TicketTypeFactory,
    UserFactory,
)
from tests.helpers import make_paid_order, make_refund

from web.events.models import EventStatus, StaffAssignment

pytestmark = pytest.mark.django_db(transaction=True)


def _messages(response) -> list[str]:
    return [str(message) for message in get_messages(response.wsgi_request)]


@pytest.fixture
def organizer_client(client, ga_event):
    client.force_login(ga_event.organizer_user)
    return client


# ------------------------------------------------------------------ access


def test_dashboard_redirects_anonymous_visitors_to_login(client):
    response = client.get(reverse("organizer:dashboard"))
    assert response.status_code == 302
    assert reverse("accounts:login") in response["Location"]


def test_customers_get_a_403_on_the_dashboard(client, customer):
    client.force_login(customer)
    assert client.get(reverse("organizer:dashboard")).status_code == 403


def test_dashboard_lists_only_the_organizers_own_events(organizer_client, ga_event):
    someone_else = EventFactory(title="Not Yours")
    response = organizer_client.get(reverse("organizer:dashboard"))
    assert response.status_code == 200
    assert ga_event.event.title in response.content.decode()
    assert someone_else.title not in response.content.decode()


def test_another_organizers_event_is_a_404(organizer_client):
    other = EventFactory()
    response = organizer_client.get(reverse("organizer:event", args=[other.pk]))
    assert response.status_code == 404


# ------------------------------------------------------------------ figures


def test_event_dashboard_counts_a_multi_ticket_order_once(
    organizer_client, ga_event, db_session, customer
):
    order = make_paid_order(db_session, ga_event, customer, quantity=2)
    response = organizer_client.get(reverse("organizer:event", args=[ga_event.event.pk]))
    assert response.status_code == 200

    sales = response.context["sales"]
    assert sales["tickets_sold"] == 2
    assert sales["paid_orders"] == 1
    assert sales["revenue_cents"] == order.total_cents == 2 * ga_event.ticket_type.price_cents
    assert response.context["remaining"] == ga_event.ticket_type.quantity_total - 2
    chart = response.context["chart"]
    assert chart is not None
    assert chart["total_cents"] == order.total_cents


def test_dashboard_totals_match_the_event_figures(organizer_client, ga_event, db_session, customer):
    order = make_paid_order(db_session, ga_event, customer, quantity=3)
    response = organizer_client.get(reverse("organizer:dashboard"))
    totals = response.context["totals"]
    assert totals["revenue_cents"] == order.total_cents
    assert totals["tickets_sold"] == 3
    assert totals["upcoming"] == 1


# ------------------------------------------------------------------ actions


def test_publish_moves_a_draft_live(organizer_client, ga_event):
    event = ga_event.event
    event.status = EventStatus.DRAFT
    event.save(update_fields=["status"])

    response = organizer_client.post(reverse("organizer:publish", args=[event.pk]))
    assert response.status_code == 302
    event.refresh_from_db()
    assert event.status == EventStatus.PUBLISHED


def test_publish_reports_a_broken_rule_instead_of_failing(client):
    organizer = OrganizerFactory()
    event = EventFactory(organizer=organizer, status=EventStatus.DRAFT)  # no ticket types
    client.force_login(organizer.user)

    response = client.post(reverse("organizer:publish", args=[event.pk]))
    assert response.status_code == 302
    event.refresh_from_db()
    assert event.status == EventStatus.DRAFT
    assert any("ticket type" in message for message in _messages(response))


def test_cancel_needs_the_exact_event_title(organizer_client, ga_event):
    response = organizer_client.post(
        reverse("organizer:cancel", args=[ga_event.event.pk]), {"confirm_title": "wrong"}
    )
    assert response.status_code == 302
    ga_event.event.refresh_from_db()
    assert ga_event.event.status != EventStatus.CANCELLED


def test_cancel_refunds_every_paid_order(organizer_client, ga_event, db_session, customer):
    order = make_paid_order(db_session, ga_event, customer, quantity=1)
    response = organizer_client.post(
        reverse("organizer:cancel", args=[ga_event.event.pk]),
        {"confirm_title": ga_event.event.title, "reason": "Venue flooded"},
    )
    assert response.status_code == 302
    ga_event.event.refresh_from_db()
    assert ga_event.event.status == EventStatus.CANCELLED

    db_session.expire_all()
    assert db_session.get(Order, order.id).status == "refunded"


def test_door_team_add_and_remove(organizer_client, ga_event):
    helper = UserFactory(role="customer")
    url = reverse("organizer:staff-add", args=[ga_event.event.pk])

    added = organizer_client.post(url, {"email": helper.email.upper()})
    assert added.status_code == 302
    assert added["Location"].endswith("#door-team")
    assert StaffAssignment.objects.filter(user=helper, event=ga_event.event).exists()
    helper.refresh_from_db()
    assert helper.role == "door_staff"

    again = organizer_client.post(url, {"email": helper.email})
    assert any("already on the door team" in message for message in _messages(again))

    organizer_client.post(
        reverse("organizer:staff-remove", args=[ga_event.event.pk]), {"email": helper.email}
    )
    assert not StaffAssignment.objects.filter(user=helper, event=ga_event.event).exists()


def test_adding_an_unknown_email_explains_what_to_do(organizer_client, ga_event):
    response = organizer_client.post(
        reverse("organizer:staff-add", args=[ga_event.event.pk]), {"email": "nobody@example.com"}
    )
    assert any("Ask them to sign up first" in message for message in _messages(response))
    assert not StaffAssignment.objects.exists()


def test_approving_a_refund_refunds_the_order(organizer_client, ga_event, db_session, customer):
    order = make_paid_order(db_session, ga_event, customer, quantity=1)
    refund = make_refund(db_session, order)

    response = organizer_client.post(reverse("organizer:refund-approve", args=[refund.id]))
    assert response.status_code == 302
    assert response["Location"].endswith("#refunds")

    db_session.expire_all()
    assert db_session.get(Refund, refund.id).status == "succeeded"
    assert db_session.get(Order, order.id).status == "refunded"

    twice = organizer_client.post(reverse("organizer:refund-approve", args=[refund.id]))
    assert any("already succeeded" in message for message in _messages(twice))


def test_declining_a_refund_keeps_the_order_paid(organizer_client, ga_event, db_session, customer):
    order = make_paid_order(db_session, ga_event, customer, quantity=1)
    refund = make_refund(db_session, order)

    organizer_client.post(reverse("organizer:refund-reject", args=[refund.id]))

    db_session.expire_all()
    assert db_session.get(Refund, refund.id).status == "rejected"
    assert db_session.get(Order, order.id).status == "paid"


def test_refunds_on_other_organizers_events_are_hidden(client, ga_event, db_session, customer):
    order = make_paid_order(db_session, ga_event, customer, quantity=1)
    refund = make_refund(db_session, order)
    client.force_login(OrganizerFactory().user)

    response = client.post(reverse("organizer:refund-approve", args=[refund.id]))
    assert response.status_code == 404
    db_session.expire_all()
    assert db_session.get(Refund, refund.id).status == "requested"


def test_actions_only_accept_post(organizer_client, ga_event):
    response = organizer_client.get(reverse("organizer:publish", args=[ga_event.event.pk]))
    assert response.status_code == 405


# --------------------------------------------------------------------- scan


def test_scan_page_is_closed_to_customers(client, customer):
    client.force_login(customer)
    assert client.get(reverse("organizer:scan")).status_code == 403


def test_door_staff_see_only_their_assigned_events(client):
    staff = UserFactory(role="door_staff")
    mine = EventFactory(title="Assigned Show")
    EventFactory(title="Other Show")
    StaffAssignmentFactory(user=staff, event=mine)
    client.force_login(staff)

    response = client.get(reverse("organizer:scan"))
    assert response.status_code == 200
    assert [event.title for event in response.context["events"]] == ["Assigned Show"]
    assert response.context["scan_config"]["token"]


def test_organizers_can_open_the_scanner(organizer_client, ga_event):
    response = organizer_client.get(reverse("organizer:scan"))
    assert response.status_code == 200
    assert ga_event.event in list(response.context["events"])


# ------------------------------------------------------------ status label


def test_status_label_follows_the_sale_window(db):
    event = EventFactory()
    TicketTypeFactory(event=event, sale_starts_at=timezone.now() + timedelta(days=1))
    assert event.status_display() == ("published", "Sales open later")

    open_event = EventFactory()
    TicketTypeFactory(event=open_event)
    assert open_event.status_display() == ("on_sale", "On sale")

    closed = EventFactory()
    TicketTypeFactory(event=closed, sale_ends_at=timezone.now() - timedelta(hours=1))
    assert closed.status_display() == ("published", "Sales closed")

    draft = EventFactory(status=EventStatus.DRAFT)
    assert draft.status_display() == ("draft", "Draft")


# ------------------------------------------------------- customer refunds


def test_scanned_orders_cannot_be_refunded(client, ga_event, db_session, customer):
    from api.app.services import payments
    from api.app.services.errors import RefundNotAllowed

    order = make_paid_order(db_session, ga_event, customer, quantity=1)
    ticket = db_session.query(Ticket).filter(Ticket.order_id == order.id).one()
    ticket.status = "checked_in"
    ticket.checked_in_at = utcnow()
    db_session.commit()

    with pytest.raises(RefundNotAllowed, match="scanned"):
        payments.request_refund(db_session, user_id=customer.pk, order_id=order.id)

    client.force_login(customer)
    response = client.get(reverse("accounts:account"))
    (card,) = response.context["upcoming"]
    assert card["can_refund"] is False


def test_a_queued_decision_shows_as_processing_until_it_lands(
    organizer_client, ga_event, db_session, customer, monkeypatch
):
    from worker import tasks

    from web.organizers import pages

    order = make_paid_order(db_session, ga_event, customer, quantity=1)
    refund = make_refund(db_session, order)
    monkeypatch.setattr(tasks.process_refund, "delay", lambda *args: None)  # worker is slow

    organizer_client.post(reverse("organizer:refund-approve", args=[refund.id]))
    page = organizer_client.get(reverse("organizer:event", args=[ga_event.event.pk]))
    (row,) = page.context["refunds"]
    assert row["status"] == "processing"
    assert page.context["pending_refunds"] == []

    # If the task never lands, the buttons come back after a minute.
    later = pages.time.time() + pages.IN_FLIGHT_SECONDS + 1
    monkeypatch.setattr(pages.time, "time", lambda: later)
    page = organizer_client.get(reverse("organizer:event", args=[ga_event.event.pk]))
    (row,) = page.context["refunds"]
    assert row["status"] == "requested"
