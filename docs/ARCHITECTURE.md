# Architecture

This document describes how TicketForge is put together and what happens on each important request. The reasons behind the larger choices, and what they cost, are in [DECISIONS.md](DECISIONS.md).

## Services

| Service | Runs | Owns | Writes to | Reads from |
|---|---|---|---|---|
| `web` | Django 5 and DRF under Gunicorn | the `core` schema | `core` | `checkout`, for the account page and organizer figures |
| `api` | FastAPI and SQLAlchemy 2 under Uvicorn | the `checkout` schema | `checkout` | `core`: events, ticket types, seats, users, staff |
| `realtime` | Django Channels under Daphne | nothing | the Redis channel layer | `checkout` for hold ownership, `core` for event ownership |
| `worker` | Celery | nothing | both, only through each schema's own service code | both |
| `beat` | Celery Beat | the schedule | nothing | nothing |
| `nginx` | nginx 1.27 | TLS, routing, the edge rate limit | | |
| `migrate` | a one-shot container | | runs Django migrations, then Alembic | |

Supporting containers: MySQL 8.4, Redis 7, Meilisearch 1.11 and Mailpit.

### One writer per schema

`core` is created and migrated by Django. `checkout` is created and migrated by Alembic. Each service may read the other's tables but never writes them. Cross-schema reads are explicit SQL in two places only, `api/app/core_repo.py` and the `readmodel.py` modules under `web/`, so it is easy to find everything that depends on the other side's column names. Neither service imports the other's ORM models.

The worker is the one process that touches both schemas, and it does so by calling the owning side's code: refunds go through `api.app.services.payments`, search indexing goes through Django models.

### Routing

nginx terminates TLS and routes by path:

| Path | Goes to |
|---|---|
| `/api/` | `api` (port 8001) |
| `/ws/` | `realtime` (port 8002), with the WebSocket upgrade headers |
| `/static/`, `/media/` | files on shared volumes, served by nginx |
| everything else, including `/org/` and `/admin/` | `web` (port 8000) |

The upstreams are resolved through Docker's DNS at request time, so restarting one service does not require restarting nginx.

## Request flows

### Browsing the catalogue

```
GET /api/v1/events
  api: in-process cache hit?  -> return the stored JSON bytes
       miss                   -> core_repo: events + ticket types (2 queries)
                              -> serialize once, store the bytes for 5 s, return
```

The event list and single event pages are identical for every visitor, so each API worker keeps the finished response bytes for `CATALOGUE_CACHE_SECONDS` (5 by default). A cache hit skips the database and also skips Pydantic validation and JSON encoding, which were most of the CPU time once the queries were fast. The cache lives in each process; there is no shared cache to invalidate. The cost is that an organizer's edit can take up to 5 seconds to appear.

### Reading a seat map

```
GET /api/v1/events/{slug}/seats
  api: event by slug
       layout (seats, ticket types, which seat sells as which type)  <- cached 5 s
       seat statuses: SELECT seat_id, status FROM event_seat WHERE event_id = ?  <- never cached
       a seat with no event_seat row is available
```

Seat statuses are always read live. The physical layout and pricing change only when an organizer edits the venue, so they share the catalogue cache.

`event_seat` rows are created lazily, the first time anyone holds a seat at an event. Until then a seat with no row is simply available. Creating the rows is the hold path's job, not the seat map's, so loading a seat map is a pure read and never takes a lock.

### Holding seats

```
POST /api/v1/holds
  api:
    event is sellable, has started? ticket types exist?
    rate limit: 10 holds per user per minute (Redis)
    make sure event_seat / ga_inventory rows exist
        (own connection, committed, guarded by GET_LOCK on first creation)
    GET_LOCK('hold:{user}:{event}')
        expire this user's stale holds for the event
        refuse if an active hold remains                     -> 409 HOLD_EXISTS
        for each seat, in ascending seat id order:
            UPDATE event_seat SET status='held', hold_id=?, version=version+1
            WHERE event_id=? AND seat_id=? AND status='available'
            0 rows -> roll back everything                    -> 409 SEAT_UNAVAILABLE
        or, for general admission:
            UPDATE ga_inventory SET available = available - ?
            WHERE ticket_type_id=? AND available >= ?
            0 rows -> roll back                               -> 409 SOLD_OUT
        INSERT hold, hold_items
    COMMIT, release the named lock
    broadcast seat_update to /ws/events/{id}/seats/
```

The affected-row count is the lock. Two requests for the same seat both run the `UPDATE`; InnoDB applies them one after the other, and the second finds `status` is no longer `available` and touches nothing. There is no window between reading the status and writing it.

The rows are created before the named lock is taken and on a separate connection. Creating them inside the hold transaction would hold insert locks on the unique index while the hold's own updates run, which could deadlock with another buyer doing the same thing.

