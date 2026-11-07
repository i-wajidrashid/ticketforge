"""Server-rendered public site."""

from __future__ import annotations

import logging
from typing import Any

from common.search import build_filters, search_events
from common.security import encode_token
from django.db.models import Q
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone

from web.events.models import Category, Event, EventStatus

logger = logging.getLogger(__name__)


def _search_ids(query: str, city: str, category: str) -> list[int] | None:
    try:
        result = search_events(
            query,
            filters=build_filters(city=city or None, category=category or None),
            limit=50,
        )
        return [int(hit["id"]) for hit in result.get("hits", [])]
    except Exception:
        logger.warning("search backend unavailable; falling back to the database", exc_info=True)
        return None


def index(request: HttpRequest) -> HttpResponse:
    query = request.GET.get("q", "").strip()
    city = request.GET.get("city", "").strip()
    category = request.GET.get("category", "").strip()

    queryset = Event.objects.published().upcoming().select_related("venue", "organizer")
    events: list[Event] = list(queryset)
    if query:
        ids = _search_ids(query, city, category)
        if ids is not None:
            by_id = {event.pk: event for event in queryset.filter(pk__in=ids)}
            events = [by_id[event_id] for event_id in ids if event_id in by_id]
        else:
            events = list(
                queryset.filter(Q(title__icontains=query) | Q(description__icontains=query))
            )
    if city:
        events = [event for event in events if event.venue.city.lower() == city.lower()]
    if category:
        events = [event for event in events if event.category == category]

    return render(
        request,
        "events/index.html",
        {
            "events": events,
            "query": query,
            "city": city,
            "category": category,
            "categories": Category.choices,
            "cities": sorted(
                {event.venue.city for event in Event.objects.published() if event.venue_id}
            ),
        },
    )


def detail(request: HttpRequest, slug: str) -> HttpResponse:
    queryset = Event.objects.select_related("venue", "organizer").prefetch_related("ticket_types")
    event = get_object_or_404(
        queryset.filter(
            status__in=[EventStatus.PUBLISHED, EventStatus.ON_SALE, EventStatus.SOLD_OUT]
        ),
        slug=slug,
    )
    token = ""
    if request.user.is_authenticated:
        token = encode_token(request.user.pk, request.user.role, "access", ttl_seconds=3600)
    ticket_types = list(event.ticket_types.all())
    seated = any(t.inventory_mode == "seated" for t in ticket_types)
    verified = bool(request.user.is_authenticated and request.user.is_verified)
    context: dict[str, Any] = {
        "event": event,
        "ticket_types": ticket_types,
        "seated": seated,
        "is_verified": verified,
        "now": timezone.now(),
        "tf_config": {
            "eventId": event.pk,
            "slug": event.slug,
            "token": token,
            "verified": verified,
            "seated": seated,
            "ticketTypes": [
                {
                    "id": tt.id,
                    "name": tt.name,
                    "price_cents": tt.price_cents,
                    "inventory_mode": tt.inventory_mode,
                    "max_per_order": tt.max_per_order,
                }
                for tt in ticket_types
            ],
        },
    }
    return render(request, "events/detail.html", context)
