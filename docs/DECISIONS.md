# Design decisions

Short architecture decision records. Each one says what was decided, why, what it costs, and where the reasoning comes from. If you want to change one of these, open an issue first and explain which of the reasons no longer holds.

1. [Two schemas, one writer each](#1-two-schemas-one-writer-each)
2. [Conditional updates decide who gets a seat](#2-conditional-updates-decide-who-gets-a-seat)
3. [A named lock for "one hold per user per event"](#3-a-named-lock-for-one-hold-per-user-per-event)
4. [The webhook ledger commits with the order](#4-the-webhook-ledger-commits-with-the-order)
5. [Idempotency keys from the browser to Stripe](#5-idempotency-keys-from-the-browser-to-stripe)
6. [A fake payment gateway that cannot be reached by accident](#6-a-fake-payment-gateway-that-cannot-be-reached-by-accident)
7. [Refuse to start with development secrets](#7-refuse-to-start-with-development-secrets)
8. [A five-second catalogue cache in each API process](#8-a-five-second-catalogue-cache-in-each-api-process)
9. [Seat maps are pure reads](#9-seat-maps-are-pure-reads)
10. [Who can scan, and no refunds after a scan](#10-who-can-scan-and-no-refunds-after-a-scan)
11. [Venues are shared, so their layout is guarded](#11-venues-are-shared-so-their-layout-is-guarded)
12. [Two rate limits, and a separate one for load tests](#12-two-rate-limits-and-a-separate-one-for-load-tests)
13. [Background tasks are allowed to run twice](#13-background-tasks-are-allowed-to-run-twice)
14. [Server-rendered pages with a little JavaScript](#14-server-rendered-pages-with-a-little-javascript)
15. [Seats stay 30 pixels on phones](#15-seats-stay-30-pixels-on-phones)
16. [Money in cents, time in UTC](#16-money-in-cents-time-in-utc)
17. [PDF tickets use a built-in font](#17-pdf-tickets-use-a-built-in-font)
18. [A Content Security Policy with no inline exceptions](#18-a-content-security-policy-with-no-inline-exceptions)

---

## 1. Two schemas, one writer each

**Decision.** Django owns the `core` schema and FastAPI owns `checkout`. Each service migrates and writes only its own schema, and reads the other with hand-written SQL in one module per side (`api/app/core_repo.py`, `web/*/readmodel.py`).

**Why.** The checkout path has different load, different failure modes and a different release rhythm from the catalogue and admin. Giving it its own schema and migration tool means a schema change on one side cannot lock tables on the other, and the boundary is visible in code review: any write across it is obviously wrong.

**Cost.** Two migration tools. Cross-schema reads are raw SQL, so renaming a `core` column means updating `core_repo.py` in the same change; the tests run against real MySQL, so a mismatch fails CI rather than production.

## 2. Conditional updates decide who gets a seat

**Decision.** Every seat and stock change is an `UPDATE ... WHERE status = 'available'` (or `WHERE available >= :quantity`), and the affected-row count decides the outcome. Multi-seat holds update in ascending seat id order.

**Why.** It is one statement, so there is no gap between checking and writing for another request to slip into. It needs no `SELECT ... FOR UPDATE` round trip, and the row lock lasts for one statement, not the whole request. A consistent update order is the standard way to stop two transactions that lock the same rows from deadlocking. It is also simple to test: `tests/test_concurrency.py` fires 50 threads at one seat and asserts exactly one wins.

**Cost.** The losing request learns only that it lost, not who won. That is all a buyer needs to know.

## 3. A named lock for "one hold per user per event"

**Decision.** Hold creation runs inside `GET_LOCK('hold:{user}:{event}')`.

**Why.** The rule is about rows that may not exist yet. A unique index cannot express "at most one row whose status is active", and a row lock cannot be taken on a row that has not been inserted. MySQL's named locks can guard that. They are keyed per user and event, so two different buyers never wait on each other. Keeping this in MySQL instead of Redis means the correctness of a sale never depends on a second data store.

**Cost.** A named lock is not tied to the transaction: the [MySQL manual](https://dev.mysql.com/doc/refman/8.0/en/locking-functions.html) notes it is released by `RELEASE_LOCK` or when the session ends, not by commit or rollback. `api/app/services/locks.py` therefore releases it in a `finally` block.

## 4. The webhook ledger commits with the order

**Decision.** Each Stripe event id is inserted into `stripe_event` (unique) in the same transaction as the order change it causes.

**Why.** Stripe's [webhooks guide](https://docs.stripe.com/webhooks) says the same event can be delivered more than once, that delivery order is not guaranteed, and recommends logging processed event ids. Committing the id together with the change gives exactly-once processing on top of at-least-once delivery: if processing fails, both roll back and the retry starts clean; if it succeeds, a retry fails the unique insert and does nothing.

**Cost.** Webhook handling happens inside the request, not on a queue, so it must be fast. It is a handful of indexed statements; the email and the live broadcast happen after commit.

## 5. Idempotency keys from the browser to Stripe

**Decision.** `POST /checkout/{hold}/pay` requires an `Idempotency-Key` header. The browser creates one key per hold and reuses it for every retry. The key is stored on the order with a unique index and passed to Stripe as Stripe's own idempotency key. If the Stripe call fails, nothing is stored.

**Why.** Stripe's [idempotent requests](https://docs.stripe.com/api/idempotent_requests) reference describes the contract: the same key returns the same result, which makes it safe to retry after a timeout. Using one key from the browser all the way to Stripe means a retry at any layer converges on one order and one PaymentIntent. `orders.hold_id` is also unique, so even two different keys cannot produce two orders for one hold.

Storing nothing on failure matters. An earlier version kept a `failed` order when Stripe errored, and because a hold can only have one order, that hold could never be paid again. Rolling back keeps the hold payable, and the browser's retry sends the same key, so if the first request did reach Stripe, the original PaymentIntent comes back.

**Cost.** The Stripe call happens while the hold row is locked. Only the same customer's second click on the same hold waits on that lock.

## 6. A fake payment gateway that cannot be reached by accident

**Decision.** `STRIPE_GATEWAY=auto` (the default) uses real Stripe when a real-looking `sk_test_` key is configured, and a deterministic in-process fake otherwise. A development-only endpoint, `/api/v1/dev/simulate-payment/{order_id}`, plays the webhook that real Stripe would send. Outside debug mode, Django refuses to start if `auto` would fall back to the fake, unless `STRIPE_GATEWAY=fake` is set on purpose.

**Why.** Anyone can clone the repository and buy a ticket in a minute, offline, and the test suite never touches the network. But the fake gateway accepts unsigned webhooks and exposes the simulator, which together make checkout free. That is fine on a laptop and a disaster in production, so reaching it in production has to be a decision someone wrote down, not the result of a missing variable.

**Cost.** A public demo that wants free checkout has to set one extra variable.

## 7. Refuse to start with development secrets

**Decision.** `DJANGO_DEBUG` defaults to off. With debug off, settings raise `ImproperlyConfigured` if `DJANGO_SECRET_KEY`, `JWT_SECRET` or `QR_SECRET` is one of the values from `.env.example` or shorter than 32 characters. The production overlay forces debug off for every Django process.

**Why.** These secrets sign sessions, API tokens and the QR codes that get people through the door. A deployment that forgot to change them would let anyone forge tickets. Failing at startup turns a silent hole into a deploy that does not come up. Defaulting debug to off means a missing variable shows a 500 page instead of a stack trace with settings in it.

**Cost.** Running with debug off locally needs real-looking secrets. The test settings set their own.

## 8. A five-second catalogue cache in each API process

**Decision.** The event list and single event responses are cached as finished JSON bytes in each API process for `CATALOGUE_CACHE_SECONDS` (5 by default). Seat statuses and anything to do with checkout are never cached.

**Why.** Before an on-sale, thousands of people request the same few pages. Profiling under load showed the queries were no longer the bottleneck once batched; Pydantic validation and JSON encoding of the same objects were. Storing the bytes skips both. An in-process cache needs no invalidation protocol and no network hop. Load testing on a laptop went from about 250 requests per second with a 2.8 second p95 to 379 requests per second with an 84 millisecond p95, together with decision 9.

**Cost.** An organizer's change can take up to 5 seconds to show in the public API. Each process warms its own cache, so with N workers the database sees up to N queries per window instead of one. Set it to 0 to turn caching off.

## 9. Seat maps are pure reads

**Decision.** `event_seat` rows are created the first time someone holds a seat at an event. The seat map treats a seat with no row as available and never creates rows itself.

**Why.** The first version created missing rows while serving the seat map. Under load that meant the busiest read endpoint took a named lock and ran inserts. A seat that has never been touched is available by definition, so the read can say so without writing anything.

**Cost.** "Seat exists but has no row yet" is a state the code has to remember. It lives in one function (`ensure_event_seats`), and a property test exercises it.

## 10. Who can scan, and no refunds after a scan

**Decision.** A ticket can be scanned by door staff assigned to that event, by the organizer who owns the event, or by an admin. Once any ticket on an order has been scanned, the order cannot be refunded.

**Why.** At small events the organizer is the door staff, and making them assign themselves was a needless step that failed at the worst moment. Refunding a ticket that was already used would let someone attend and get their money back.

**Cost.** An organizer who wants to refund a customer who was let in by mistake has to do it in the Stripe dashboard.

## 11. Venues are shared, so their layout is guarded

**Decision.** Venues have no owner; any organizer can read them and create new ones. Changing a venue, or adding to its sections and seats, is allowed for an admin or for an organizer whose events are the only ones there. Once a venue has an event that is past draft, its existing sections and seats cannot be changed or deleted through the API by anyone; new ones can be added.

**Why.** Shared venues match reality: many promoters book the same hall. But tickets point at seat rows, and seat deletes cascade, so renaming or removing a seat under a live event would rewrite or orphan tickets that were already sold. Without these rules any organizer could have done that to another organizer's event.

**Cost.** Fixing a typo in a seat label after publishing needs an admin and the Django admin.

## 12. Two rate limits, and a separate one for load tests

**Decision.** nginx limits each client IP to 20 requests per second with a burst of 40. The API adds per-user limits in Redis (10 holds per minute, 30 payment starts per minute) that fail open if Redis is down. Load tests swap in a much higher nginx limit through an overlay file, and `make loadtest-off` restores the real one.

**Why.** The edge limit protects the whole stack from one noisy client and costs nothing per request. Per-user limits stop one account from churning holds to keep seats away from everyone else. They fail open because, during a Redis outage, refusing every sale is worse than briefly not throttling, and nginx is still limiting by IP. A single-machine load test sends everything from one IP, so with the real limit nginx rejects nearly all of it with `503` (the [default rejection status](https://nginx.org/en/docs/http/ngx_http_limit_req_module.html)) and the test measures nginx. A separate file that is never baked into the image keeps that change from shipping.

**Cost.** Many users behind one carrier NAT share one IP budget. Raise the limit in `deploy/nginx/limits.conf` if that is your audience.

## 13. Background tasks are allowed to run twice

**Decision.** Celery tasks take ids, reload state, and are written so a second run does nothing harmful. Workers run with `task_acks_late`, `task_reject_on_worker_lost` and a prefetch of 1. Tasks are split across `payments`, `emails`, `search` and `default` queues. Retries use exponential backoff with jitter.

**Why.** Late acknowledgement means a task whose worker crashes is delivered again instead of lost. The [Celery documentation](https://docs.celeryq.dev/en/stable/userguide/configuration.html#std-setting-task_acks_late) is explicit that this requires idempotent tasks, so they are written that way. Separate queues stop a pile of emails from delaying refunds. Jitter keeps a burst of failures from retrying in lockstep, as Marc Brooker shows in [Exponential Backoff and Jitter](https://aws.amazon.com/blogs/architecture/exponential-backoff-and-jitter/).

**Cost.** Every task has to be written with "what if this already ran" in mind. The refund state machine and unique Stripe refund ids are what make the most dangerous one safe.

## 14. Server-rendered pages with a little JavaScript

**Decision.** Django templates render every page. JavaScript is used only where it changes the experience: the seat map, the hold countdown, live updates, the scanner and toasts. Those scripts call the same public JSON API that third parties would.

**Why.** Pages work and are readable before any script runs, they are easy to make accessible, and there is no build step. Using the public API from the site's own scripts keeps that API honest.

**Cost.** No client-side routing. Moving between pages is a full page load, which for a ticketing site is a few per visit.

## 15. Seats stay 30 pixels on phones

**Decision.** Seat buttons are 30 by 30 CSS pixels with 6 pixel gaps at every screen size. On narrow screens the seat map scrolls sideways inside its own box, with a hint above it, instead of shrinking the seats.

**Why.** WCAG 2.2 success criterion 2.5.8, [Target Size (Minimum)](https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum.html), asks for targets of at least 24 by 24 CSS pixels at level AA. Shrinking a 20-seat row to fit a 390 pixel phone would push each seat well under that and make people tap the wrong seat. Scrolling one box sideways is a smaller cost than buying the wrong seat.

A related layout rule: the page grids use `minmax(0, 1fr)` rather than `1fr`. The [CSS Grid specification](https://www.w3.org/TR/css-grid-1/#track-sizes) says a bare flexible track "implies an automatic minimum", so a wide seat map would otherwise stretch the column past the screen instead of scrolling inside it.

**Cost.** On a phone, a large venue needs a sideways swipe to see every seat.

## 16. Money in cents, time in UTC

**Decision.** All amounts are integer cents in `BIGINT` columns. All timestamps are stored in UTC without a time zone, and converted to `TIME_ZONE` only for display.

**Why.** Floats cannot represent most decimal amounts exactly, and payment providers, Stripe included, take integer minor units anyway. Storing one time zone removes any question of what a stored time means; MySQL `DATETIME` has no zone of its own.

**Cost.** Code that displays a time must convert it. The template filters and PDF renderer do this in one place each.

## 17. PDF tickets use a built-in font

**Decision.** PDF tickets are drawn with fpdf2's built-in Helvetica. Text is passed through a small function that maps typographic characters (curly quotes, dashes, ellipses) to Latin-1 equivalents before drawing.

**Why.** The [fpdf2 documentation](https://py-pdf.github.io/fpdf2/Unicode.html) explains that the built-in fonts only cover Latin characters. Embedding a Unicode font would add a font file to the image and to every PDF, for a document that mostly prints a name, a date, a seat and a QR code.

**Cost.** An event title in a non-Latin script would print with replacement characters. Supporting that means adding a TTF font with `add_font` and dropping the mapping.

## 18. A Content Security Policy with no inline exceptions

**Decision.** Every HTML page from Django carries a Content Security Policy that allows scripts, styles, fonts and images from this origin only, adds the Stripe.js and Link hosts that Stripe's [integration security guide](https://docs.stripe.com/security/guide) lists, and allows no inline scripts, inline event handlers or `style` attributes. Page configuration is passed in `json_script` data blocks, which browsers do not execute. Values that used to be inline styles are classes (category colours) or SVG attributes (progress bars).

**Why.** Output escaping is the first defence against cross-site scripting; the policy is the second. A policy that still allows `'unsafe-inline'` would let an injected `<script>` run anyway, so it is worth the small cost of keeping markup free of inline code. A test scans every template so the rule cannot erode quietly.

**Cost.** The Django admin and the Swagger UI are exempt, because their markup is not ours. A new Stripe feature may need extra hosts from Stripe's list.
