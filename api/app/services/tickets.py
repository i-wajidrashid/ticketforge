"""Ticket scanning and ownership checks."""

from __future__ import annotations

from typing import Any

from common.security import QRTokenError, verify_qr_token
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from api.app import core_repo
from api.app.db import affected
from api.app.models import Order, Ticket, utcnow
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

    if not core_repo.is_staff_for_event(staff_user_id, order.event_id):
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
    event = core_repo.get_event(order.event_id)
    event_title = event.title if event else "Event"
    user = core_repo.get_user(user_id)
    return {
        "ticket_id": ticket.id,
        "qr_token": ticket.qr_token,
        "order_id": order.id,
        "event_title": event_title,
        "owner_email": user.email if user else "",
    }


def order_tickets(session: Session, order_id: str) -> list[Ticket]:
    return list(session.scalars(select(Ticket).where(Ticket.order_id == order_id)).all())
