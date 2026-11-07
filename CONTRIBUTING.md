# Contributing

Thanks for helping. This page covers setting up a development environment, the checks a change must pass, and the rules the codebase follows. If you are planning something large, open an issue first so we can agree on the approach before you spend time on it.

Security problems are reported privately; see [SECURITY.md](SECURITY.md).

## Development setup

You need Docker, [uv](https://docs.astral.sh/uv/) and Python 3.12.

```bash
cp .env.example .env
make setup                                   # .venv with the app and dev dependencies
docker compose up -d mysql redis search mailpit
make test
```

The tests run on the host against the MySQL and Redis containers. They create and drop their own `test_core` and `test_checkout` databases, so they never touch your development data.

To work on the running app instead, start everything with `make up` and load data with `make seed`. Code is baked into the images, so after changing Python, templates, CSS or JavaScript run `docker compose up -d --build` (or `docker compose up -d --build web` for one service).

Install the git hooks once:

```bash
uv run --with pre-commit pre-commit install
```

## Before opening a pull request

```bash
make check
```

That runs, in order:

1. `ruff check` and `ruff format --check`
2. `mypy`, which is strict for `api`, `common` and `worker`
3. the test suite with coverage, which fails below 85%

CI additionally checks that no Django migration or Alembic revision is missing, runs `pip-audit`, and builds the Docker image. A pull request needs all of it green.

## Rules the codebase follows

**Ownership**

- Django writes only to `core`. FastAPI writes only to `checkout`. Reading the other side is fine, through `api/app/core_repo.py` or a `readmodel.py`; writing to it is not.
- Business rules live in `api/app/services/` and `web/organizers/services.py`. Routers and views parse input, call a service and shape the response.

**Correctness**

- Money is an integer number of cents. Never a float, never a `Decimal` in the payment path.
- Timestamps are stored in UTC. Convert to `TIME_ZONE` only when showing them.
- Anything that changes stock or seat status is a conditional update whose affected-row count is checked. Do not read a status and then write it.
- A new race condition gets a concurrency test that reproduces it with threads, not a comment saying it is fine.
- Celery tasks take ids and must be safe to run twice.
- Emails, PDFs, refunds and search indexing go through Celery, and are queued only after the database change they depend on has committed: with `transaction.on_commit` in Django, after `session.commit()` in the API. A rolled-back request must send nothing.

**Tests**

- Every endpoint has a test. Every bug fix comes with a test that fails without the fix.
- Tests use the real database. If something is hard to test without mocks, that usually means the code needs a seam, such as the payment gateway protocol in `api/app/services/stripe_gateway.py`.

**Migrations**

- A migration ships in the same pull request as the code that needs it.
- Migrations must work with the previous release's code still running. Break a destructive change into steps (add, backfill, switch, remove) across releases. [docs/OPERATIONS.md](docs/OPERATIONS.md#order-of-a-release) explains why.

**Front end**

- Pages must work at 390 pixels wide without sideways scrolling of the page itself. Interactive targets stay at least 24 by 24 pixels ([WCAG 2.5.8](https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum.html)).
- Grid columns that hold wide content use `minmax(0, 1fr)`, not `1fr`.
- JavaScript is for interaction only. Every page must render and make sense before scripts run.
- Pages are served with a Content Security Policy that forbids inline code. No `<script>` blocks other than `json_script` data, no `onclick=` style handlers, no `style=""` attributes. Use a class, an SVG attribute, or set the style from a script file. `web/tests/test_csp.py` checks the templates.

**Writing**

- User-facing text is plain and specific: say what happened and what to do next.
- Documentation states only what the code does today. If you change behaviour, update the docs in the same pull request. Link primary sources (official documentation, specifications, papers) when you explain why something is done a certain way.

## Commits and pull requests

- One logical change per commit, with a message in the [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/) style: `feat:`, `fix:`, `docs:`, `test:`, `refactor:`, `chore:`.
- The pull request description says what changed, why, and how you tested it. Screenshots for anything visible, at desktop and phone widths.
- Add a line to the `Unreleased` section of [CHANGELOG.md](CHANGELOG.md) for anything a user or operator would notice.

## License

By contributing you agree that your contribution is licensed under the [MIT License](LICENSE), the same as the rest of the project.
