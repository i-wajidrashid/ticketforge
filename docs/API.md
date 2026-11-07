# API guide

TicketForge exposes three HTTP APIs and three WebSocket channels, all behind nginx on the same origin.

| Base path | Served by | What it covers | Interactive docs |
|---|---|---|---|
| `/accounts/api/` | Django | Signup, tokens, password reset, the current user | (part of the organizer schema) |
| `/api/v1/` | FastAPI | Catalogue, search, holds, checkout, orders, tickets, scanning, webhooks | `/api/docs` |
| `/org/api/v1/` | Django REST Framework | Venues, seats, events, ticket types, staff, sales, refunds | `/org/api/docs/` |
| `/ws/` | Django Channels | Live seat maps, hold countdowns, organizer sales | none |

The OpenAPI documents are at `/api/openapi.json` and `/org/api/schema/`. They are generated from the code, so they are always current. This page explains how the pieces fit together, which the generated docs do not.

## Authentication

Django issues the tokens and both services accept them. Tokens are HS256 JWTs signed with `JWT_SECRET`, which both services read from the same environment.

```bash
curl -sk https://localhost/accounts/api/token/ \
  -H 'Content-Type: application/json' \
  -d '{"email": "fan011@ticketforge.local", "password": "ticketforge123"}'
```

```json
{"access": "eyJ...", "refresh": "eyJ...", "token_type": "Bearer", "expires_in": 900, "user": {"id": ..., "email": "fan011@ticketforge.local", "role": "customer", "is_verified": true, "date_joined": "..."}}
```

Send the access token as `Authorization: Bearer <access>`. It lasts 15 minutes (`JWT_ACCESS_TTL_SECONDS`). Exchange the refresh token, which lasts 7 days (`JWT_REFRESH_TTL_SECONDS`), at `POST /accounts/api/token/refresh/` with `{"refresh": "..."}`.

Limits on the account endpoints:

- Login and password reset: 5 requests per minute per client (DRF scoped throttle).
- Signup: 10 per hour per client.
- Failed logins: after 5 failures within 15 minutes, further attempts on that account are refused for the rest of the window. Two counters are kept, one per account and one per address and account pair, so spreading attempts across many addresses does not help.

Holds, checkout and orders also require a verified email address. An unverified account gets `403 EMAIL_NOT_VERIFIED`.

The organizer API also accepts the Django session cookie, so the browsable pages and Swagger UI work after logging in on the site.

## Errors

Every error from every service uses one envelope, including validation errors, unknown routes and wrong methods:

```json
{"error": {"code": "SEAT_UNAVAILABLE", "message": "Someone just took one of those seats.", "details": {"seat_ids": [41]}}}
```

`code` is stable and meant for programs. `message` is written for people and may change. `details` carries whatever the client needs to recover: the seats that were lost, the fields that failed validation, or the time a ticket was first scanned.

Codes from the checkout API:

| HTTP | Code | When |
|---|---|---|
| 400 | `INVALID_SIGNATURE` | A Stripe webhook failed signature verification. |
| 401 | `UNAUTHORIZED` | Missing, expired or invalid token. |
| 402 | `PAYMENT_FAILED` | Stripe refused to create the payment. |
| 403 | `EMAIL_NOT_VERIFIED` | The account has not confirmed its email address. |
| 403 | `FORBIDDEN` | Signed in, but not allowed (for example, scanning for an event you have no role at). |
| 404 | `NOT_FOUND`, `HOLD_NOT_FOUND`, `TICKET_INVALID` | Unknown resource; a QR code that does not verify; or a ticket that was refunded or cancelled. |
| 405 | `METHOD_NOT_ALLOWED` | The route exists but not with that method. |
| 409 | `EVENT_NOT_SELLABLE` | The event is a draft, cancelled, finished or already started. |
| 409 | `SALE_WINDOW_CLOSED` | Sales for that ticket type have not opened yet or have closed. |
| 409 | `HOLD_EXISTS` | You already have an active hold for this event. |
| 409 | `HOLD_NOT_ACTIVE`, `HOLD_EXPIRED` | The hold was paid, cancelled or ran out. |
| 409 | `SEAT_UNAVAILABLE` | One of the seats was taken. `details.seat_ids` says which. |
| 409 | `SOLD_OUT` | Not enough general admission tickets left. |
| 409 | `IDEMPOTENCY_CONFLICT` | The key belongs to another customer, or this hold already has a payment under a different key. |
| 409 | `REFUND_NOT_ALLOWED` | The order cannot be refunded (already refunded, or a ticket was scanned). |
| 409 | `TICKET_ALREADY_USED` | The ticket was scanned before. `details.checked_in_at` has the time. |
| 422 | `INVALID_QUANTITY`, `VALIDATION_ERROR` | The request body is wrong. `details.fields` lists the problems. |
| 429 | `RATE_LIMITED` | Too many holds in a minute (`HOLD_RATE_LIMIT_PER_MINUTE`, default 10). |
| 503 | `SEARCH_UNAVAILABLE` | Meilisearch could not be reached. |