The named lock covers a different rule: one active hold per user per event. A unique index cannot say "at most one row where status is active", and a row lock cannot protect a row that does not exist yet. MySQL's `GET_LOCK` can. It is keyed per user and event, so it never makes two different buyers wait for each other. See the [MySQL locking functions](https://dev.mysql.com/doc/refman/8.0/en/locking-functions.html) reference; a named lock is released by `RELEASE_LOCK` or when the session ends, not by `COMMIT`, which is why the code releases it in a `finally`.

### Paying

```
POST /api/v1/checkout/{hold_id}/pay      Idempotency-Key: <uuid>
  api:
    order with this key exists?  -> return it (same customer only)
    SELECT hold ... FOR UPDATE   (two clicks on "Pay" queue here)
    order for this hold exists?  -> return it
    hold expired?                -> release it                -> 409 HOLD_EXPIRED
    INSERT order (pending, idempotency_key UNIQUE, hold_id UNIQUE)
    Stripe: create PaymentIntent with the same idempotency key
        fails -> roll back, nothing stored                    -> 402 PAYMENT_FAILED
    COMMIT
```

The Stripe call happens while the hold row is locked. That lock only blocks the same customer's second click on the same hold, so it costs nothing for anyone else, and it guarantees one PaymentIntent per hold.

