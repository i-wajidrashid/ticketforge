"""Meilisearch integration shared by Django (indexing) and FastAPI (querying)."""

from __future__ import annotations

import logging
from typing import Any

import meilisearch

from common.env import env

logger = logging.getLogger(__name__)

INDEX_SETTINGS: dict[str, Any] = {
    "searchableAttributes": ["title", "description", "venue_name", "city", "organizer_name"],
    "filterableAttributes": [
        "category",
        "city",
        "starts_at",
        "min_price_cents",
        "has_availability",
        "status",
    ],
    "sortableAttributes": ["starts_at", "min_price_cents"],
    "rankingRules": ["words", "typo", "proximity", "attribute", "sort", "exactness"],
    "pagination": {"maxTotalHits": 5000},
}


def client() -> meilisearch.Client:
    return meilisearch.Client(
        env("MEILI_URL", "http://127.0.0.1:7700"),
        env("MEILI_MASTER_KEY", "dev-meili-master-key"),
    )


def index_name() -> str:
    return env("MEILI_INDEX", "events")


def configure_index() -> None:
    """Idempotent: safe to call on every worker start or deploy."""
    events = client().index(index_name())
    events.update_settings(INDEX_SETTINGS)


def event_document(event: Any) -> dict[str, Any]:
    """Build the denormalised document Meilisearch stores for one event."""
    return {
        "id": event.pk,
        "title": event.title,
        "slug": event.slug,
        "description": event.description,
        "category": event.category,
        "city": event.venue.city,
        "venue_name": event.venue.name,
        "organizer_name": event.organizer.name,
        "starts_at": int(event.starts_at.timestamp()),
        "ends_at": int(event.ends_at.timestamp()),
        "min_price_cents": event.min_price_cents,
        "has_availability": event.status in ("published", "on_sale"),
        "status": event.status,
    }


def upsert_event(document: dict[str, Any]) -> None:
    configure_index()
    client().index(index_name()).add_documents([document], primary_key="id")


def delete_event(event_id: int) -> None:
    client().index(index_name()).delete_document(event_id)


def build_filters(
    *,
    city: str | None = None,
    category: str | None = None,
    starts_from: str | None = None,
    starts_to: str | None = None,
    max_price_cents: int | None = None,
    available_only: bool = False,
    statuses: list[str] | None = None,
) -> str:
    clauses: list[str] = []
    if city:
        clauses.append(f'city = "{city}"')
    if category:
        clauses.append(f'category = "{category}"')
    if starts_from:
        clauses.append(f"starts_at >= {int(starts_from)}")
    if starts_to:
        clauses.append(f"starts_at <= {int(starts_to)}")
    if max_price_cents is not None:
        clauses.append(f"min_price_cents <= {max_price_cents}")
    if available_only:
        clauses.append("has_availability = true")
    if statuses:
        joined = ", ".join(f'"{status}"' for status in statuses)
        clauses.append(f"status IN [{joined}]")
    return " AND ".join(clauses)


def search_events(
    query: str = "",
    *,
    filters: str = "",
    sort: list[str] | None = None,
    limit: int = 20,
    offset: int = 0,
) -> dict[str, Any]:
    options: dict[str, Any] = {"limit": limit, "offset": offset}
    if filters:
        options["filter"] = filters
    if sort:
        options["sort"] = sort
    return client().index(index_name()).search(query, options)


def index_stats() -> Any:
    return client().index(index_name()).get_stats()
