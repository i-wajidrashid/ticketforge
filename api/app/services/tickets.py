"""Ticket scanning and ownership checks."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from common.security import QRTokenError, verify_qr_token
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from api.app import core_repo
from api.app.config import settings
from api.app.db import affected
from api.app.models import EventSeat, Order, Ticket, utcnow
from api.app.services.errors import DomainError, NotFound, TicketInvalid


class StaffNotAssigned(DomainError):
    status_code = 403
    code = "STAFF_NOT_ASSIGNED"


def _ticket_with_order(session: Session, ticket_id: str) -> tuple[Ticket, Order] | None:
    ticket = session.get(Ticket, ticket_id)
    if ticket is None:
        return None
    order = session.get(Order, ticket.order_id)
    if order is None:
        return None
    return ticket, order


def scan_ticket(session: Session, *, qr_token: str, staff_user_id: int) -> dict[str, Any]:
    try:
        ticket_id = verify_qr_token(qr_token)
    except QRTokenError as exc:
        raise TicketInvalid("This QR code is not a valid TicketForge ticket.") from exc

    found = _ticket_with_order(session, ticket_id)
    if found is None:
        raise TicketInvalid("This ticket does not exist.")
    ticket, order = found

    if not core_repo.can_scan_event(staff_user_id, order.event_id):
        raise StaffNotAssigned("You are not assigned to this event.")

    if ticket.status == "checked_in":
        return {
            "result": "already_used",
            "checked_in_at": ticket.checked_in_at.isoformat() if ticket.checked_in_at else None,
            "checked_in_by": ticket.checked_in_by,
        }
    if ticket.status != "valid":
        raise TicketInvalid(f"This ticket is {ticket.status} and cannot be scanned.")

    # MySQL DATETIME keeps whole seconds; normalise so read-back matches.
    moment = utcnow().replace(microsecond=0)
    result = session.execute(
        update(Ticket)
        .where(Ticket.id == ticket.id, Ticket.status == "valid")
        .values(status="checked_in", checked_in_at=moment, checked_in_by=staff_user_id)
    )
    session.commit()
    if affected(result) != 1:
        session.refresh(ticket)
        return {
            "result": "already_used",
            "checked_in_at": ticket.checked_in_at.isoformat() if ticket.checked_in_at else None,
            "checked_in_by": ticket.checked_in_by,
        }
    return {"result": "valid", "checked_in_at": moment.isoformat(), "event_id": order.event_id}


def ticket_pdf_payload(session: Session, *, user_id: int, ticket_id: str) -> dict[str, Any]:
    found = _ticket_with_order(session, ticket_id)
    if found is None:
        raise NotFound("Ticket not found.")
    ticket, order = found
    if order.user_id != user_id:
        raise NotFound("Ticket not found.")
    if ticket.status not in {"valid", "checked_in"}:
        raise TicketInvalid("This ticket is no longer downloadable.")
    user = core_repo.get_user(user_id)
    return pdf_fields(session, ticket, order, owner_email=user.email if user else "")


def local_time(moment: datetime) -> datetime:
    """Naive UTC (as stored) -> aware venue-local time for display."""
    aware = moment if moment.tzinfo else moment.replace(tzinfo=UTC)
    return aware.astimezone(ZoneInfo(settings.time_zone))


def pdf_fields(
    session: Session, ticket: Ticket, order: Order, *, owner_email: str
) -> dict[str, str]:
    """Everything printed on a ticket PDF. One builder for every caller
    (account download, API download, confirmation email) so they never drift."""
    event = core_repo.get_event(order.event_id)
    ticket_type = core_repo.get_ticket_type(ticket.ticket_type_id)
    admission = ticket_type.name if ticket_type else "General admission"
    if ticket.event_seat_id is not None:
        seat_id = session.scalar(
            select(EventSeat.seat_id).where(EventSeat.id == ticket.event_seat_id)
        )
        seat = next((s for s in core_repo.seats_for_event(order.event_id) if s.id == seat_id), None)
        if seat is not None:
            admission = f"{seat.section}, row {seat.row}, seat {seat.number}"
    return {
        "ticket_id": ticket.id,
        "qr_token": ticket.qr_token,
        "order_id": order.id,
        "owner_email": owner_email,
        "event_title": event.title if event else "Event",
        "when": local_time(event.starts_at).strftime("%a %d %b %Y, %H:%M") if event else "",
        "venue": f"{event.venue_name}, {event.city}" if event else "",
        "admission": admission,
    }


def order_tickets(session: Session, order_id: str) -> list[Ticket]:
    return list(session.scalars(select(Ticket).where(Ticket.order_id == order_id)).all())
