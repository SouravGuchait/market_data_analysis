# Market Data Analytics

Minimal Django backend foundation for a multi-user market data analytics
platform.

This repository implements **Project Foundation**, **Custom User Authentication**, **Database Schema and Model Relationships**, **User Portfolio and Configuration**, the **User Audit Trail**, **Scheduled File Discovery and Ingestion**, and the **tick parser layer**. Worker persistence, tick-to-candle aggregation, and analytics calculations remain future features.

## Implemented features

### Feature 001: Project Foundation

- Django 5.2 and Django REST Framework.
- MySQL 8 as the persistent database, configured through environment variables.
- Redis cache and Celery broker/result backend.
- Celery worker and Celery Beat configuration.
- Modular Django apps for the foundation and the planned domain areas.
- Custom user model from the first migration.
- Environment-specific settings: `development`, `production`, `test`.
- OpenAPI schema and Swagger/ReDoc documentation.
- Central versioned API routing through `config/api_urls.py` and the
  `API_PREFIX` setting (currently `/api/v1`).
- Console and rotating-file structured logging with request correlation IDs.
- Liveness and readiness health endpoints.
- Exchange master-data model.
- Docker Compose for local development with MySQL, Redis, web, worker and beat.
- Unit and integration test structure with pytest and pytest-django.

### Custom User Authentication

Authentication routes are mounted under `/api/v1/auth/`.

| Method | Endpoint | View | Purpose |
| --- | --- | --- | --- |
| POST | `/api/v1/auth/register/` | `RegisterView.post` | Create an account from username, email, password, and password confirmation. |
| POST | `/api/v1/auth/login/` | `LoginView.post` | Authenticate using a username or email and password; return access and refresh JWTs. |
| POST | `/api/v1/auth/token/refresh/` | `ThrottledTokenRefreshView` | Refresh an access token using a refresh token. |
| POST | `/api/v1/auth/logout/` | `LogoutView.post` | Blacklist the submitted refresh token for the authenticated user. |
| GET | `/api/v1/auth/me/` | `CurrentUserView.get` | Return the authenticated user's profile. |
| POST | `/api/v1/auth/password-reset/` | `PasswordResetRequestView.post` | Request a reset link by email; return a generic response. |
| POST | `/api/v1/auth/password-reset/confirm/` | `PasswordResetConfirmView.post` | Set a new password using a valid, unused reset token. |

#### Authentication functions

- **Registration:** validates username and email uniqueness, confirms the password, applies Django password validators, hashes the password, and returns safe account fields.
- **Login:** resolves username or email case-insensitively, checks the password and account status, and returns a generic authentication error on failure.
- **Token refresh:** uses SimpleJWT refresh-token validation and the `anon_token_refresh` throttle scope.
- **Logout:** blacklists the submitted refresh token. Requires authentication.
- **Current user:** requires authentication and uses the `user_profile` throttle scope.
- **Password-reset request:** generates a random token, stores its SHA-256 hash, and emails the reset link using the configured frontend URL. The response does not reveal whether the email exists.
- **Password-reset confirmation:** checks token validity and password confirmation, validates the new password, stores its hash, marks the reset token used, and blacklists the user's outstanding JWTs.

Registration, login, refresh, password-reset request, and confirmation use scoped throttles. JWT refresh rotation and blacklist behavior are configured in SimpleJWT.

### Database Schema and Model Relationships

- Shared exchange and instrument records, with per-exchange symbol uniqueness.
- Daily OHLCV records with daily uniqueness and OHLC database constraints.
- User-owned portfolios and an explicit portfolio-instrument association.
- A mapping service for validating and persisting generator rows.
- Idempotent reference-catalog seeding for exchange, symbol, tick-column, asset-class and currency values.
- MySQL bootstrap schema in `schema.sql` and Django migrations.

### Feature 003: User Portfolio and Configuration

Portfolio routes are mounted under `/api/v1/portfolios/` and require JWT
authentication.

| Method | Endpoint | Purpose |
| --- | --- | --- |
| GET | `/api/v1/portfolios/` | List the authenticated user's portfolios. |
| POST | `/api/v1/portfolios/` | Create a portfolio for the authenticated user. |
| GET | `/api/v1/portfolios/{portfolio_id}/` | Retrieve one owned portfolio. |
| PATCH | `/api/v1/portfolios/{portfolio_id}/` | Update name, description or status. |
| DELETE | `/api/v1/portfolios/{portfolio_id}/` | Delete an owned portfolio. |
| GET | `/api/v1/portfolios/{portfolio_id}/instruments/` | List portfolio instrument membership. |
| POST | `/api/v1/portfolios/{portfolio_id}/instruments/` | Add an instrument to a portfolio. |
| DELETE | `/api/v1/portfolios/{portfolio_id}/instruments/{id}/` | Remove an instrument membership. |

Ownership is always derived from the authenticated request; the owner cannot be
set or changed through request data. Every query is scoped to the authenticated
user, and a portfolio belonging to another user returns the same `404` response
as a missing portfolio.

