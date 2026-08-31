# TaxDocs

## Run the stack

```bash
cp .env.example .env
docker compose up
```

This brings up `api` (http://localhost:8000), `frontend` (http://localhost:5173), `worker`, `postgres` (with `pgvector`), `redis`, and `minio`. The `api` service runs pending Alembic migrations on startup.

## Database migrations

Migrations live in `backend/migrations`. From `backend/`, with `DATABASE_URL` pointing at a running Postgres:

```bash
cd backend
uv sync
uv run alembic upgrade head      # apply migrations
uv run alembic downgrade -1      # roll back one revision
uv run alembic revision -m "..." # create a new migration
```

## Backend tests

Requires a Postgres instance reachable via `DATABASE_URL` (defaults to `postgresql+psycopg://taxdocs:taxdocs@localhost:5432/taxdocs_test`):

```bash
cd backend
uv run pytest
uv run mypy app
```

## Frontend tests

```bash
cd frontend
npm install
npm test
npm run typecheck
```
