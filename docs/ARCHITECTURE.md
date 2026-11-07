# Architecture

## 1. Services and ownership

| Service | Runtime | Owns | Writes | Reads |
|---|---|---|---|---|
| `web` | Django 5 + DRF, Gunicorn | `core` schema | `core` only | `checkout` projections (dashboard/account) |
| `api` | FastAPI + SQLAlchemy 2, Uvicorn | `checkout` schema | `checkout` only | `core` (event catalogue, users, staff assignments) |
| `realtime` | Daphne + Django Channels | — | Redis channel layer | `checkout` (hold ownership), `core` (organizer ownership) |
| `worker` | Celery | — | through service code | both |
| `beat` | Celery Beat | — | — | — |

The rule is simple: **one writer per schema**. `core` is migrated by Django, `checkout` by
Alembic. Cross-service reads are explicit SQL in `api/app/core_repo.py` and
`web/*/readmodel.py`, never ORM imports of the other service's models.

## 2. Request flows

### Browse

```
GET /api/v1/events/{slug}
  FastAPI -> core_repo -> MySQL(core) -> Pydantic response
```

### Hold

```
POST /api/v1/holds
  FastAPI
    -> core_repo (event, ticket types, sale window)
    -> ensure inventory rows (idempotent, named lock on first materialisation)
    -> named lock hold:{user}:{event}   (one active hold per user per event)
    -> conditional UPDATE event_seat ... WHERE status = 'available'
       or UPDATE ga_inventory ... WHERE available >= qty
    -> INSERT hold + hold_items
    -> commit, broadcast seat_update over Redis
```

Failure mid-way rolls the whole transaction back — a partial hold is impossible.

### Pay

```
POST /api/v1/checkout/{hold}/pay   (Idempotency-Key required)
  FastAPI
    -> find order by idempotency_key  (return it if present)
    -> SELECT hold FOR UPDATE
    -> reject if expired / not active
    -> INSERT order(pending) + Stripe PaymentIntent(idempotency key)
    -> commit
```

### Webhook

```
POST /api/v1/webhooks/stripe
  verify signature (live mode) -> JSON
  INSERT stripe_event (UNIQUE) ---- duplicate? return 200, do nothing
  payment_intent.succeeded:
      SELECT order FOR UPDATE
      hold still valid?      -> convert: seats held->sold, tickets, order paid
      hold expired?          -> try re-acquire inventory in a SAVEPOINT
                                success -> convert
                                failure -> refund + audit + email
  UPDATE stripe_event.processed_at, broadcast sale, enqueue confirmation email
  COMMIT
```

The dedupe row and the business change are committed together: if processing fails, the ledger
row disappears and Stripe's retry is processed.

### Scan

```
POST /api/v1/scan
  verify HMAC QR token -> ticket id
  staff must be assigned to the event (core_staff_assignment)
  UPDATE ticket SET status='checked_in' WHERE id=:id AND status='valid'
  rowcount 1 -> valid
  rowcount 0 -> already_used (returns the original scan time)
```

## 3. Concurrency design

| Risk | Mitigation |
|---|---|
| Two buyers, same seat | conditional UPDATE, affected rows must equal requested count |
| Deadlocks across multi-seat holds | seats updated in ascending id order |
| Two active holds, one user | MySQL `GET_LOCK` (row locks cannot guard a row that does not exist yet) |
| Overselling GA | `available >= qty` in the UPDATE predicate + `CHECK (available >= 0)` |
| Duplicate payment | unique idempotency key, unique hold→order |
| Duplicate webhook | unique `stripe_event_id` |
| Duplicate refund | unique `stripe_refund_id` + state machine |
| Slow hold sweeper | payment re-checks expiry inside the transaction |
| First-ever seat materialisation | one-off `INSERT IGNORE` guarded by a named lock |

## 4. Data model highlights

- **Money** is integer cents, `BIGINT`, never floats.
- **Timestamps** are naive UTC in MySQL, and all comparisons use a single `utcnow()` helper.
- **Public ids** are UUIDv4 strings; internal foreign keys are `BIGINT`.
- **Enums** are `VARCHAR` + `CHECK` constraints (portable, readable, no silent truncation).
- `event_seat` has `UNIQUE(event_id, seat_id)` and a `version` column for optimistic diagnostics.
- `orders.hold_id` is UNIQUE — a hold can be paid exactly once, even with different idempotency keys.

## 5. Real time

Group names (`common/realtime.py`):

| Group | Producer | Consumer |
|---|---|---|
| `event_{id}_seats` | holds service, expirer, webhook | public seat map page |
| `hold_{id}` | hold consumer (1 s tick) | the hold owner |
| `event_{id}_sales` | webhook | organizer dashboard |

`api/app/realtime.py` uses `channels_redis` directly, so the API can fan out without going
through Django. Broadcast failures are logged and swallowed: realtime must never break checkout.

## 6. Failure modes

| Failure | Behaviour |
|---|---|
| Meilisearch down | search returns 503; the site falls back to DB `icontains` |
| Redis down | rate limiter fails open; Channel layer reconnects; Celery retries |
| Stripe down | payment start returns 402, order marked failed, hold remains until expiry |
| Worker down | webhooks still commit; emails/indexing are retried from the queue later |
| Duplicate/late webhook | handled by unique ledger + state machine |
| Expired hold paid | automatic refund + email |
| MySQL failover | `pool_pre_ping`, `READ COMMITTED`, short transactions |

## 7. Scaling notes

- API and web are stateless; scale horizontally behind Nginx.
- Seat contention is per event, so the hot row set is small and shardable by `event_id`.
- Read replicas can serve `core_repo`; checkout writes stay on the primary.
- Celery queues are split (`default`, `emails`, `search`, `payments`) so a slow PDF/email job
  cannot delay refunds.
- The sweeper uses `FOR UPDATE SKIP LOCKED` so multiple beat/worker replicas can run it safely.
