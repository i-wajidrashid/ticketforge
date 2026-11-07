# Contributing

## Setup

```bash
cp .env.example .env
make setup        # uv venv + dev dependencies
docker compose up -d mysql redis search mailpit
make test
```

## Before you push

```bash
make check        # ruff + mypy + pytest with the 85% coverage gate
pre-commit install
```

## Rules of the road

- **Never** write to a database schema your service does not own. `core` is Django's, `checkout`
  is FastAPI's.
- Money is integer cents. Timestamps are UTC.
- Every new endpoint needs a test; every bug fix needs a regression test.
- Race conditions get a concurrency test, not a comment.
- Emails/PDFs/search indexing go through Celery, not request handlers.
- Keep views thin: business logic belongs in `services/`.
- Migrations ship with the code that needs them, and must be backward compatible.

## Commit style

Conventional commits (`feat:`, `fix:`, `docs:`, `test:`, `chore:`), one logical change per commit.
