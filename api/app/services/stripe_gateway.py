"""Thin Stripe boundary so the domain logic is testable without the network.

``STRIPE_GATEWAY=auto`` uses the real client when a real-looking key is
configured, and a deterministic fake otherwise. That keeps the local demo
working out of the box while production tests still exercise the live path.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Protocol

from api.app.config import settings


@dataclass(frozen=True, slots=True)
class PaymentIntentResult:
    id: str
    client_secret: str
    status: str


@dataclass(frozen=True, slots=True)
class RefundResult:
    id: str
    status: str


class PaymentGateway(Protocol):
    def create_payment_intent(
        self,
        *,
        amount_cents: int,
        currency: str,
        metadata: dict[str, str],
        idempotency_key: str,
    ) -> PaymentIntentResult: ...

    def create_refund(
        self,
        *,
        payment_intent_id: str,
        amount_cents: int,
        idempotency_key: str,
    ) -> RefundResult: ...

    def list_recent_payment_intents(self, limit: int = 100) -> list[str]: ...


class FakeGateway:
    """Deterministic ids derived from the idempotency key, which keeps tests repeatable."""

    def _digest(self, value: str) -> str:
        return hashlib.sha256(value.encode()).hexdigest()[:24]

    def create_payment_intent(
        self,
        *,
        amount_cents: int,
        currency: str,
        metadata: dict[str, str],
        idempotency_key: str,
    ) -> PaymentIntentResult:
        digest = self._digest(idempotency_key)
        return PaymentIntentResult(
            id=f"pi_fake_{digest}",
            client_secret=f"pi_fake_{digest}_secret_demo",
            status="requires_confirmation",
        )

    def create_refund(
        self,
        *,
        payment_intent_id: str,
        amount_cents: int,
        idempotency_key: str,
    ) -> RefundResult:
        return RefundResult(id=f"re_fake_{self._digest(idempotency_key)}", status="succeeded")

    def list_recent_payment_intents(self, limit: int = 100) -> list[str]:
        return []


class LiveStripeGateway:
    def __init__(self) -> None:
        import stripe  # imported lazily so tests never need network config

        self._stripe = stripe
        self._stripe.api_key = settings.stripe_secret_key

    def create_payment_intent(
        self,
        *,
        amount_cents: int,
        currency: str,
        metadata: dict[str, str],
        idempotency_key: str,
    ) -> PaymentIntentResult:
        intent = self._stripe.PaymentIntent.create(
            amount=amount_cents,
            currency=currency.lower(),
            metadata=metadata,
            automatic_payment_methods={"enabled": True},
            idempotency_key=idempotency_key,
        )
        return PaymentIntentResult(
            id=str(intent.id),
            client_secret=str(intent.client_secret or ""),
            status=str(intent.status),
        )

    def create_refund(
        self,
        *,
        payment_intent_id: str,
        amount_cents: int,
        idempotency_key: str,
    ) -> RefundResult:
        refund = self._stripe.Refund.create(
            payment_intent=payment_intent_id,
            amount=amount_cents,
            idempotency_key=idempotency_key,
        )
        return RefundResult(id=str(refund.id), status=str(refund.status))

    def list_recent_payment_intents(self, limit: int = 100) -> list[str]:
        intents: Any = self._stripe.PaymentIntent.list(limit=limit)
        return [item.id for item in intents.auto_paging_iter()]


def get_gateway() -> PaymentGateway:
    mode = settings.stripe_gateway.lower()
    if mode == "fake":
        return FakeGateway()
    if mode == "live":
        return LiveStripeGateway()
    if (
        settings.stripe_secret_key.startswith("sk_test_")
        and settings.stripe_secret_key != "sk_test_xxx"
    ):
        return LiveStripeGateway()
    return FakeGateway()