Portfolio instrument writes accept either `instrument_id` or
`instrument_symbol`, and optionally either `exchange_id` or `exchange_code`,
plus optional `alias` and `is_active`. Identifier symbols and exchange codes are
matched case-insensitively. When only `instrument_symbol` is supplied, the
symbol must identify exactly one active instrument; if it is listed on multiple
exchanges, an exchange identifier is required. The exchange defaults to the
instrument's exchange and must match it when supplied. Sending both forms of the
same relationship is rejected. Read responses return nested instrument and
exchange objects.

Example request:

```json
{
  "instrument_symbol": "ACME",
  "exchange_code": "XNYS",
  "alias": "Core holding",
  "is_active": true
}
```


`PortfolioInstrument.exchange` is stored explicitly for the portfolio
configuration, is backfilled from the instrument by migration `0003`, and is
validated in both the model and serializer. The existing unique constraint on
`portfolio + instrument` prevents duplicate membership.

### Feature 004: User Audit Trail

Audit routes are mounted under `/api/v1/audit/` and require JWT authentication.

| Method | Endpoint | Purpose |
| --- | --- | --- |
| GET | `/api/v1/audit/` | List audit records visible to the authenticated user. |
| GET | `/api/v1/audit/{audit_id}/` | Retrieve one visible audit record. |

Ordinary users see only records attributed to their own account. Staff users
are the restricted administrative audience and may inspect all records,
including system events with no user actor. The collection endpoint supports
`action`, `resource_type`, `status`, `start_date` and `end_date` query filters.
Date filters accept an ISO 8601 date or datetime; date-only end values cover the
whole day. Collections use the standard pagination envelope with 20 results per
page and a maximum page size of 100.

Audit records include a UUID, nullable actor, action, resource type and
identifier, status, timestamp, request correlation ID, source IP and sanitized
metadata. The service redacts keys containing password, secret, token,
authorization, cookie or API-key values. Source IP comes from the socket peer
address; forwarding headers are not trusted.

Records are created for registration, login success and failure, logout,
password-reset request and completion, portfolio creation, update and deletion,
and portfolio instrument addition and removal. Ingestion and analytics actions
have constants in the audit model, and `apps.audit.services.record_system_event`
records an unattributed system event for future asynchronous workflows.

The audit model is append-only: application code cannot update or delete a
stored record, and no write API is exposed. Deleting an actor user preserves the
audit row and sets the actor to null.

Ingestion workers are future work.

### Feature 005: Scheduled File Discovery and Ingestion

Celery Beat calls `apps.ingestion.tasks.scan_enabled_ingestion_rules` every
`INGESTION_SCAN_INTERVAL_SECONDS` (default `60`). Each enabled rule is scanned
only when its own `scan_interval_seconds` has elapsed, so the global Beat cadence
is the lower bound.

Rules are administrator-controlled (Django admin or ORM). No API accepts a
filesystem path. A rule stores its storage backend, relative location under
`INGESTION_LOCAL_ROOT`, glob pattern, supported formats, scan interval, stability
policy, schema/instrument/exchange mappings, and scan watermark/status. Local and
EFS rules use the local filesystem adapter; the S3 choice is reserved and fails
loudly until an adapter is added. New rules default to `file_pattern="*"` and
the supported formats `csv`, `json` and `xlsx`; a rule can narrow discovery by
changing either setting.

Discovery uses a storage-adapter contract (`list_objects`, `stat`, `open`,
content hash) so parsing and scheduler code do not depend on a single storage
implementation. The local adapter enforces that rule locations and object keys
stay inside the configured root.

For each object, the scan:

- Tracks size and modification time across scans and requires both to remain
  unchanged for `required_stable_scans` (default `2`). Files still being copied
  are not processed.
- Treats files at or before the rule's initial watermark (or previous successful
  scan watermark) as historical, so pre-existing files are not ingested merely
  because they are present.
- Calculates a SHA-256 content hash and deduplicates against stored ingestion
  records with a database unique constraint on rule, object key and content hash.
- Creates a UUID ingestion record in `QUEUED`, records `DISCOVERED` and `QUEUED`
  state transitions, and dispatches one Celery task after the record commits.
- Marks a broker dispatch failure as `FAILED`; a later scan re-queues that
  content instead of silently skipping it.

A scan failure is persisted on the rule and does not advance the watermark. An
unreadable object is recorded individually without stopping other objects, and a
failing rule does not cancel unrelated rules. Scans and queued files produce
system-attributed audit events (`ingestion_started`, `ingestion_completed`,
`ingestion_failed`). Rule changes made in Django admin are attributed to the
authenticated administrator.

The per-file Celery task remains a worker boundary. Parsing and validation are
provided by the feature_006 parser layer below; database persistence and
tick-to-candle aggregation belong to the later ingestion-worker feature.

### Feature 006: Tick file parsing and validation

`apps.ingestion.parsers.iter_file_records` selects a parser by file extension
and yields either a normalized `TickRecord` or a traceable `RowRejection`.
`iter_file_batches` groups these results into bounded batches for a worker.
CSV uses pandas `read_csv` with `chunksize`; JSON supports explicitly configured
root arrays (streamed with ijson) and JSON Lines; XLSX reads rows with
openpyxl's read-only mode. The ijson dependency streams JSON array entries
without loading the full document. No parser loads the entire input into a DataFrame.

