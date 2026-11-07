# Security policy

TicketForge handles accounts, payments and entry tokens, so please treat it accordingly.

## Reporting a vulnerability

Open a private security advisory on the repository (or email the maintainer). Do not open a
public issue for a vulnerability. You will get an acknowledgement within 48 hours.

## What is in scope

- Authentication, authorisation and object-level access control
- Payment idempotency, webhook verification and refund logic
- Seat/inventory race conditions
- QR token forgery or ticket replay
- Injection, SSRF, path traversal, unsafe file uploads
- Secret leakage

## What is intentionally out of scope for this demo

- Stripe runs in **test mode** only; the fake gateway accepts unsigned webhooks **in local
  development only** (`STRIPE_GATEWAY=fake`). Production must use `STRIPE_GATEWAY=live` with a
  real webhook secret.
- The local stack ships a self-signed TLS certificate and development secrets in `.env.example`.
  Both are meant to be replaced before any public deployment.
- The demo seed command and `dev/simulate-payment` endpoint are disabled by the fake-gateway
  check in any real configuration.

## Hardening checklist before a public deployment

1. Replace every secret in `.env` (`DJANGO_SECRET_KEY`, `JWT_SECRET`, `QR_SECRET`, Stripe keys).
2. Set `DJANGO_DEBUG=0`, a real `DJANGO_ALLOWED_HOSTS`, and a real `SITE_ORIGIN`.
3. Issue a real certificate (Let's Encrypt) and remove the self-signed one.
4. Put MySQL/Redis/Meilisearch on a private network — never publish their ports.
5. Rotate `QR_SECRET` only after re-issuing tickets, or old QR codes stop validating.
6. Enable `pip-audit` + Dependabot and keep the base images patched.