The organizer API uses the same envelope with DRF's codes: `VALIDATION_ERROR`, `NOT_AUTHENTICATED`, `PERMISSION_DENIED`, `NOT_FOUND` and `RATE_LIMITED`.

## Buying tickets

### 1. Find an event

```
GET /api/v1/events?limit=20&cursor=<next_cursor>
GET /api/v1/events/{slug}
GET /api/v1/events/{slug}/seats
GET /api/v1/search?q=jazz&city=Karachi&category=music&from=2026-11-01&to=2026-12-31&max_price_cents=5000&available_only=true&sort=date
```

None of these need a token. The list is cursor-paginated: pass `next_cursor` from one page to get the next, until it is `null`. `limit` is clamped to 1 to 100.

The event list and event pages can be served from memory for up to `CATALOGUE_CACHE_SECONDS` (5 by default), so an edit by an organizer can take that long to show up. The seat map is never cached: its seat statuses are read from the database on every request.

`/search` uses Meilisearch. `sort` is `relevance` (the default), `date` or `price` (cheapest first); `limit` and `offset` page through results. If Meilisearch is unreachable this endpoint answers `503 SEARCH_UNAVAILABLE`; the website's own search box falls back to a database query instead.

### 2. Hold

```bash
# general admission
curl -sk https://localhost/api/v1/holds -H "Authorization: Bearer $ACCESS" \
  -H 'Content-Type: application/json' \
  -d '{"event_id": 2, "ticket_type_id": 3, "quantity": 2}'

# reserved seating: seat ids come from the seat map
curl -sk https://localhost/api/v1/holds -H "Authorization: Bearer $ACCESS" \
  -H 'Content-Type: application/json' \
  -d '{"event_id": 1, "seat_ids": [41, 42]}'
```

```json
{"id": "df59dad0-2967-4ea0-92ce-58f7302a61c1", "event_id": 2, "status": "active", "expires_at": "2026-11-07T09:25:45.639975", "seconds_left": 599, "total_cents": 7000, "currency": "USD", "items": [{"event_seat_id": null, "seat_label": null, "ticket_type_id": 3, "quantity": 2}]}
```

Send either `seat_ids` or `ticket_type_id` with `quantity`, never both. For seats, the server decides the price: a seat in the section called "Balcony" sells as the ticket type called "Balcony". You only need `ticket_type_id` with seats when a section's name matches no ticket type and the event has more than one seated type.

A hold is all or nothing. If one seat of four is gone, nothing is held and the answer is `409 SEAT_UNAVAILABLE` naming that seat. Each user can have one active hold per event.

`GET /api/v1/holds/{id}` returns the current state. `DELETE /api/v1/holds/{id}` releases it early.

All timestamps in the checkout API are UTC without an offset.

### 3. Pay

```bash
curl -sk -X POST https://localhost/api/v1/checkout/$HOLD_ID/pay \
  -H "Authorization: Bearer $ACCESS" \
  -H "Idempotency-Key: 8f14e45f-ceea-4672-9b5c-1a2d3e4f5a6b"
```

```json
{"order_id": "3e5eb4bc-b542-41e7-9227-c4916b9a0552", "payment_intent_id": "pi_fake_56650a2cc06e3eccef55d2b1", "client_secret": "pi_fake_..._secret_demo", "amount_cents": 7000, "currency": "USD", "status": "pending"}
```

