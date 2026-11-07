"""Channel-layer group names shared by the API, workers and the socket layer."""

from __future__ import annotations


def seat_group(event_id: int) -> str:
    return f"event_{event_id}_seats"


def hold_group(hold_id: str) -> str:
    return f"hold_{hold_id}"


def sales_group(event_id: int) -> str:
    return f"event_{event_id}_sales"
