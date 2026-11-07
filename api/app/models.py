"""Checkout schema (owned by Alembic).

Conventions used everywhere:
* money is an integer number of cents, never a float;
* timestamps are naive UTC (MySQL DATETIME has no timezone);
* ids exposed publicly are UUID strings, internal surrogate keys are BIGINT.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.app.db import Base

SEAT_STATUSES = ("available", "held", "sold", "blocked")
HOLD_STATUSES = ("active", "converted", "expired", "cancelled")
ORDER_STATUSES = ("pending", "paid", "refunded", "partially_refunded", "failed")
TICKET_STATUSES = ("valid", "checked_in", "refunded", "void")
REFUND_STATUSES = ("requested", "processing", "succeeded", "rejected", "failed")


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def new_uuid() -> str:
    return str(uuid.uuid4())


def in_clause(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(value) for value in values)})"


class EventSeat(Base):
    __tablename__ = "event_seat"
    __table_args__ = (
        UniqueConstraint("event_id", "seat_id", name="uq_event_seat"),
        CheckConstraint(in_clause("status", SEAT_STATUSES), name="ck_event_seat_status"),
        Index("ix_event_seat_event_status", "event_id", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    seat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    ticket_type_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="available")
    hold_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class GAInventory(Base):
    __tablename__ = "ga_inventory"
    __table_args__ = (CheckConstraint("available >= 0", name="ck_ga_inventory_non_negative"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ticket_type_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True)
    available: Mapped[int] = mapped_column(Integer, nullable=False)


class Hold(Base):
    __tablename__ = "hold"
    __table_args__ = (
        CheckConstraint(in_clause("status", HOLD_STATUSES), name="ck_hold_status"),
        Index("ix_hold_expiry", "status", "expires_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    event_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)

    items: Mapped[list[HoldItem]] = relationship(
        back_populates="hold", cascade="all, delete-orphan"
    )


class HoldItem(Base):
    __tablename__ = "hold_item"
    __table_args__ = (CheckConstraint("quantity >= 0", name="ck_hold_item_quantity"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    hold_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("hold.id", ondelete="CASCADE"), nullable=False
    )
    event_seat_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("event_seat.id"), nullable=True
    )
    ticket_type_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    hold: Mapped[Hold] = relationship(back_populates="items")


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        CheckConstraint(in_clause("status", ORDER_STATUSES), name="ck_order_status"),
        Index("ix_order_user_created", "user_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    event_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    hold_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("hold.id"), nullable=False, unique=True
    )
    total_cents: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    stripe_payment_intent_id: Mapped[str | None] = mapped_column(
        String(120), nullable=True, unique=True
    )
    client_secret: Mapped[str | None] = mapped_column(String(255), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)

    tickets: Mapped[list[Ticket]] = relationship(back_populates="order")


class Ticket(Base):
    __tablename__ = "ticket"
    __table_args__ = (
        CheckConstraint(in_clause("status", TICKET_STATUSES), name="ck_ticket_status"),
        Index("ix_ticket_order", "order_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    order_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("orders.id", ondelete="CASCADE"), nullable=False
    )
    ticket_type_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    event_seat_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("event_seat.id"), nullable=True
    )
    qr_token: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="valid")
    checked_in_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    checked_in_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    order: Mapped[Order] = relationship(back_populates="tickets")


class Refund(Base):
    __tablename__ = "refund"
    __table_args__ = (
        CheckConstraint(in_clause("status", REFUND_STATUSES), name="ck_refund_status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("orders.id"), nullable=False, index=True
    )
    amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="requested")
    stripe_refund_id: Mapped[str | None] = mapped_column(String(120), nullable=True, unique=True)
    reason: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class StripeEvent(Base):
    """Idempotency ledger for webhooks: the unique index *is* the dedupe."""

    __tablename__ = "stripe_event"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    stripe_event_id: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    type: Mapped[str] = mapped_column(String(80), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    actor_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    entity: Mapped[str] = mapped_column(String(80), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(80), nullable=False)
    data: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)


__all__ = [
    "AuditLog",
    "EventSeat",
    "GAInventory",
    "Hold",
    "HoldItem",
    "Order",
    "Refund",
    "StripeEvent",
    "Ticket",
    "new_uuid",
    "utcnow",
]