Every ingestion rule's `schema_mapping` must contain a `columns` object mapping
canonical names to source headings. The required canonical fields are
`instrument_identifier`, `exchange_identifier`, `event_timestamp`, `price`,
and `quantity`; optional fields are `exchange_trade_id`, `sequence_number`, and
`side`. Format-specific settings are nested under `csv`, `json`, or `xlsx`.
JSON requires `json.mode` (`array` or `jsonl`); XLSX requires `xlsx.worksheet`.
Naive timestamps require `source_timezone`; ambiguous daylight-saving times
also require `ambiguous_time_fold`. Numeric bounds and decimal precision can be
configured in `schema_mapping`. Fallback deduplication is performed only when
`deduplication_fields` is explicitly configured; no trade IDs are invented. A
bounded rolling identity cache detects nearby duplicates; database uniqueness
across the full ingestion lifecycle belongs to the worker persistence feature.
The cache defaults to 10,000 identities and can be changed with
`deduplication_cache_size` (maximum 100,000).

Parsers are library-level services and do not write ticks or alter the Celery
task lifecycle. Feature 007 will connect parsing outcomes to persisted tick
records and ingestion state transitions.

## Project layout

```text
.
├── apps/
│   ├── accounts/          # Custom user model
│   ├── analytics/         # Reserved for a later analytics feature
│   ├── audit/             # Append-only user and system audit records
│   ├── common/            # Health, logging and middleware
│   ├── exchanges/         # Exchange master data
│   ├── ingestion/         # Ingestion rules, storage discovery and lifecycle records
│   ├── instruments/       # Instrument master data
│   ├── market_data/       # Daily OHLCV and generator catalogs
│   └── portfolios/        # User-owned portfolios and instrument configuration
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

The following setup uses WSL Bash and keeps the environment file outside the
repository. Run commands from the repository root.

1. Set the env file location and create its parent directory:

   ```bash
   env_dir="$HOME/.config/env_vars"
   env_file="$env_dir/your-desired-name.env"
   mkdir -p "$env_dir"
   ```

2. For a new setup, copy the sample file. If you already have a project `.env`,
   move it instead. Choose one command:

   ```bash
   cp .env.example "$env_file"
   # Or, if an existing .env contains your local configuration:
   # mv .env "$env_file"
   ```

   Restrict access and edit the file. Generate fresh local secrets before use
   if the sample values are present. Set `TIME_ZONE=Asia/Kolkata` for GMT+05:30.

   ```bash
   chmod 600 "$env_file"
   nano "$env_file"
   ```

3. Install the global WSL Bash shortcuts. The script detects the repository
   location from its own path and saves the selected env file location:

   ```bash
   bash scripts/install_dev_commands.sh "$env_file"
   source "$HOME/.bashrc"
   ```

4. Build and start the services:

   ```bash
   dev_up --build
   ```

   Compose waits for MySQL to become healthy, runs `python manage.py migrate`
   in the one-shot `migrate` service, then starts the web, worker and beat
   services after migrations succeed.

After setup, `dev_up`, `dev_down` and `dev_restart` work from any new WSL Bash
terminal. They accept optional service names, for example `dev_restart web`.
The commands use Docker Compose's `--env-file` option, since Compose does not
automatically read the `MDA_ENV_FILE` variable used by Django's local settings.

This starts:

- MySQL 8 on `MYSQL_HOST_PORT` (default `3307`; container port `3306`).
- Redis on `REDIS_HOST_PORT` (default `6379`).
- Django on `http://localhost:8000`.
- Celery worker.
- Celery Beat.

Docker Compose reads variables from the configured env file and substitutes
`${VARIABLE}` expressions in `docker-compose.yml`. The Compose-specific values
set container hostnames, ports, paths and Redis URLs; the regular `DB_HOST`,
`DB_PORT`, `CACHE_URL` and Celery URLs remain for commands run directly on your
host. MySQL uses the non-root `DB_USER` account for Django, with
`DB_ROOT_PASSWORD` reserved for the database administrator.

Compose requires the database credentials, Django secret and allowed hosts,
`MYSQL_HOST_PORT`, `REDIS_HOST_PORT`, `REDIS_IMAGE`, `WEB_HOST_PORT`, `COMPOSE_DB_HOST`,
`COMPOSE_DB_PORT`, the three `COMPOSE_*` Redis URLs, and the Compose ingestion
path. The web, worker and beat containers write logs to `/var/log/mda/app.log`
in the persistent `mda_logs` Docker volume. View the web container log with
`docker compose exec web tail -f /var/log/mda/app.log`. It stops with a clear
error if a required value is missing.

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
| `LOG_FILE_PATH` | Rotating log file path | `/var/log/mda/app.log` |
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

Optional ingestion settings:

- `INGESTION_SCAN_INTERVAL_SECONDS` (default `60`) — Celery Beat discovery cadence.
- `INGESTION_HASH_CHUNK_SIZE_BYTES` (default `1048576`) — content-hash read size.

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
