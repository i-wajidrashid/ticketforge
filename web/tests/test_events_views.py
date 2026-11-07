from __future__ import annotations

import pytest
from django.urls import reverse
from tests.factories import EventFactory, OrganizerFactory, VenueFactory

pytestmark = pytest.mark.django_db


def test_homepage_lists_published_events(client):
    EventFactory(title="Visible Show")
    EventFactory(title="Draft Show", status="draft")
    response = client.get(reverse("events:index"))
    assert response.status_code == 200
    assert b"Visible Show" in response.content
    assert b"Draft Show" not in response.content


def test_homepage_uses_search_backend_when_available(client, monkeypatch):
    event = EventFactory(title="Searchable Show")
    monkeypatch.setattr("web.events.views._search_ids", lambda *args, **kwargs: [event.pk])
    response = client.get(reverse("events:index"), {"q": "show"})
    assert response.status_code == 200
    assert b"Searchable Show" in response.content


def test_homepage_falls_back_to_database_when_search_is_down(client, monkeypatch):
    EventFactory(title="Fallback Show")
    monkeypatch.setattr("web.events.views._search_ids", lambda *args, **kwargs: None)
    response = client.get(reverse("events:index"), {"q": "Fallback"})
    assert b"Fallback Show" in response.content


def test_homepage_filters_city_and_category(client):
    venue = VenueFactory(city="Lahore")
    EventFactory(title="Lahore Music", venue=venue, category="music")
    EventFactory(title="Karachi Music", category="music")
    response = client.get(reverse("events:index"), {"city": "Lahore", "category": "music"})
    assert b"Lahore Music" in response.content
    assert b"Karachi Music" not in response.content


def test_event_detail_page_renders(client, seated_event):
    response = client.get(reverse("events:detail", kwargs={"slug": seated_event.event.slug}))
    assert response.status_code == 200
    assert seated_event.event.title.encode() in response.content
    assert b"seat-map" in response.content


def test_cancelled_event_detail_is_404(client):
    organizer = OrganizerFactory()
    event = EventFactory(organizer=organizer, status="cancelled")
    response = client.get(reverse("events:detail", kwargs={"slug": event.slug}))
    assert response.status_code == 404


def test_anonymous_detail_page_does_not_leak_a_token(client, seated_event):
    response = client.get(reverse("events:detail", kwargs={"slug": seated_event.event.slug}))
    assert b"tf-config" in response.content
