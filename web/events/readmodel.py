"""Read-only availability for the public pages.

FastAPI owns the checkout schema; Django only ever SELECTs from it (see
``web/accounts/readmodel.py`` for the shared engine). Showing "Only 4 left" on
the event page must not cost a service-to-service HTTP round trip.
"""

from __future__ import annotations

import logging

from sqlalchemy import bindparam, text

from web.accounts.readmodel import engine
from web.events.models import TicketType

logger = logging.getLogger(__name__)


def ga_remaining(ticket_types: list[TicketType]) -> dict[int, int]:
    """Units a customer can hold right now, per general-admission ticket type.

    A type that has never been held has no counter row yet: its whole
    ``quantity_total`` is available. Read failures degrade to "unknown"
    (empty dict) so the page still renders and checkout stays authoritative.
    """
    ga = [t for t in ticket_types if t.inventory_mode == "ga"]
    if not ga:
        return {}
    sql = text(
        "SELECT ticket_type_id, available FROM ga_inventory WHERE ticket_type_id IN :ids"
    ).bindparams(bindparam("ids", expanding=True))
    try:
        with engine().connect() as conn:
            rows: dict[int, int] = {
                int(row.ticket_type_id): int(row.available)
                for row in conn.execute(sql, {"ids": [t.pk for t in ga]})
            }
    except Exception:
        logger.warning("availability read failed; hiding stock levels", exc_info=True)
        return {}
    return {t.pk: int(rows.get(t.pk, t.quantity_total)) for t in ga}
