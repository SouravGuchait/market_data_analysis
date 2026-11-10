# Market Data Analytics

Minimal Django backend foundation for a multi-user market data analytics
platform.

This repository currently implements **Feature 001: Project Foundation** only.
Authentication, ingestion workflows, analytics calculations, portfolios and
audit APIs are intentionally out of scope and will be added in later features.

## Implemented in Feature 001

- Django 5.2 and Django REST Framework.
- MySQL 8 as the persistent database, configured through environment variables.
- Redis cache and Celery broker/result backend.
- Celery worker and Celery Beat configuration.
- Modular Django apps for the foundation and the planned domain areas.
- Custom user model from the first migration.
- Environment-specific settings: `development`, `production`, `test`.
- OpenAPI schema and Swagger/ReDoc documentation.
- Central versioned API routing through `config/api_urls.py` and the
  `API_PREFIX` setting (currently `api/v1`).
- Console and rotating-file structured logging with request correlation IDs.
- Liveness and readiness health endpoints.
- Exchange and instrument master-data models, with database constraints.
- Docker Compose for local development with MySQL, Redis, web, worker and beat.
- Unit and integration test structure with pytest and pytest-django.

## Project layout

```text
.
├── apps/
│   ├── accounts/          # Custom user model
│   ├── analytics/         # Reserved for a later analytics feature
│   ├── audit/             # Reserved for a later audit feature
│   ├── common/            # Health, logging and middleware
│   ├── exchanges/         # Exchange master data
│   ├── ingestion/         # Reserved for a later ingestion feature
│   ├── instruments/       # Instrument master data
│   ├── market_data/       # Reserved for later market-data features
│   └── portfolios/        # Reserved for a later portfolio feature
├── config/
│   ├── settings/          # base, development, production, test
│   ├── celery.py
│   ├── urls.py
│   ├── asgi.py
│   └── wsgi.py
├── tests/                 # Unit tests and integration tests
├── docker-compose.yml
├── Dockerfile
├── manage.py
├── pytest.ini
├── ruff.toml
└── pyrightconfig.json
```

## Requirements

- Python 3.12+
- MySQL 8+
- Redis 7+
- Docker and Docker Compose (recommended for local dependencies)

## Local setup without Docker

1. Create and activate a virtual environment:

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```

2. Install dependencies:

   ```powershell
   pip install -r requirements-dev.txt
   ```

3. Copy `.env.example` to `.env`. It contains generated local sample secrets;
   generate new secrets before sharing or deploying the application:

   ```powershell
   Copy-Item .env.example .env
   ```

4. Start MySQL and Redis. MySQL is exposed on host port `3307` by default and
   listens on container port `3306`. `DB_PORT` is for Django running directly
   on your computer; Compose overrides it to `3306` for containers.

   ```powershell
   docker compose up -d db redis
   ```

5. Apply migrations and start Django:

   ```powershell
   python manage.py migrate
   python manage.py runserver
   ```

6. Start Celery worker and beat in separate terminals:

   ```powershell
   celery -A config worker --loglevel=INFO
   celery -A config beat --loglevel=INFO
   ```

   On Windows, run the worker with `--pool=solo`.

## Docker Compose

```powershell
docker compose build
docker compose up -d db redis
docker compose run --rm web python manage.py migrate
docker compose up
```

This starts:

- MySQL 8 on `MYSQL_HOST_PORT` (default `3307`; container port `3306`).
- Redis on `REDIS_HOST_PORT` (default `6379`).
- Django on `http://localhost:8000`.
- Celery worker.
- Celery Beat.

Docker Compose reads variables from `.env` and substitutes `${VARIABLE}`
expressions in `docker-compose.yml`. The Compose-specific variables in `.env`
set container hostnames, ports, paths and Redis URLs; the regular `DB_HOST`,
`DB_PORT`, `CACHE_URL` and Celery URLs remain for commands run directly on your
host. MySQL uses the non-root `DB_USER` account for Django, with
`DB_ROOT_PASSWORD` reserved for the database administrator.

Compose requires the database credentials, Django secret and allowed hosts,
`MYSQL_HOST_PORT`, `REDIS_HOST_PORT`, `REDIS_IMAGE`, `WEB_HOST_PORT`, `COMPOSE_DB_HOST`,
`COMPOSE_DB_PORT`, the three `COMPOSE_*` Redis URLs, and the Compose log and
ingestion paths. It stops with a clear error if a required value is missing.

The first command builds the app image. The next two start the dependencies and
apply database migrations before the API, worker and beat services start.

## Health endpoints

### Liveness

`GET /api/v1/health/`

Returns HTTP 200 without touching MySQL or Redis. Use this for process-level
liveness probes.

```json
{
  "success": true,
  "message": "Service is alive",
  "data": {
    "status": "ok",
    "version": "0.1.0",
    "timestamp": "2026-11-09T12:00:00Z"
  },
  "errors": null
}
```

### Readiness

`GET /api/v1/health/ready/`

Checks MySQL and Redis. Returns HTTP 200 when both are available and HTTP 503
when either dependency is unavailable.

