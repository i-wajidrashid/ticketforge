"""Pydantic request/response models (the public contract)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TicketTypeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    price_cents: int
    currency: str
    quantity_total: int
    max_per_order: int
    inventory_mode: str
    sale_starts_at: datetime | None = None
    sale_ends_at: datetime | None = None


class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    slug: str
    description: str
    category: str
    starts_at: datetime
    ends_at: datetime
    status: str
    venue_name: str
    city: str
    cover_url: str | None = None
    min_price_cents: int = 0
    ticket_types: list[TicketTypeOut] = Field(default_factory=list)


class EventListOut(BaseModel):
    items: list[EventOut]
    next_cursor: int | None = None


class SeatOut(BaseModel):
    seat_id: int
    row: str
    number: str
    status: str


class SectionOut(BaseModel):
    name: str
    seats: list[SeatOut]


class SeatMapOut(BaseModel):
    event_id: int
    sections: list[SectionOut]


class HoldCreate(BaseModel):
    event_id: int
    seat_ids: list[int] | None = None
    ticket_type_id: int | None = None
    quantity: int | None = None

    @model_validator(mode="after")
    def exactly_one_mode(self) -> HoldCreate:
        has_seats = self.seat_ids is not None
        has_quantity = self.quantity is not None
        if has_seats == has_quantity:
            raise ValueError("Provide either seat_ids or quantity.")
        if has_seats and not self.seat_ids:
            raise ValueError("seat_ids cannot be empty.")
        return self


class HoldItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    event_seat_id: int | None
    seat_label: str | None
    ticket_type_id: int
    quantity: int


class HoldOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    event_id: int
    status: str
    expires_at: datetime
    seconds_left: int
    total_cents: int
    currency: str
    items: list[HoldItemOut]


class PaymentOut(BaseModel):
    order_id: str
    payment_intent_id: str
    client_secret: str
    amount_cents: int
    currency: str
    status: str


class TicketOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    status: str
    ticket_type_id: int
    event_seat_id: int | None
    checked_in_at: datetime | None = None


class OrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    event_id: int
    status: str
    total_cents: int
    currency: str
    created_at: datetime
    tickets: list[TicketOut] = Field(default_factory=list)


class RefundRequest(BaseModel):
    reason: str = Field(default="", max_length=255)


class RefundOut(BaseModel):
    id: int
    order_id: str
    amount_cents: int
    status: str
    reason: str


class ScanRequest(BaseModel):
    qr_token: str = Field(min_length=8, max_length=200)


class ScanResult(BaseModel):
    result: str
    checked_in_at: str | None = None
    event_id: int | None = None


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorOut(BaseModel):
    error: ErrorDetail


class ReadyOut(BaseModel):
    status: str
    checks: dict[str, str]
