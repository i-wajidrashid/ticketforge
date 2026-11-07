"""The site's pages are served with a strict Content Security Policy."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.urls import reverse
from tests.factories import EventFactory

from web.config.csp import policy

pytestmark = pytest.mark.django_db

TEMPLATES = Path(__file__).resolve().parents[1] / "templates"


def test_pages_forbid_inline_code_and_allow_only_stripe(client):
    event = EventFactory()
    for url in ("/", reverse("events:detail", args=[event.slug]), reverse("accounts:login")):
        header = client.get(url)["Content-Security-Policy"]
        assert "script-src 'self' https://js.stripe.com" in header
        assert "style-src 'self';" in header
        assert "'unsafe-inline'" not in header
        assert "frame-ancestors 'none'" in header


def test_json_responses_and_third_party_pages_are_left_alone(client, admin_client):
    assert "Content-Security-Policy" not in client.get("/health/")
    assert "Content-Security-Policy" not in client.get("/org/api/docs/")
    assert "Content-Security-Policy" not in admin_client.get("/admin/")


def test_websockets_to_this_host_are_allowed():
    assert "connect-src 'self' wss://tickets.example.com " in policy("tickets.example.com")


def test_templates_contain_nothing_the_policy_would_block():
    """Inline scripts, inline handlers and style attributes would all be refused."""
    for path in TEMPLATES.rglob("*.html"):
        text = path.read_text()
        assert not re.search(r"<script(?![^>]*\bsrc=)(?![^>]*application/json)[^>]*>", text), path
        assert not re.search(r"\son[a-z]+=", text), path
        assert 'style="' not in text, path
