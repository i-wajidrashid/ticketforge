# Changelog

All notable changes to this project are recorded here. The format follows [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/), and the project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- Django 6.1 (from 5.2). Django 5.2 is no longer supported, because mail is now configured through Django 6.1's [`MAILERS` setting](https://docs.djangoproject.com/en/6.1/topics/email/). The `EMAIL_BACKEND`, `EMAIL_HOST`, `EMAIL_PORT` and `EMAIL_USE_TLS` environment variables keep their names, so existing `.env` files work unchanged.
- Stripe Python 16 (from 11), gunicorn 26 (from 23), redis-py 8 (from 6), meilisearch-python 0.43 (from 0.33) and drf-spectacular 0.30 (from 0.28).
- Development tools: mypy 2.4, django-stubs 6.1, djangorestframework-stubs 3.18 and pytest-cov 7.
- CI uses the current major versions of `actions/checkout`, `astral-sh/setup-uv`, `actions/upload-artifact`, `docker/setup-buildx-action` and `docker/build-push-action`.

### Added

- `docker-compose.dokploy.yml` for hosts where another proxy, such as Dokploy's Traefik, terminates HTTPS. nginx gets a `proxied` build mode for it that serves plain HTTP and takes the visitor's scheme and address from the proxy's forwarded headers. See "Deploying with Dokploy" in the operations guide.
- A demo notice on every page, a footer line and a `noindex` tag when the site runs as a demo. Demo mode turns on by itself when payments use the fake gateway outside debug mode, and `DEMO_MODE` overrides it.
- `seed_demo_data --if-empty`, and `DEMO_SEED=1` to run it from the `migrate` job, so a public demo seeds itself once on first deploy.

### Fixed

- Organizer and account API endpoints answer a JSON array or a bare value in the request body with a 400 error instead of crashing with a 500.
- An event reminder that the mail server refuses is now logged and skipped. Before, the failure was ignored without a log line and still counted as sent.

### Security

- Django REST Framework 3.18.2 and pytest 9.0.3, which fix the advisories `pip-audit` reported against 3.16 and 8.4.

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
