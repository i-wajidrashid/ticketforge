"""Public catalogue endpoints (no authentication)."""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from typing import Annotated, Any

from common.search import build_filters, search_events
from fastapi import APIRouter, Query

from api.app import core_repo
from api.app.config import settings
from api.app.schemas import EventListOut, EventOut, SeatMapOut, SeatOut, SectionOut, TicketTypeOut
from api.app.services import inventory
from api.app.services.errors import DomainError

router = APIRouter(tags=["public"])


class SearchUnavailable(DomainError):
    status_code = 503
    code = "SEARCH_UNAVAILABLE"


def _cover_url(path: str | None) -> str | None:
    if not path:
        return None
    return f"{settings.site_origin}/media/{path}"


def _ticket_types(event_id: int) -> list[TicketTypeOut]:
    return [TicketTypeOut.model_validate(t) for t in core_repo.ticket_types_for_event(event_id)]


def _event_out(event: core_repo.EventRow) -> EventOut:
    ticket_types = _ticket_types(event.id)
    return EventOut(
        id=event.id,
        title=event.title,
        slug=event.slug,
        description=event.description,
        category=event.category,
        starts_at=event.starts_at,
        ends_at=event.ends_at,
        status=event.status,
        venue_name=event.venue_name,
        city=event.city,
        cover_url=_cover_url(event.cover_image),
        min_price_cents=min((t.price_cents for t in ticket_types), default=0),
        ticket_types=ticket_types,
    )


@router.get("/events", response_model=EventListOut)
def list_events(cursor: int | None = None, limit: int = 20) -> EventListOut:
    events, next_cursor = core_repo.list_events(cursor=cursor, limit=limit)
    return EventListOut(items=[_event_out(event) for event in events], next_cursor=next_cursor)


@router.get("/events/{slug}", response_model=EventOut)
def get_event(slug: str) -> EventOut:
    from api.app.services.errors import NotFound

    event = core_repo.get_event_by_slug(slug)
    if event is None:
        raise NotFound("Event not found.")
    return _event_out(event)


@router.get("/events/{slug}/seats", response_model=SeatMapOut)
def get_seat_map(slug: str) -> SeatMapOut:
    from api.app.services.errors import NotFound

    event = core_repo.get_event_by_slug(slug)
    if event is None:
        raise NotFound("Event not found.")
    sections = [
        SectionOut(name=section["name"], seats=[SeatOut(**seat) for seat in section["seats"]])
        for section in inventory.seat_map(event.id)
    ]
    return SeatMapOut(event_id=event.id, sections=sections)


@router.get("/search")
def search(
    q: str = "",
    city: str | None = None,
    category: str | None = None,
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
    max_price_cents: int | None = None,
    available_only: bool = False,
    sort: str = "relevance",
    limit: int = 20,
    offset: int = 0,
) -> dict[str, Any]:
    filters = build_filters(
        city=city,
        category=category,
        starts_from=str(int(datetime.combine(date_from, time.min, tzinfo=UTC).timestamp()))
        if date_from
        else None,
        starts_to=str(int(datetime.combine(date_to, time.max, tzinfo=UTC).timestamp()))
        if date_to
        else None,
        max_price_cents=max_price_cents,
        available_only=available_only,
        statuses=["published", "on_sale", "sold_out"],
    )
    sort_options = {
        "date": ["starts_at:asc"],
        "price": ["min_price_cents:asc"],
        "relevance": None,
    }
    try:
        result = search_events(
            q,
            filters=filters,
            sort=sort_options.get(sort),
            limit=max(1, min(limit, 100)),
            offset=max(0, offset),
        )
    except Exception as exc:
        raise SearchUnavailable("Search is temporarily unavailable.") from exc
    return result


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "api"}