```json
{
  "success": true,
  "message": "Service is ready",
  "data": {
    "status": "ok",
    "version": "0.1.0",
    "timestamp": "2026-11-09T12:00:00Z",
    "checks": {
      "database": { "status": "ok" },
      "cache": { "status": "ok" }
    }
  },
  "errors": null
}
```

Dependency errors are logged with technical detail; API responses never expose
connection strings or stack traces.

## OpenAPI documentation

- Schema: `GET /api/schema/`
- Swagger UI: `GET /api/docs/`
- ReDoc: `GET /api/docs/redoc/`

Add new versioned API routes in `config/api_urls.py`. The root URL config mounts
that module under `API_PREFIX`, so route modules do not need to repeat
`/api/v1/` in each endpoint.

## Frontend security

Django CSRF middleware is enabled. `DJANGO_CSRF_TRUSTED_ORIGINS` is read from
the environment and is empty by default; add the frontend origin, including
its scheme, when cross-origin browser requests need to submit unsafe methods.
CSRF origin trust does not enable CORS.

CORS is not configured yet, so browsers cannot call this API from a different
origin. Streamlit server-side requests do not need browser CORS. For a browser
frontend, add a CORS middleware with an explicit origin allowlist. Keep that
allowlist separate from CSRF trusted origins; only enable credentialed CORS if
the frontend will use cross-origin cookies.

## Dependency verification

```powershell
python manage.py check_dependencies
```

Checks MySQL, the Redis cache and the Celery broker. It exits with a non-zero
status if any dependency is unavailable.

## Logging

Logs are emitted to the console and to a rotating file. The file path, level,
rotation size and backup count are environment-driven:

| Variable | Purpose | Default |
| --- | --- | --- |
| `LOG_LEVEL` | Root log level | `INFO` |
| `LOG_FILE_PATH` | Rotating log file path | `logs/app.log` |
| `LOG_MAX_BYTES` | Maximum size of one log file | `10485760` |
| `LOG_BACKUP_COUNT` | Number of rotated files retained | `5` |

Each log line is a JSON object containing the timestamp, level, logger,
message, request ID and any safe structured fields. `X-Request-ID` is accepted
when it matches `[A-Za-z0-9._-]{1,64}`; otherwise a UUID is generated. The
correlation ID is returned on the response.

Django, the Celery worker and Celery Beat share the same handlers, so worker
and beat output also appears in the rotating file. Celery's root-logger hijack
is disabled to keep the Django logging configuration authoritative.

## Environment variables

See `.env.example` for the complete list. Required in every non-test
environment:

- `DJANGO_SECRET_KEY`
- `DJANGO_ALLOWED_HOSTS`
- `DB_NAME`, non-root `DB_USER`, `DB_PASSWORD`, `DB_ROOT_PASSWORD`, `DB_HOST`, `DB_PORT`
- `CACHE_URL`
- `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND`
- `LOG_FILE_PATH`
- `INGESTION_LOCAL_ROOT`

Production additionally requires `DJANGO_SETTINGS_MODULE=config.settings.production`
and rejects the development secret key and an empty host list.

## Database notes

- All timestamps are timezone-aware and stored in UTC.
- Django's MySQL backend stores `DateTimeField` values with microsecond
  precision (`DATETIME(6)`).
- The default transaction isolation level is `READ COMMITTED`.
- Exchange codes are unique.
- Instrument symbols are unique within an exchange.

## Tests

Unit tests use in-memory SQLite and local-memory cache, so they need no
external services:

```powershell
python -m pytest -q
```

Integration tests use the real MySQL, Redis and Celery broker configured in
`config.settings.development`:

```powershell
$env:RUN_INTEGRATION="1"
python -m pytest -m integration --ds=config.settings.development
```

Static checks:

```powershell
python -m ruff check .
python -m ruff format --check .
basedpyright
python manage.py check
```

## Security notes

- Secrets are read from environment variables; `.env` is git-ignored.
- The production settings require a non-development secret key and explicit
  allowed hosts.
- Health responses never include credentials, internal paths or exception
  details.
- Request bodies and authorization headers are not logged. Structured log
  fields with names containing password, secret, token, authorization, cookie
  or API key are redacted.
- The ingestion root is configured server-side; no API accepts arbitrary paths.
- The readiness endpoint is intentionally public so orchestrators can probe it;
  business APIs use DRF authentication and permission defaults.

## Out of scope for Feature 001

- Registration, login, JWT issuance and password reset.
- File discovery, parsing, tick ingestion and candle aggregation workflows.
- Portfolio, analytics and audit APIs.
- Streamlit frontend.
- AWS ECR/ECS deployment.
- The separate AI/FastAPI service described in later roadmap features.

## Feature 001 implementation record

The foundation currently contains the custom user model, exchange and
instrument master data, environment-based Django settings, the health and
OpenAPI endpoints, request correlation and redacted structured logging, and
Docker/Celery runtime configuration. Unused tick, candle and ingestion models,
unused database indexes, and dependencies used only by deferred features have
been removed. Required future app packages remain as minimal placeholders; add
models, services and dependencies alongside the feature that first uses them.
