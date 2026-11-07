"""Domain errors carry an HTTP status and the API's error code."""

from __future__ import annotations

from typing import Any


class DomainError(Exception):
    status_code = 400
    code = "BAD_REQUEST"

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class NotFound(DomainError):
    status_code = 404
    code = "NOT_FOUND"


class EventNotSellable(DomainError):
    status_code = 409
    code = "EVENT_NOT_SELLABLE"


class SaleWindowClosed(DomainError):
    status_code = 409
    code = "SALE_WINDOW_CLOSED"


class HoldExists(DomainError):
    status_code = 409
    code = "HOLD_EXISTS"


class HoldNotFound(NotFound):
    code = "HOLD_NOT_FOUND"


class HoldNotActive(DomainError):
    status_code = 409
    code = "HOLD_NOT_ACTIVE"


class HoldExpired(DomainError):
    status_code = 409
    code = "HOLD_EXPIRED"


class SeatUnavailable(DomainError):
    status_code = 409
    code = "SEAT_UNAVAILABLE"


class SoldOut(DomainError):
    status_code = 409
    code = "SOLD_OUT"


class InvalidQuantity(DomainError):
    status_code = 422
    code = "INVALID_QUANTITY"


class RateLimited(DomainError):
    status_code = 429
    code = "RATE_LIMITED"


class PaymentFailed(DomainError):
    status_code = 402
    code = "PAYMENT_FAILED"


class IdempotencyConflict(DomainError):
    status_code = 409
    code = "IDEMPOTENCY_CONFLICT"


class RefundNotAllowed(DomainError):
    status_code = 409
    code = "REFUND_NOT_ALLOWED"


class TicketInvalid(DomainError):
    status_code = 404
    code = "TICKET_INVALID"


class Unauthorized(DomainError):
    status_code = 401
    code = "UNAUTHORIZED"


class Forbidden(DomainError):
    status_code = 403
    code = "FORBIDDEN"


class EmailNotVerified(DomainError):
    status_code = 403
    code = "EMAIL_NOT_VERIFIED"


class TicketAlreadyUsed(DomainError):
    status_code = 409
    code = "TICKET_ALREADY_USED"


class InvalidSignature(DomainError):
    status_code = 400
    code = "INVALID_SIGNATURE"


class ValidationFailed(DomainError):
    status_code = 422
    code = "VALIDATION_ERROR"