`Idempotency-Key` is required and must be 8 to 120 characters. Generate a new UUID for each purchase and reuse it when you retry that purchase. A retry with the same key returns the same order instead of creating a second one, so a client can safely retry after a timeout. Stripe recommends the same approach for its own API ([idempotent requests](https://docs.stripe.com/api/idempotent_requests)).

With real Stripe, use `client_secret` with Stripe.js to confirm the card payment. The order becomes `paid` when Stripe's `payment_intent.succeeded` webhook reaches `POST /api/v1/webhooks/stripe`, not when the browser says so.

With the fake gateway (the default locally), there is no Stripe to send that webhook. `POST /api/v1/dev/simulate-payment/{order_id}` sends it for you. That endpoint returns `404` whenever a real Stripe gateway is configured.

### 4. Tickets and refunds

```
GET  /api/v1/me/orders
GET  /api/v1/me/tickets/{ticket_id}/pdf
POST /api/v1/me/orders/{order_id}/refund-request    {"reason": "Can't make it"}
```

Each ticket's PDF carries a QR code with an HMAC-signed token. A refund request goes to the organizer, who approves or rejects it. Approval refunds through Stripe and returns the seats to sale. Once any ticket on an order has been scanned at the door, the order can no longer be refunded (`409 REFUND_NOT_ALLOWED`).

## Scanning

```bash
curl -sk -X POST https://localhost/api/v1/scan -H "Authorization: Bearer $STAFF_ACCESS" \
  -H 'Content-Type: application/json' -d '{"qr_token": "<the text in the QR code>"}'
```

```json
{"result": "valid", "checked_in_at": "2026-11-21T14:02:11", "event_id": 1}
```

The caller must be one of: door staff assigned to that event, the organizer who owns it, or an admin. Anyone else gets `403 FORBIDDEN`. A second scan of the same ticket returns `409 TICKET_ALREADY_USED` with the time of the first scan, so door staff can tell a copied ticket from a double tap. The check is a single conditional `UPDATE`, so two doors scanning the same ticket at the same moment cannot both let it in.

## Organizer API

Organizer endpoints need an account with the organizer role (or an admin). Every list is scoped to the caller: an organizer asking for another organizer's event gets `404`, not `403`, so ids cannot be probed.

Creating and publishing an event, as run against the local stack:

```bash
# Venues are shared between organizers
curl -sk https://localhost/org/api/v1/venues/ -H "Authorization: Bearer $ORG"

# A draft event
curl -sk -X POST https://localhost/org/api/v1/events/ -H "Authorization: Bearer $ORG" \
  -H 'Content-Type: application/json' \
  -d '{"title": "Docs Walkthrough Night", "description": "...", "category": "comedy", "venue": 1,
       "starts_at": "2026-12-20T15:00:00Z", "ends_at": "2026-12-20T17:00:00Z"}'
# {"id": 9, "slug": "docs-walkthrough-night", "status": "draft", ...}

# A ticket type
curl -sk -X POST https://localhost/org/api/v1/events/9/ticket-types/ -H "Authorization: Bearer $ORG" \
  -H 'Content-Type: application/json' \
  -d '{"name": "General admission", "price_cents": 2500, "currency": "USD", "quantity_total": 200,
       "max_per_order": 6, "inventory_mode": "ga"}'

# Publish: it now appears in the catalogue and the search index
curl -sk -X POST https://localhost/org/api/v1/events/9/publish/ -H "Authorization: Bearer $ORG"
# {"status": "published", "indexed": true}
```

Publishing needs at least one ticket type and a start time in the future. `inventory_mode` is `ga` or `seated`; seated ticket types are matched to sections by name, as described under [Hold](#2-hold).

Other endpoints (the full list is in `/org/api/docs/`):

| Endpoint | Purpose |
|---|---|
| `POST /org/api/v1/venues/{id}/sections/` | Add a section to a venue. |
| `POST /org/api/v1/sections/{id}/seats/bulk/` | Load seats from CSV, `row,number` per line, up to 5000 per upload. |
| `POST /org/api/v1/events/{id}/cancel/` | Cancel an event and refund every paid order. |
| `GET /org/api/v1/events/{id}/sales/` | Revenue, paid orders, tickets sold and check-ins. |
| `GET, POST, DELETE /org/api/v1/events/{id}/staff/` | Door staff for an event, by email address. |
| `GET /org/api/v1/refunds/` | Refund requests for your events. |
| `POST /org/api/v1/refunds/{id}/approve/`, `.../reject/` | Decide a refund request. Approval is processed by a Celery task. |

Venues are shared, so two rules protect them:

1. Any organizer can read venues and create new ones. Changing a venue, or adding to its sections and seats, is only allowed if every event at that venue belongs to you. Otherwise only an admin can.
2. Once a venue has an event that is past draft, its existing sections and seats cannot be renamed or deleted by anyone through the API, because sold tickets point at those seat rows. New sections and seats can still be added.

## WebSockets

| Path | Who can connect | Messages |
|---|---|---|
| `/ws/events/{event_id}/seats/` | Anyone | `{"type": "seat_update", "seats": [{"seat_id", "label", "status"}, ...]}` |
| `/ws/holds/{hold_id}/?token=<access>` | The hold's owner | `hold_tick` with `seconds_left` every second, then `hold_expired` |
| `/ws/org/events/{event_id}/sales/?token=<access>` | The event's organizer | `{"type": "sale", "tickets", "revenue_cents"}` |

Browsers cannot set headers on a WebSocket handshake, so the access token goes in the query string. nginx and Daphne log only the path for `/ws/`, so tokens do not end up in access logs. A hold socket without a valid token closes with code 4401. A socket whose token is valid but which has no access to that hold or event closes with 4403. Send `{"type": "ping"}` to get `{"type": "pong"}`.

Messages are a hint that something changed, not the source of truth. The site's JavaScript refetches the seat map after every reconnect, because messages sent while a socket was down are gone.
