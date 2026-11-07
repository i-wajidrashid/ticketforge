"""Meilisearch integration (skipped automatically when the service is absent)."""

from __future__ import annotations

import time

import pytest
from common.search import (
    configure_index,
    delete_event,
    event_document,
    search_events,
    upsert_event,
)

pytestmark = pytest.mark.django_db(transaction=True)


def test_event_document_is_searchable(seated_event):
    try:
        configure_index()
        upsert_event(event_document(seated_event.event))
    except Exception:
        pytest.skip("Meilisearch is not reachable")

    hits = []
    for _ in range(40):
        result = search_events(seated_event.event.title)
        hits = result.get("hits", [])
        if hits:
            break
        time.sleep(0.25)

    assert hits, "event was not indexed in time"
    matches = [item for item in hits if item["slug"] == seated_event.event.slug]
    assert matches, f"event missing from hits: {[h['slug'] for h in hits]}"
    hit = matches[0]
    assert hit["city"] == seated_event.venue.city
    assert hit["min_price_cents"] == 5000

    filtered = search_events("", filters='city = "Karachi"')
    assert any(item["id"] == seated_event.event.pk for item in filtered.get("hits", []))

    delete_event(seated_event.event.pk)