If Stripe fails or times out, nothing is stored and the hold stays payable. The browser keeps one key per hold, so its retry sends the same key to Stripe. Stripe returns the original PaymentIntent if the first request did reach it, as its [idempotent requests](https://docs.stripe.com/api/idempotent_requests) reference describes.

### Receiving a Stripe webhook

```
POST /api/v1/webhooks/stripe
  api:
    verify the Stripe-Signature header (real Stripe only)    -> 400 INVALID_SIGNATURE
    INSERT stripe_event (stripe_event_id UNIQUE)
        duplicate -> 200, do nothing
    payment_intent.succeeded:
        SELECT order ... FOR UPDATE, SELECT hold ... FOR UPDATE
        hold still active   -> convert
        hold expired        -> try to take the same inventory again, in a SAVEPOINT
                                 got it -> convert
                                 lost   -> refund through Stripe, audit, email the customer
        convert = seats held->sold, tickets with signed QR tokens, order paid
    payment_intent.payment_failed -> order failed; the hold keeps its seats and the
                                     PaymentIntent can still be retried with another card
    charge.refunded / refund.updated -> order refunded, seats released
    stripe_event.processed_at = now
    COMMIT
    after commit: broadcast the sale, queue the confirmation email
```

The ledger insert and the order change commit together. If processing raises, both roll back and Stripe's retry gets a clean second attempt. If processing succeeded, a retry hits the unique index and does nothing. Stripe's [webhooks guide](https://docs.stripe.com/webhooks) states that endpoints can receive the same event more than once and that delivery order is not guaranteed; the ledger handles the first, and handling each event type from the order's current state handles the second.

A payment that succeeds after its hold expired is not a hypothetical. Card payments with 3-D Secure can take minutes. The savepoint lets the handler try to keep the customer's seats without abandoning the whole transaction if someone else has them.

### Scanning a ticket

```
POST /api/v1/scan     {"qr_token": "..."}
  api:
    verify the HMAC in the token                       -> 404 TICKET_INVALID
    caller is staff at this event, its organizer, or an admin?   -> 403 FORBIDDEN
    UPDATE ticket SET status='checked_in', checked_in_at=now
    WHERE id=? AND status='valid'
        1 row  -> "valid"
        0 rows -> 409 TICKET_ALREADY_USED with the first scan time
```

The QR token is the ticket id plus an HMAC-SHA256 signature made with `QR_SECRET`, compared with [`hmac.compare_digest`](https://docs.python.org/3/library/hmac.html#hmac.compare_digest) so the comparison takes the same time whether the first byte or the last one is wrong. A forged code fails before the database is touched. Two doors scanning the same ticket at the same moment run the same conditional `UPDATE`, and only one gets a row.

### Refunds

A customer asks, an organizer approves or rejects in the dashboard, and a Celery task on the `payments` queue calls Stripe and returns the seats to sale. Open seat maps show those seats as free straight away. The refund's Stripe id is unique and refund states only move forward (`requested`, `processing`, `succeeded` or `failed`), so a task retried after a crash cannot refund twice. Orders with a scanned ticket cannot be refunded, because the customer has already been let in.

Cancelling an event queues a refund for every paid order. The task is queued with `transaction.on_commit`, so if the cancellation rolls back, no refunds go out. Django's documentation covers this in [performing actions after commit](https://docs.djangoproject.com/en/stable/topics/db/transactions/#performing-actions-after-commit).

## Background work

Celery uses Redis as its broker. Tasks are routed to four queues so slow work cannot delay urgent work:

| Queue | Tasks |
|---|---|
| `payments` | refunds, event cancellation refunds, payment reconciliation |
| `emails` | verification, password reset, order confirmation with PDF tickets, reminders, late-payment notices |
| `search` | Meilisearch index updates |
| `default` | everything else: expiring holds, marking events finished |

Every task takes ids, loads fresh state, and is safe to run twice. The worker runs with `task_acks_late` and `task_reject_on_worker_lost`, so a task whose worker dies mid-run is delivered again instead of lost, and `worker_prefetch_multiplier = 1`, so one long task does not hold others hostage. The [Celery configuration reference](https://docs.celeryq.dev/en/stable/userguide/configuration.html#std-setting-task_acks_late) explains why late acknowledgement requires idempotent tasks. Retries back off exponentially with jitter, as described in Marc Brooker's [Exponential Backoff and Jitter](https://aws.amazon.com/blogs/architecture/exponential-backoff-and-jitter/).

Beat schedule:

| Task | When |
|---|---|
| Release expired holds | every 30 seconds |
| Mark finished events | every hour |
| Reconcile payments with Stripe | 02:00 |
| Event reminders | 09:00 in `TIME_ZONE` |

The hold sweeper selects with `FOR UPDATE SKIP LOCKED`, so if two sweepers run at once they split the work instead of waiting on each other. MySQL's [locking reads](https://dev.mysql.com/doc/refman/8.0/en/innodb-locking-reads.html) documentation describes `SKIP LOCKED` as meant for queue-like tables, which is what expired holds are.

## Real time

Group names live in `common/realtime.py` so both services agree on them.

| Group | Sent by | Received by |
|---|---|---|
| `event_{id}_seats` | new holds, released holds, the sweeper, paid orders, refunds | every open seat map for the event |
| `hold_{id}` | the hold consumer, once per second | the hold's owner |
| `event_{id}_sales` | the webhook handler after a sale | the organizer's dashboard |

The API publishes straight to the Redis channel layer with `channels_redis`, so it does not need Django to fan out a message. A failed broadcast is logged and ignored: real-time updates are a convenience and must never fail a checkout.

Messages are hints, not the source of truth. The browser client refetches the seat map every time its socket reconnects, because anything sent while it was disconnected is lost.

## Data model notes

- Money is stored as integer cents in `BIGINT` columns. There are no floats anywhere in the payment path.
- Timestamps are stored as UTC without a time zone, and the API compares them with one `utcnow()` helper. Pages, PDFs and emails convert to `TIME_ZONE` for display.
- Orders, holds and tickets have UUID primary keys, so ids in URLs cannot be guessed or counted. Internal foreign keys are `BIGINT`.
- Status columns are `VARCHAR` with `CHECK` constraints, which MySQL 8.0.16 and later enforce.
- `event_seat` has `UNIQUE (event_id, seat_id)`. `ga_inventory` has `CHECK (available >= 0)`, a second line of defence behind the conditional update.
- `orders.idempotency_key`, `orders.hold_id`, `stripe_event.stripe_event_id` and `refund.stripe_refund_id` are all `UNIQUE`. Each one turns a duplicate into an integrity error instead of a duplicate charge.
- The checkout connection uses `READ COMMITTED` and `pool_pre_ping`. Pre-ping costs one small round trip per connection checkout and in exchange a connection dropped by a MySQL restart is replaced instead of failing a request; see SQLAlchemy's notes on [pessimistic disconnect handling](https://docs.sqlalchemy.org/en/20/core/pooling.html#disconnect-handling-pessimistic).

## When something is down

| What fails | What happens |
|---|---|
| Meilisearch | `/api/v1/search` returns `503 SEARCH_UNAVAILABLE`. The website's search falls back to a database query. Index updates wait in the `search` queue. |
| Redis | Rate limits fail open, because availability matters more than throttling and nginx still limits by IP. Celery and the channel layer reconnect when Redis returns. Live updates pause. |
| Stripe | Starting a payment returns `402`. Nothing is stored, the hold keeps running, and the customer can retry. |
| The worker | Payments still complete, because webhooks are handled in the API. Emails, refunds and indexing queue up and run when it is back. |
| A webhook arrives twice or out of order | The ledger ignores the duplicate; each handler works from the order's current state. |
| A payment lands after its hold expired | The seats are taken again if free; otherwise the payment is refunded and the customer is emailed. |
| MySQL restarts | Dropped pooled connections are detected by pre-ping and replaced. Requests that were mid-transaction fail and roll back. |

## Scaling

- `web` and `api` keep no state between requests and scale by adding processes or containers behind nginx. Each API process has its own catalogue cache, which only makes the cache warm more often.
- Seat contention is per event. Even a large on-sale is a few thousand hot rows in one event, and the conditional update holds each row lock for one statement.
- Catalogue reads in `core_repo` can be pointed at a read replica. Checkout writes must stay on the primary.
- Two sweepers are safe because of `SKIP LOCKED`; two Beat schedulers are not, because Beat would queue every periodic task twice. Run one `beat`.
- Measured throughput on a laptop is in the [README](../README.md#load-test-results).
