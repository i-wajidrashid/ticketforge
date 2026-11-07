# Architecture decision records (short form)

## ADR-001 — Two databases, one writer each

**Decision:** Django owns `core`, FastAPI owns `checkout`; each reads the other read-only.

**Why:** independent deploys and migrations, a hard service boundary, and no shared ORM models.

**Trade-off:** cross-service reads are hand-written SQL and must be versioned with the schema.

## ADR-002 — Conditional updates, not `SELECT … FOR UPDATE` on seats

**Decision:** every seat transition is `UPDATE … WHERE status = 'available'` and the affected-row
count decides the winner.

**Why:** it is a single round trip, correct under any isolation level, and trivially testable.

## ADR-003 — MySQL named locks for "one hold per user per event"

**Decision:** wrap hold creation in `GET_LOCK('hold:{user}:{event}')`.

**Why:** a unique index cannot express "at most one *active* row"; a named lock closes the race
without a Redis dependency on the correctness path.

**Trade-off:** the lock is per user+event, so it does not serialise unrelated buyers.

## ADR-004 — Webhook ledger committed with the business change

**Decision:** insert `stripe_event` and mutate the order in the same transaction.

**Why:** if processing fails the ledger row rolls back and Stripe's retry works; if it succeeds a
retry is a no-op.

## ADR-005 — Fake Stripe gateway for local development

**Decision:** `STRIPE_GATEWAY=auto` picks the real client only for real-looking test keys,
otherwise a deterministic fake; a dev-only simulate endpoint completes the flow.

**Why:** the whole demo works offline, and tests never touch the network.

**Trade-off:** the simulate endpoint must be impossible in production — it checks the gateway
type at request time.

## ADR-006 — Server-rendered site + a thin JS client

**Decision:** Django templates render pages; the seat map and checkout use the documented JSON API.

**Why:** fast first paint, accessible pages, and the API is exercised the same way a future client
would use it.

## ADR-007 — Money as integer cents, timestamps as naive UTC

**Decision:** `BIGINT` cents everywhere; one `utcnow()` helper; no timezone-aware values in MySQL.

**Why:** no float rounding, no timezone ambiguity between MySQL and Python.
