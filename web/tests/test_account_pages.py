"""Customer-facing account pages: ticket PDFs, password reset, verification."""

from __future__ import annotations

import pytest
from api.app.models import Ticket
from api.app.services import payments
from django.core import mail, signing
from django.urls import reverse
from tests.factories import UserFactory
from tests.helpers import make_paid_order, make_refund

from web.accounts.views import RESET_SALT

pytestmark = pytest.mark.django_db(transaction=True)


def _first_ticket(db_session, order) -> Ticket:
    return db_session.query(Ticket).filter(Ticket.order_id == order.id).first()


# ------------------------------------------------------------- ticket PDF


def test_owner_downloads_a_ticket_pdf(client, ga_event, db_session, customer):
    order = make_paid_order(db_session, ga_event, customer, quantity=1)
    ticket = _first_ticket(db_session, order)
    client.force_login(customer)

    response = client.get(reverse("accounts:ticket-pdf", args=[ticket.id]))
    assert response.status_code == 200
    assert response["Content-Type"] == "application/pdf"
    assert response.content.startswith(b"%PDF")
    assert f"ticket-{ticket.id}.pdf" in response["Content-Disposition"]


def test_someone_elses_ticket_is_a_404(client, ga_event, db_session, customer):
    order = make_paid_order(db_session, ga_event, customer, quantity=1)
    ticket = _first_ticket(db_session, order)
    client.force_login(UserFactory())

    assert client.get(reverse("accounts:ticket-pdf", args=[ticket.id])).status_code == 404


def test_refunded_tickets_are_no_longer_downloadable(client, ga_event, db_session, customer):
    order = make_paid_order(db_session, ga_event, customer, quantity=1)
    refund = make_refund(db_session, order)
    payments.process_refund(db_session, refund_id=refund.id, actor_id=None)
    ticket = _first_ticket(db_session, order)
    client.force_login(customer)

    assert client.get(reverse("accounts:ticket-pdf", args=[ticket.id])).status_code == 403


def test_unverified_accounts_are_sent_back_to_confirm_first(client, ga_event, db_session):
    buyer = UserFactory(is_verified=True)
    order = make_paid_order(db_session, ga_event, buyer, quantity=1)
    ticket = _first_ticket(db_session, order)
    buyer.is_verified = False
    buyer.save(update_fields=["is_verified"])
    client.force_login(buyer)

    response = client.get(reverse("accounts:ticket-pdf", args=[ticket.id]))
    assert response.status_code == 302
    assert response["Location"] == reverse("accounts:account")


def test_account_page_lists_paid_orders_with_a_refund_option(
    client, ga_event, db_session, customer
):
    make_paid_order(db_session, ga_event, customer, quantity=2)
    client.force_login(customer)

    response = client.get(reverse("accounts:account"))
    assert response.status_code == 200
    (card,) = response.context["upcoming"]
    assert card["event"] == ga_event.event
    assert len(card["tickets"]) == 2
    assert card["can_refund"] is True
    assert response.context["account_config"]["token"]


# --------------------------------------------------------- password reset


def test_password_reset_request_emails_existing_accounts_only(client, customer):
    url = reverse("accounts:password-reset")
    client.post(url, {"email": customer.email})
    assert len(mail.outbox) == 1

    response = client.post(url, {"email": "nobody@example.com"})
    assert response.status_code == 302  # same answer either way, no account probing
    assert len(mail.outbox) == 1


def test_password_reset_confirm_sets_the_new_password(client, customer):
    token = signing.dumps({"uid": customer.pk}, salt=RESET_SALT)
    url = reverse("accounts:password-reset-confirm")

    assert client.get(url, {"token": token}).status_code == 200

    mismatch = client.post(
        url, {"token": token, "password1": "a-new-pass-123", "password2": "different-123"}
    )
    assert mismatch.status_code == 200
    customer.refresh_from_db()
    assert not customer.check_password("a-new-pass-123")

    weak = client.post(url, {"token": token, "password1": "123", "password2": "123"})
    assert weak.status_code == 200

    done = client.post(
        url, {"token": token, "password1": "a-new-pass-123", "password2": "a-new-pass-123"}
    )
    assert done.status_code == 302
    customer.refresh_from_db()
    assert customer.check_password("a-new-pass-123")


def test_a_tampered_reset_token_is_rejected(client):
    response = client.get(reverse("accounts:password-reset-confirm"), {"token": "nope"})
    assert response.status_code == 302
    assert response["Location"] == reverse("accounts:password-reset")


def test_resend_verification_only_mails_unverified_users(client):
    unverified = UserFactory(is_verified=False)
    client.force_login(unverified)
    client.post(reverse("accounts:resend-verification"))
    assert len(mail.outbox) == 1

    client.force_login(UserFactory(is_verified=True))
    client.post(reverse("accounts:resend-verification"))
    assert len(mail.outbox) == 1


# -------------------------------------------------------- event page stock


def test_event_page_shows_live_general_admission_stock(client, ga_event, db_session, customer):
    make_paid_order(db_session, ga_event, customer, quantity=3)
    response = client.get(reverse("events:detail", args=[ga_event.event.slug]))
    assert response.status_code == 200
    (row,) = response.context["ga_rows"]
    assert row["remaining"] == ga_event.ticket_type.quantity_total - 3
    assert row["sold_out"] is False


def test_a_password_reset_ends_a_lockout_caused_by_someone_else(client, customer):
    from web.accounts.throttling import register_failed_login

    for _ in range(5):  # someone guessing from another address
        register_failed_login("203.0.113.9", customer.email)
    login = reverse("accounts:login")
    blocked = client.post(login, {"email": customer.email, "password": "anything-at-all"})
    assert blocked.status_code == 429

    token = signing.dumps({"uid": customer.pk}, salt=RESET_SALT)
    client.post(
        reverse("accounts:password-reset-confirm"),
        {"token": token, "password1": "a-new-pass-123", "password2": "a-new-pass-123"},
    )
    signed_in = client.post(login, {"email": customer.email, "password": "a-new-pass-123"})
    assert signed_in.status_code == 302


def test_the_mailpit_hint_only_appears_in_local_development(client, settings):
    client.force_login(UserFactory(is_verified=False))
    assert "Mailpit" not in client.get(reverse("accounts:account")).content.decode()

    settings.DEBUG = True
    assert "Mailpit" in client.get(reverse("accounts:account")).content.decode()
