"""Server-rendered public site: the programme and individual event pages."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import datetime
from itertools import groupby
from typing import Any

from common.search import build_filters, search_events
from common.security import encode_token
from django.conf import settings
from django.db.models import Q
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone

from web.events.models import Category, Event, EventStatus
from web.events.readmodel import ga_remaining

logger = logging.getLogger(__name__)

LOW_STOCK = 10  # show "Only N left" at or below this many tickets
ACCESS_TOKEN_TTL = 3600  # seconds; long enough to finish a checkout comfortably


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


def _by_month(events: Iterable[Event]) -> list[tuple[datetime, list[Event]]]:
    """Group a date-sorted programme into (first day of month, events) pairs."""

    def month_start(event: Event) -> datetime:
        local = timezone.localtime(event.starts_at)
        return local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    return [(month, list(items)) for month, items in groupby(events, key=month_start)]


def index(request: HttpRequest) -> HttpResponse:
    query = request.GET.get("q", "").strip()
    city = request.GET.get("city", "").strip()
    category = request.GET.get("category", "").strip()
    if category not in Category.values:
        category = ""

    queryset = (
        Event.objects.published()
        .upcoming()
        .select_related("venue", "organizer")
        .prefetch_related("ticket_types")
    )
    events: list[Event] = list(queryset)
    if query:
        ids = _search_ids(query, city, category)
        if ids is not None:
            by_id = {event.pk: event for event in events}
            events = [by_id[event_id] for event_id in ids if event_id in by_id]
            events.sort(key=lambda event: event.starts_at)
        else:
            events = list(
                queryset.filter(
                    Q(title__icontains=query)
                    | Q(description__icontains=query)
                    | Q(venue__name__icontains=query)
                )
            )
    if city:
        events = [event for event in events if event.venue.city.lower() == city.lower()]
    if category:
        events = [event for event in events if event.category == category]

    filters_active = bool(query or city or category)
    cities = sorted(
        set(Event.objects.published().upcoming().values_list("venue__city", flat=True)) - {""}
    )
    featured = None
    if not filters_active:
        featured = next((event for event in events if event.is_sellable), None)

    return render(
        request,
        "events/index.html",
        {
            "events": events,
            "months": _by_month(events),
            "featured": featured,
            "filters_active": filters_active,
            "query": query,
            "city": city,
            "category": category,
            "categories": Category.choices,
            "cities": cities,
            "headline_city": city or (cities[0] if len(cities) == 1 else ""),
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
    user = request.user
    token = ""
    if user.is_authenticated:
        token = encode_token(user.pk, user.role, "access", ttl_seconds=ACCESS_TOKEN_TTL)
    verified = bool(user.is_authenticated and user.is_verified)

    ticket_types = list(event.ticket_types.all())
    seated_types = [t for t in ticket_types if t.inventory_mode == "seated"]
    ga_types = [t for t in ticket_types if t.inventory_mode == "ga"]
    remaining = ga_remaining(ga_types) if event.is_sellable else {}
    ga_rows: list[dict[str, Any]] = []
    for ticket_type in ga_types:
        left = remaining.get(ticket_type.pk)  # None = unknown (read failed / not on sale)
        ga_rows.append(
            {
                "type": ticket_type,
                "remaining": left,
                "sold_out": left == 0,
                "low": left is not None and 0 < left <= LOW_STOCK,
                "max": ticket_type.max_per_order
                if left is None
                else min(ticket_type.max_per_order, left),
            }
        )

    if event.status == EventStatus.SOLD_OUT:
        sale_state = "sold_out"
    elif not event.is_sellable:
        sale_state = "closed"
    elif event.starts_at <= timezone.now():
        sale_state = "started"
    else:
        sale_state = "open"

    context: dict[str, Any] = {
        "event": event,
        "ticket_types": ticket_types,
        "seated_types": seated_types,
        "ga_rows": ga_rows,
        "seated": bool(seated_types),
        "is_verified": verified,
        "sale_state": sale_state,
        "tf_config": {
            "eventId": event.pk,
            "slug": event.slug,
            "title": event.title,
            "token": token,
            "verified": verified,
            "authenticated": user.is_authenticated,
            "seated": bool(seated_types),
            "onSale": sale_state == "open",
            "loginUrl": f"/accounts/login/?next={request.path}",
            "stripeKey": settings.STRIPE_BROWSER_KEY,
            "ticketTypes": [
                {
                    "id": t.id,
                    "name": t.name,
                    "price_cents": t.price_cents,
                    "currency": t.currency,
                    "inventory_mode": t.inventory_mode,
                    "max_per_order": t.max_per_order,
                }
                for t in ticket_types
            ],
        },
    }
    return render(request, "events/detail.html", context)
