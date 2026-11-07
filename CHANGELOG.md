# Changelog

All notable changes to this project are recorded here. The format follows [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/), and the project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

Nothing yet.

## [1.0.0] - 2026-11-07

First public release.

### Added

- Accounts: signup with email verification, login, password reset, JWT access and refresh tokens shared by both services, and login lockout after 5 failures in 15 minutes.
- Catalogue: events with general admission or reserved seating, ticket types with sale windows and per-order limits, cover images, and Meilisearch search with filters, falling back to the database on the website.
- Checkout: 10-minute holds with a live countdown, conditional-update seat locking, one hold per user per event, idempotent payment start, Stripe PaymentIntents, a webhook ledger, and automatic refunds for payments that arrive after a hold was resold.
- Tickets: PDF tickets with HMAC-signed QR codes, emailed and downloadable, and a door scanner page that works with a camera or a handheld scanner.
- Organizers: an API for venues, sections, seats (with CSV upload), events, ticket types and door staff; a dashboard with revenue, check-ins, a daily revenue chart and live sales; refund approval; event cancellation with automatic refunds.
- Real time: live seat maps, hold countdowns and organizer sales over WebSockets.
- Operations: JSON logs with request ids carried from nginx to the application, health and readiness endpoints, a Celery Beat schedule (hold expiry, nightly payment reconciliation, event reminders, finished events), a production Compose overlay, a load-test overlay and Locust profile, and a read-only sales report notebook.
- Quality: 227 tests against real MySQL and Redis, including thread races and a Hypothesis property test; ruff, mypy, migration checks, `pip-audit` and an image build in CI.
- Documentation: README, architecture, API guide, operations runbook, design decisions, security policy and contribution guide.

### Security

These were found and fixed while preparing the release.

- Django refuses to start outside debug mode with development secrets, secrets shorter than 32 characters, or a payment setup that would silently fall back to the fake gateway and make tickets free.
- `DJANGO_DEBUG` defaults to off when unset.
- The production overlay turns debug off for every Django process and stops publishing the MySQL, Redis, Meilisearch and Mailpit ports.
- Organizers can no longer change venues, sections or seats that another organizer's events use, and nobody can rename or delete existing seats under a published event through the API.
- WebSocket access tokens no longer appear in nginx access logs.
- Every page is served with a Content Security Policy that allows no inline code.
- With the production overlay, the database user no longer gets the rights the test suite needs.
- A completed password reset ends a login lockout, so an attacker cannot keep the real owner locked out.
- The local-development Mailpit hint no longer appears on production pages.
- The MySQL bootstrap script uses the configured user and database names instead of hard-coded ones.

### Fixed

- A Stripe error while starting a payment no longer leaves the hold permanently unpayable.
- Event figures no longer double count revenue from orders with several tickets.
- Organizers can scan tickets for their own events without assigning themselves as staff.
- Orders with a scanned ticket can no longer be refunded.
- FastAPI's own 404 and 405 responses use the same error envelope as every other error.
- Seat maps are read-only, so loading one never takes a lock or writes rows.
- Mobile layouts no longer scroll sideways on the home, event and organizer pages.
- Refunded seats show as free on open seat maps without a reload.
- "Seat just taken" messages name the section as well as the row and seat.
- The test client no longer prints a deprecation warning (`httpx2` replaces `httpx` in the dev dependencies).

[Unreleased]: https://github.com/i-wajidrashid/ticketforge/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/i-wajidrashid/ticketforge/releases/tag/v1.0.0
