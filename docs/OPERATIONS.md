# Operations runbook

## Start / stop

```bash
docker compose up -d --build
docker compose ps
docker compose logs -f api web worker
docker compose down            # keep volumes
docker compose down -v         # destructive: drops MySQL/Redis/Meili data
```

## Migrations

```bash
# Django (core)
docker compose exec web python manage.py migrate
docker compose exec web python manage.py makemigrations --check --dry-run

# Alembic (checkout)
docker compose exec api alembic -c api/alembic.ini upgrade head
docker compose exec api alembic -c api/alembic.ini check
docker compose exec api alembic -c api/alembic.ini revision --autogenerate -m "change"
```

Deploy order: run migrations first (the `migrate` one-shot service does this), then roll new
code. Changes must be backward compatible (expand → backfill → switch → contract).

## Backups

```bash
docker compose exec mysql mysqldump -uroot -p"$MYSQL_ROOT_PASSWORD" \
  --single-transaction --databases core checkout > backup.sql
# restore into a scratch container and run the test suite against it before trusting it
```

## Health

- `GET /api/v1/health` — liveness
- `GET /api/v1/ready` — MySQL + Redis
- `GET /health/` — web liveness
- `docker compose ps` shows container healthchecks

## Common tasks

| Task | Command |
|---|---|
| Reindex search | `docker compose exec web python manage.py reindex_events` |
| Seed demo data | `docker compose exec web python manage.py seed_demo_data --orders 400` |
| Open a shell | `docker compose exec web python manage.py shell` |
| Inspect queued tasks | `docker compose exec redis redis-cli -n 1 keys '*'` |
| Read emails | <http://localhost:8025> |
| Run tests (host) | `uv run pytest` |
| Run tests (container) | `docker compose run --rm web pytest` (needs test DBs) |

## Incident checklist

1. `docker compose ps` and `logs` for the affected service.
2. `GET /ready` to separate app failure from dependency failure.
3. Check Stripe dashboard (test mode) for webhook delivery attempts.
4. `stripe_event` rows with `processed_at IS NULL` indicate in-flight/retried events.
5. Orders stuck `pending` with an expired hold are safe: the sweeper releases inventory; a late
   success triggers an automatic refund.
6. For oversell reports, query for duplicate `qr_token` or seats with two tickets — both should
   be impossible; capture the `audit_log` around the order ids.

## Rotating secrets

Update `.env`, then recreate containers (`docker compose up -d --force-recreate`). Rotating
`JWT_SECRET` invalidates all tokens (users re-login). Rotating `QR_SECRET` invalidates existing
QR codes — re-render PDFs before rotating in production.
