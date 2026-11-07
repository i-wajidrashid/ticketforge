# Security

TicketForge handles accounts, payments and the tokens that get people into venues. Please report problems privately and give maintainers a chance to fix them before they are public.

## Reporting a vulnerability

Use GitHub's private vulnerability reporting: open the repository's **Security** tab and choose **Report a vulnerability**. Please do not open a public issue, pull request or discussion for a security problem.

A useful report says what an attacker can do, what they need first (an account? an organizer account? network access?), and the steps or a request that shows it. Maintainers will confirm they received it, work out a fix with you, and agree on when to publish. Credit is given in the advisory and the changelog unless you would rather stay anonymous.

Only the latest release on the default branch gets security fixes.

## In scope

- Authentication, sessions, JWT handling and login throttling
- Authorization: one organizer reaching another's events, venues, sales or refunds; a customer reaching another's orders or tickets; scanning without a role at the event
- Paying less than the price, getting tickets without paying, or being charged twice
- Selling one seat or ticket twice
- Forging, replaying or reusing QR tickets
- Webhook spoofing
- Injection of any kind, cross-site scripting, cross-site request forgery, request smuggling through the nginx configuration
- Leaks of secrets, tokens or other customers' data through responses, logs or error pages

## Not in scope

- The fake payment gateway behaving like a fake. It accepts unsigned webhooks and makes checkout free by design, for local use. Django will not start on it outside debug mode unless `STRIPE_GATEWAY=fake` is set explicitly.
- The self-signed certificate and the development secrets in `.env.example`, which exist to be replaced.
- Denial of service by sheer volume. Rate limits exist, but absorbing a flood is the job of the platform in front of the app.
- Findings from automated scanners without a demonstrated impact.

## How the important parts are protected

| Threat | Protection | Where |
|---|---|---|
| Two buyers get one seat | Conditional `UPDATE` with the affected-row count as the lock; `UNIQUE (event_id, seat_id)`; `CHECK (available >= 0)` | `api/app/services/holds.py` |
| Duplicate charge | `Idempotency-Key` stored with a unique index and forwarded to Stripe; one order per hold | `api/app/services/payments.py` |
| Replayed or spoofed webhook | Stripe signature verification with the endpoint secret; unique ledger of processed event ids | `api/app/routers/webhooks.py` |
| Forged ticket | QR token carries an HMAC-SHA256 of the ticket id under `QR_SECRET`, checked with `hmac.compare_digest` | `common/security.py` |
| Ticket used twice | Check-in is a conditional `UPDATE ... WHERE status = 'valid'` | `api/app/services/tickets.py` |
| Scanning by anyone | Only staff assigned to the event, its organizer, or an admin | `api/app/core_repo.py` (`can_scan_event`) |
| Organizer reads another's data | Every organizer queryset is filtered by owner; foreign ids return 404 | `web/organizers/views.py` |
| Organizer edits a venue another organizer sells at | Venue writes limited to admins or sole users; seats under published events frozen | `web/organizers/views.py` (`check_venue_write`) |
| Password guessing | 5 failures per 15 minutes per account and per address-and-account pair; DRF throttles on login, signup and reset | `web/accounts/throttling.py` |
| Weak passwords | Django validators: minimum length 10, common-password list, not all digits, not similar to the email | `web/config/settings.py` |
| Deploying with default secrets | Startup fails outside debug if a secret is a development value or under 32 characters | `web/config/settings.py` |
| Free tickets in production | Startup fails outside debug if payments would fall back to the fake gateway | `web/config/settings.py` |
| Clickjacking, MIME sniffing, downgrade | `X-Frame-Options: DENY`, `nosniff`, HSTS, secure and `HttpOnly` session cookies, `SameSite=Lax` | `deploy/nginx/nginx.conf`, settings |
| Tokens in logs | WebSocket handshakes, which carry the token in the query string, are logged by path only | `deploy/nginx/nginx.conf` |
| Injected scripts | A Content Security Policy on every page: scripts, styles and fonts from this origin only (plus Stripe.js), no inline code, no framing | `web/config/csp.py` |

## Known limitations

These are real trade-offs in the current design. Pull requests that improve them are welcome.

- **Account lockout can be triggered by anyone who knows an email address.** The per-account counter is what stops attackers from spreading guesses across many IP addresses, and it also means five wrong passwords lock the real owner out for up to 15 minutes. Resetting the password through the emailed link ends the lockout at once, since following the link proves who owns the account.
- **Access tokens in WebSocket URLs.** Browsers cannot send headers on a WebSocket handshake. The token lasts 15 minutes and is kept out of access logs, but it is still visible to anything that can read the browser's memory or devtools.
- **The Content Security Policy does not cover the Django admin or the Swagger UI at `/org/api/docs/`.** Neither is our markup: Swagger loads from a CDN and uses an inline script. Both require a signed-in account with the right role.
- **Locally, the bundled MySQL bootstrap gives the application user `CREATE`, `DROP` and `ALTER` on all databases** so the test suite can create its own. The production overlay sets `MYSQL_TEST_GRANTS=0`, which limits the user to `core` and `checkout`. Like all MySQL bootstrap settings, it only applies when the data directory is first created.
- **Rate limits per IP** treat everyone behind one carrier NAT or office proxy as one client.

## Hardening checklist for a deployment

1. New `DJANGO_SECRET_KEY`, `JWT_SECRET` and `QR_SECRET`, each 32 or more random characters.
2. Real Stripe keys and webhook secret, with the webhook endpoint registered in Stripe.
3. `DJANGO_ALLOWED_HOSTS`, `SITE_ORIGIN` and `CSRF_TRUSTED_ORIGINS` set to your domain only.
4. Start with `docker-compose.prod.yml`, which turns debug off everywhere and stops publishing database, cache, search and mail ports. Confirm with `docker compose ... config` that only nginx has `ports`.
5. A real certificate mounted into nginx (see [docs/OPERATIONS.md](docs/OPERATIONS.md#tls-certificates)).
6. New `MYSQL_PASSWORD`, `MYSQL_ROOT_PASSWORD` and `MEILI_MASTER_KEY` before the first start.
7. Change `ADMIN_URL` from `admin/` to something not guessable, and give admin accounts long unique passwords.
8. Keep dependencies patched. CI runs `pip-audit` on every push; rebuild images regularly so the base images get security updates.
9. Do not rotate `QR_SECRET` while tickets for upcoming events are in circulation; every issued QR code would stop scanning.
