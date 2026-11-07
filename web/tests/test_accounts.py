from __future__ import annotations

import re
from urllib.parse import unquote

import pytest
from django.core import mail
from django.urls import reverse

from web.accounts import emails
from web.accounts.models import User

pytestmark = pytest.mark.django_db


def test_signup_logs_in_and_sends_a_verification_email(client):
    response = client.post(
        reverse("accounts:signup"),
        {
            "email": "new@example.com",
            "password1": "super-secret-123",
            "password2": "super-secret-123",
        },
    )
    assert response.status_code == 302

    user = User.objects.get(email="new@example.com")
    assert user.is_verified is False
    assert user.role == "customer"
    assert len(mail.outbox) == 1
    assert "Verify your TicketForge email" in mail.outbox[0].subject
    # The token embeds a timestamp, so compare what it *decodes* to rather than
    # the exact string (which changes if the clock ticks mid-test).
    match = re.search(r"/accounts/verify/([^/\s]+)/", mail.outbox[0].body)
    assert match is not None
    assert emails.read_verification_token(unquote(match.group(1))) == user.pk


def test_verification_link_marks_the_account_verified(client):
    user = User.objects.create_user(email="verify@example.com", password="super-secret-123")
    token = emails.make_verification_token(user.pk)

    response = client.get(reverse("accounts:verify", kwargs={"token": token}))

    assert response.status_code == 302
    user.refresh_from_db()
    assert user.is_verified is True


def test_bad_verification_token_is_rejected(client):
    response = client.get(reverse("accounts:verify", kwargs={"token": "not-a-token"}))
    assert response.status_code == 302
    assert response.url == reverse("accounts:login")


def test_login_and_logout(client):
    User.objects.create_user(email="login@example.com", password="super-secret-123")
    response = client.post(
        reverse("accounts:login"), {"email": "login@example.com", "password": "super-secret-123"}
    )
    assert response.status_code == 302
    assert response.url == reverse("accounts:account")

    response = client.post(reverse("accounts:logout"))
    assert response.status_code == 302


def test_login_is_rate_limited_after_five_failures(client):
    User.objects.create_user(email="locked@example.com", password="super-secret-123")
    for _ in range(5):
        response = client.post(
            reverse("accounts:login"), {"email": "locked@example.com", "password": "wrong-password"}
        )
        assert response.status_code == 200

    blocked = client.post(
        reverse("accounts:login"), {"email": "locked@example.com", "password": "super-secret-123"}
    )
    assert blocked.status_code == 429


def test_account_page_requires_login(client):
    response = client.get(reverse("accounts:account"))
    assert response.status_code == 302
    assert "/accounts/login/" in response.url


def test_json_token_endpoint_returns_a_usable_jwt(client):
    from common.security import decode_token

    user = User.objects.create_user(
        email="jwt@example.com", password="super-secret-123", is_verified=True
    )
    response = client.post(
        "/accounts/api/token/",
        data={"email": "jwt@example.com", "password": "super-secret-123"},
        content_type="application/json",
    )
    assert response.status_code == 200
    payload = response.json()
    assert decode_token(payload["access"]).user_id == user.pk
    assert payload["user"]["email"] == "jwt@example.com"


def test_refresh_token_cannot_be_used_as_access_token(client):
    from common.security import encode_token

    user = User.objects.create_user(email="refresh@example.com", password="super-secret-123")
    refresh = encode_token(user.pk, user.role, "refresh")
    response = client.post(
        "/accounts/api/token/refresh/", data={"refresh": refresh}, content_type="application/json"
    )
    assert response.status_code == 200
    assert "access" in response.json()

    # An access token is not accepted where a refresh token is required.
    access = encode_token(user.pk, user.role, "access")
    rejected = client.post(
        "/accounts/api/token/refresh/", data={"refresh": access}, content_type="application/json"
    )
    assert rejected.status_code == 401


def test_an_api_password_reset_ends_a_lockout(customer):
    from django.core import signing
    from rest_framework.test import APIClient

    from web.accounts.throttling import is_login_blocked, register_failed_login
    from web.accounts.views import RESET_SALT

    for _ in range(5):
        register_failed_login("203.0.113.9", customer.email)
    assert is_login_blocked("127.0.0.1", customer.email)

    token = signing.dumps({"uid": customer.pk}, salt=RESET_SALT)
    response = APIClient().post(
        "/accounts/api/password-reset/confirm/",
        {"token": token, "password": "a-new-pass-123"},
        format="json",
    )
    assert response.status_code == 200
    assert not is_login_blocked("127.0.0.1", customer.email)


@pytest.mark.parametrize(
    "path",
    [
        "/accounts/api/token/refresh/",
        "/accounts/api/password-reset/",
        "/accounts/api/password-reset/confirm/",
    ],
)
def test_account_endpoints_answer_a_json_array_with_400(path):
    from rest_framework.test import APIClient

    response = APIClient().post(path, [], format="json")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
