# Operations

How to deploy, run, observe and repair TicketForge. Commands assume the Docker Compose setup in this repository; adapt them if you run the images on another platform.

## Contents

- [Everyday commands](#everyday-commands)
- [Deploying](#deploying)
- [TLS certificates](#tls-certificates)
- [Migrations](#migrations)
- [Backups and restore](#backups-and-restore)
- [Health checks and logs](#health-checks-and-logs)
- [Load testing](#load-testing)
- [Incident checklist](#incident-checklist)
- [Rotating secrets](#rotating-secrets)

## Everyday commands

| Task | Command |
|---|---|
| Build and start everything | `make up` (`docker compose up -d --build`) |
| See what is running | `docker compose ps` |
| Follow application logs | `make logs` |
| Stop, keeping data | `make down` |
| Stop and delete all data | `make reset` (deletes the MySQL, Redis and Meilisearch volumes) |
| Load demo data | `make seed` |
| Rebuild the search index | `make reindex` |
| Django shell | `make shell` |
| Read outgoing email | http://localhost:8025 |

Code is copied into the image at build time, not mounted. After changing Python, templates or static files, run `docker compose up -d --build` again.

## Deploying

### Before the first deploy

1. **Secrets.** Generate `DJANGO_SECRET_KEY`, `JWT_SECRET` and `QR_SECRET`, each at least 32 characters:

   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(48))"
   ```

   Outside debug mode Django refuses to start if any of them is a value from `.env.example` or shorter than 32 characters. That covers `migrate`, `web`, `realtime`, `worker` and `beat`; the API waits for `migrate`, so it never starts either.

2. **Payments.** Set `STRIPE_SECRET_KEY`, `STRIPE_PUBLISHABLE_KEY` and `STRIPE_WEBHOOK_SECRET`, and point a Stripe webhook endpoint at `https://<your domain>/api/v1/webhooks/stripe` for `payment_intent.succeeded`, `payment_intent.payment_failed`, `charge.refunded` and `refund.updated`. Without real keys, Django refuses to start outside debug mode, because the fake gateway would make every ticket free. A public demo where that is the intent must say so with `STRIPE_GATEWAY=fake`.

3. **Hosts and email.** Set `DJANGO_ALLOWED_HOSTS`, `SITE_ORIGIN`, `CSRF_TRUSTED_ORIGINS`, and `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_USE_TLS` for a real SMTP relay.

4. **Database credentials.** Change `MYSQL_PASSWORD` and `MYSQL_ROOT_PASSWORD`. The MySQL container only reads them when its data directory is empty, so set them before the first start.

5. **Certificate.** See [TLS certificates](#tls-certificates).

### Starting

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

The production overlay:

- forces `DJANGO_DEBUG=0` for every Django process, so the secret checks above cannot be skipped by a forgotten line in `.env`;
- removes the published ports of MySQL, Redis, Meilisearch and Mailpit, leaving only nginx on 80 and 443 (this uses the `!reset` tag, which needs Docker Compose 2.24 or newer);
- sets `MYSQL_TEST_GRANTS=0`, so the database user created on first start has rights on `core` and `checkout` and nothing else;
- sets `restart: always`, memory limits for `web` and `api`, and larger worker counts.

Check the result with `docker compose -f docker-compose.yml -f docker-compose.prod.yml config` before the first start. Only `nginx` should list `ports`.

### Order of a release

1. Build the new image.
2. Run `migrate`. It applies Django migrations to `core`, then Alembic migrations to `checkout`, and exits.
3. Replace `web`, `api`, `realtime`, `worker` and `beat` with the new image.

Compose does this for you: every application service has `depends_on: migrate: condition: service_completed_successfully`.

Because old code keeps running while migrations apply, every migration must work with both the old and the new code. Split breaking changes into expand, migrate, contract steps, as Danilo Sato describes in [ParallelChange](https://martinfowler.com/bliki/ParallelChange.html). Renaming a column, for example, is four releases: add the new column; write both and backfill; read the new one; drop the old one.

### Sizing

The defaults are 3 Gunicorn workers, 4 Uvicorn workers and 4 Celery processes; the overlay raises them to 5, 4 and 8. Each API worker holds its own catalogue cache and its own connection pools. Run exactly one `beat`, because two would queue every scheduled task twice. Any number of `worker` containers is fine.

## TLS certificates

The nginx image generates a self-signed certificate for `localhost` at build time, at `/etc/nginx/certs/ticketforge.crt` and `/etc/nginx/certs/ticketforge.key`. For a real domain, mount your certificate over those paths, for example in a `docker-compose.override.yml`:

```yaml
services:
  nginx:
    volumes:
      - /etc/letsencrypt/live/tickets.example.com/fullchain.pem:/etc/nginx/certs/ticketforge.crt:ro
      - /etc/letsencrypt/live/tickets.example.com/privkey.pem:/etc/nginx/certs/ticketforge.key:ro
```

Then add that file to the `-f` list and run `docker compose exec nginx nginx -s reload` after each renewal.

## Migrations

```bash
# Django, core schema
docker compose exec web python manage.py migrate
docker compose exec web python manage.py makemigrations --check --dry-run   # fails if a model change has no migration

# Alembic, checkout schema
docker compose exec api alembic -c api/alembic.ini upgrade head
docker compose exec api alembic -c api/alembic.ini check                    # fails if a model change has no revision
docker compose exec api alembic -c api/alembic.ini revision --autogenerate -m "describe the change"
```

CI runs both checks, so a model change without its migration cannot be merged. Never write Django migrations that touch `checkout`, or Alembic revisions that touch `core`.

## Backups and restore

Back up both schemas in one consistent snapshot:

```bash
docker compose exec -T mysql sh -c 'exec mysqldump -uroot -p"$MYSQL_ROOT_PASSWORD" \
  --single-transaction --routines --databases core checkout' > ticketforge-$(date +%F).sql
```

`--single-transaction` takes the dump inside one InnoDB snapshot, so orders and tickets match even while sales continue.

A backup is not a backup until it has been restored. Restore into a scratch container and check it:

```bash
docker run -d --name restore-check -e MYSQL_ROOT_PASSWORD=check mysql:8.4
sleep 30
docker exec -i restore-check mysql -uroot -pcheck < ticketforge-YYYY-MM-DD.sql
docker exec restore-check mysql -uroot -pcheck -e \
  "SELECT COUNT(*) FROM checkout.orders WHERE status = 'paid'; SELECT COUNT(*) FROM checkout.ticket;"
docker rm -f restore-check
```

Compare the counts with production. Redis holds only rate-limit counters, the Celery queue and the channel layer, so losing it loses queued emails but no sales. Meilisearch can always be rebuilt with `make reindex`.

## Health checks and logs

| Endpoint | Checks | Use it for |
|---|---|---|
| `GET /api/v1/health` | the API process answers | liveness |
| `GET /api/v1/ready` | the API can reach MySQL and Redis | readiness, load balancer checks |
| `GET /health/` | the Django process answers | liveness |

`docker compose ps` shows the container health checks for MySQL, Redis and Meilisearch.

Everything logs to stdout. nginx writes one JSON object per request. The Django and FastAPI application loggers write JSON too, with a `request_id` field; the access lines from Gunicorn, Uvicorn and Daphne are their usual plain text.

nginx gives every request an id, passes it to the application as `X-Request-ID`, writes it in its own log line, and the application returns it in the `X-Request-ID` response header. When a user reports an error, ask for that header (or find the request in the nginx log) and search for the id to see what the application logged for that request. Celery tasks do not carry the id; they log the order, hold or event id they were given instead.

nginx logs only the path for WebSocket connections, because their query string carries an access token.

There is no error tracking or metrics integration built in. Ship the JSON logs to whatever you already use.

## Load testing

```bash
make loadtest-on         # swap in the load-test edge limit
make loadtest-headless   # 500 users for 90 seconds, prints a summary
make loadtest-off        # restore the production edge limit
```

or `make loadtest` for the Locust web UI at http://localhost:8089.

**Why the first step exists.** nginx limits each client IP to 20 requests per second with a burst of 40 (`deploy/nginx/limits.conf`). A load generator on one machine sends everything from one IP, so without the overlay almost every request is rejected with `503`, the default status for `limit_req` rejections according to the [nginx documentation](https://nginx.org/en/docs/http/ngx_http_limit_req_module.html). That would measure nginx, not TicketForge. `make loadtest-on` mounts `deploy/nginx/limits.loadtest.conf`, which raises the limit to 100000 requests per second. Never deploy that file. `make loadtest-off` recreates nginx from the image, which contains the production limit.

**What the profile does.** Anonymous browsing only: event list, event page, seat map and search, weighted 5:3:2:1, with 0.5 to 2 seconds between requests per user. Checkout is deliberately left out; see the docstring in `locustfile.py`.

**Generating more load.** One Locust process uses one CPU core. For more than a few hundred users, add `--processes 4` (or `-1` for one per core), as the [Locust documentation](https://docs.locust.io/en/stable/running-distributed.html) recommends. When Locust runs on the same machine as the stack, they compete for CPU, so treat the numbers as a lower bound.

**Reading the result.** The numbers measured on an M2 MacBook Air are in the [README](../README.md#load-test-results). If you see failures, check them in this order: `503` means the edge limit is still on; `429` means the per-user API limits are being hit; `5xx` from the applications means a real problem, so check `make logs`.

## Incident checklist

1. `docker compose ps` and `docker compose logs --since 15m <service>` for the service that is failing.
2. `GET /api/v1/ready` tells you whether the API or one of its dependencies is down.
3. Payments not completing: in the Stripe dashboard, open the webhook endpoint and look at its recent delivery attempts. A signature error usually means `STRIPE_WEBHOOK_SECRET` does not match that endpoint's signing secret.
4. `SELECT * FROM checkout.stripe_event WHERE processed_at IS NULL` should be empty or very short. A row that stays means its processing keeps failing; the log line for its id will say why. Stripe keeps retrying for up to three days in live mode.
5. Orders stuck in `pending` with an expired hold are safe. The sweeper has released the seats. If the payment does arrive, the webhook takes the seats again or refunds automatically.
6. Emails not arriving: check the `worker` logs and Mailpit (local) or your SMTP provider. Tasks retry with exponential backoff before giving up.
7. A report of a seat sold twice: it should be impossible, so treat it as a serious bug. Collect evidence before changing anything:

   ```sql
   SELECT event_seat_id, COUNT(*) FROM checkout.ticket
   WHERE event_seat_id IS NOT NULL AND status IN ('valid', 'checked_in')
   GROUP BY event_seat_id HAVING COUNT(*) > 1;

   SELECT * FROM checkout.audit_log WHERE entity_id IN ('<order ids>') ORDER BY created_at;
   ```

8. The nightly reconciliation writes `reconciliation_mismatch` rows to `checkout.audit_log` for paid orders without tickets and for Stripe payments with no paid order. Look at them each morning.

## Rotating secrets

Change the value in `.env`, then `docker compose up -d --force-recreate`.

| Secret | Effect of rotating it |
|---|---|
| `DJANGO_SECRET_KEY` | Every session cookie and password reset link becomes invalid. Users log in again. |
| `JWT_SECRET` | Every access and refresh token becomes invalid. Users log in again, and open WebSockets drop on their next reconnect. Update every service at once, since all of them check tokens. |
| `QR_SECRET` | **Every QR code already issued stops scanning.** Do not rotate it while tickets for upcoming events are out, unless you re-issue and re-send them. |
| `STRIPE_WEBHOOK_SECRET` | Roll it in the Stripe dashboard first, then deploy the new value; webhooks fail signature checks in between and Stripe retries them. |
| `MEILI_MASTER_KEY` | Restart `search` and every service that talks to it together, then run `make reindex`. |
