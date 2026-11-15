# Market Data & Trade Analytics

Minimal Django backend foundation for a multi-user market data analytics
platform.

This repository implements **Project Foundation**, **Custom User Authentication**, **Database Schema and Model Relationships**, **User Portfolio and Configuration**, the **User Audit Trail**, **Instrument Master Seeding**, **User file ingestion management**, **Universal CSV conversion / Pandas validation**, **Chunk-based Celery processing with batch persistence** (Feature 007), **Candle aggregation with daily returns** (Feature 008), **SMA, EMA, and MACD** (Feature 009), **Bollinger Bands and ATR** (Feature 010), **RSI and Stochastic** (Feature 011), and **maximum drawdown** (Feature 020). Volume, price-action, and statistical indicators remain future work (Features 012–014).

## Implemented features

### Feature 001: Project Foundation

- Django 5.2 and Django REST Framework.
- MySQL 8 as the persistent database, configured through environment variables.
- Redis is the API cache. RabbitMQ is the Celery broker, and task results use that same broker.
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
- Docker Compose for local development with MySQL, Redis, RabbitMQ, web and worker.
- Unit and integration test structure with pytest and pytest-django.

### Custom User Authentication

Authentication routes are mounted under `/api/v1/auth/`.

| Method | Endpoint                                 | View                              | Purpose                                                                              |
| ------ | ---------------------------------------- | --------------------------------- | ------------------------------------------------------------------------------------ |
| POST   | `/api/v1/auth/register/`               | `RegisterView.post`             | Create an account from username, email, password, and password confirmation.         |
| POST   | `/api/v1/auth/login/`                  | `LoginView.post`                | Authenticate using a username or email and password; return access and refresh JWTs. |
| POST   | `/api/v1/auth/token/refresh/`          | `ThrottledTokenRefreshView`     | Refresh an access token using a refresh token.                                       |
| POST   | `/api/v1/auth/logout/`                 | `LogoutView.post`               | Blacklist the submitted refresh token for the authenticated user.                    |
| GET    | `/api/v1/auth/me/`                     | `CurrentUserView.get`           | Return the authenticated user's profile.                                             |
| POST   | `/api/v1/auth/password-reset/`         | `PasswordResetRequestView.post` | Request a reset link by email; return a generic response.                            |
| POST   | `/api/v1/auth/password-reset/confirm/` | `PasswordResetConfirmView.post` | Set a new password using a valid, unused reset token.                                |

#### Authentication functions

- **Registration:** validates username and email uniqueness, confirms the password, applies Django password validators, hashes the password, and returns safe account fields.
- **Login:** resolves username or email case-insensitively, checks the password and account status, and returns a generic authentication error on failure.
- **Token refresh:** uses SimpleJWT refresh-token validation and the `anon_token_refresh` throttle scope.
- **Logout:** blacklists the submitted refresh token. Requires authentication.
- **Current user:** requires authentication and uses the `user_profile` throttle scope.
- **Password-reset request:** generates a random token, stores its SHA-256 hash, and emails the reset link using the configured frontend URL. The response does not reveal whether the email exists.
- **Password-reset confirmation:** checks token validity and password confirmation, validates the new password, stores its hash, marks the reset token used, and blacklists the user's outstanding JWTs.

Registration, login, refresh, password-reset request, and confirmation use scoped throttles. JWT refresh rotation and blacklist behavior are configured in SimpleJWT.

Authenticated calls send `Authorization: Bearer <access>`. `GET /api/v1/auth/me/` has no body.

```json
POST /api/v1/auth/register/
{
  "username": "analyst",
  "email": "analyst@example.com",
  "password": "Correct-horse-battery-staple-9",
  "password_confirm": "Correct-horse-battery-staple-9"
}
```

```json
POST /api/v1/auth/login/
{
  "username": "analyst",
  "password": "Correct-horse-battery-staple-9"
}
```

`username` accepts the account username or the email.

```json
POST /api/v1/auth/token/refresh/
{
  "refresh": "<refresh-token>"
}
```

```json
POST /api/v1/auth/logout/
{
  "refresh": "<refresh-token>"
}
```

```json
POST /api/v1/auth/password-reset/
{
  "email": "analyst@example.com"
}
```

```json
POST /api/v1/auth/password-reset/confirm/
{
  "token": "<reset-token-from-the-email-link>",
  "password": "Another-correct-horse-staple-9",
  "password_confirm": "Another-correct-horse-staple-9"
}
```

Confirmation uses `token`, `password`, and `password_confirm`. The new password must pass Django's password validators.

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

| Method | Endpoint                                                | Purpose                                        |
| ------ | ------------------------------------------------------- | ---------------------------------------------- |
| GET    | `/api/v1/portfolios/`                                 | List the authenticated user's portfolios.      |
| POST   | `/api/v1/portfolios/`                                 | Create a portfolio for the authenticated user. |
| GET    | `/api/v1/portfolios/{portfolio_id}/`                  | Retrieve one owned portfolio.                  |
| PATCH  | `/api/v1/portfolios/{portfolio_id}/`                  | Update name, description or status.            |
| DELETE | `/api/v1/portfolios/{portfolio_id}/`                  | Delete an owned portfolio.                     |
| GET    | `/api/v1/portfolios/{portfolio_id}/instruments/`      | List portfolio instrument membership.          |
| POST   | `/api/v1/portfolios/{portfolio_id}/instruments/bulk/` | Add an instrument to a portfolio.              |
| DELETE | `/api/v1/portfolios/{portfolio_id}/instruments/{id}/` | Remove an instrument membership.               |

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

Create and update a portfolio:

```json
POST /api/v1/portfolios/
{
  "name": "Core book",
  "description": "Primary holdings",
  "status": "active"
}
```

```json
PATCH /api/v1/portfolios/{portfolio_id}/
{
  "name": "Core book",
  "description": "Updated holdings",
  "status": "active"
}
```

`description` and `status` may be omitted. `DELETE` has no body.

Attach one instrument. `ACME` is listed on `XNAS` in the seeded catalog. Send either `instrument_id` or `instrument_symbol`, and either `exchange_id` or `exchange_code`:

```json
POST /api/v1/portfolios/{portfolio_id}/instruments/
{
  "instrument_symbol": "ACME",
  "exchange_code": "XNAS",
  "alias": "Core holding",
  "is_active": true
}
```

`DELETE /api/v1/portfolios/{portfolio_id}/instruments/{id}/` has no body.

`PortfolioInstrument.exchange` is stored explicitly for the portfolio
configuration, is backfilled from the instrument by migration `0003`, and is
validated in both the model and serializer. The existing unique constraint on
`portfolio + instrument` prevents duplicate membership.

### Feature 004: User Audit Trail

Audit routes are mounted under `/api/v1/audit/` and require JWT authentication.

| Method | Endpoint                      | Purpose                                               |
| ------ | ----------------------------- | ----------------------------------------------------- |
| GET    | `/api/v1/audit/`            | List audit records visible to the authenticated user. |
| GET    | `/api/v1/audit/{audit_id}/` | Retrieve one visible audit record.                    |

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
portfolio instrument addition and removal, ingestion lifecycle events, and
aggregation trigger / completion / failure events (Feature 008). Ordinary
analytics GET requests are not audited as exports.
`apps.audit.services.record_system_event` records unattributed system events for
ingestion-triggered aggregation and other worker workflows.

The audit model is append-only: application code cannot update or delete a
stored record, and no write API is exposed. Deleting an actor user preserves the
audit row and sets the actor to null.

### Feature 004.5: Instrument seeding and portfolio instrument management

Shared exchange and instrument master data can be loaded from the synthetic
generator and managed through authenticated APIs.

```bash
python manage.py seed_exchanges
python manage.py seed_instruments
python manage.py seed_market_data
```
### Feature 006: Universal CSV conversion and Pandas chunk processing

Every supported source (`csv`, `json`, `jsonl`, `xlsx`) is converted into bounded
CSV staging chunks before Pandas normalization and validation. Market-data
persistence, Celery claim, Trade rows and RejectedRow storage remain Feature 007.

Pipeline entry point:

```python
from apps.ingestion.pipeline import run_conversion_pipeline

result = run_conversion_pipeline(str(ingestion.id))
```

Staging layout:

```text
{INGESTION_SYSTEM_ROOT}/
  users/<owner_pk>/staging/<ingestion_uuid>/   # or system/staging/<ingestion_uuid>/
    chunk_000001.csv
    chunk_000002.csv
    manifest.json
```

#### Implemented files and functions

##### `apps/ingestion/converters/types.py`

| Symbol | Purpose |
| --- | --- |
| `SOURCE_ROW_COLUMN` | Canonical staged column `_source_row` for original row numbers. |
| `UnsupportedFileFormatError` | Unknown or unsupported extension. |
| `FormatMismatchError` | Extension and content sniff disagree. |
| `ConversionError` | File-level conversion failure. |
| `ConvertRequest` | Source path, staging dir, chunk size, parser config. |
| `ChunkMeta` | One staged chunk: number, filename, row count. |
| `ConversionRejection` | Recoverable conversion rejection (`CONVERSION` stage). |
| `ConversionManifest` / `as_dict()` | Manifest payload written beside staging chunks. |

##### `apps/ingestion/converters/detect.py`

| Function | Purpose |
| --- | --- |
| `normalize_extension` | Lowercase extension including the leading dot. |
| `detect_format` | Map extension to `CSV` / `JSON` / `JSONL` / `XLSX`. |
| `verify_format` | Sniff bytes/structure and reject mismatches. |
| `_looks_like_csv` / `_looks_like_json_array` / `_looks_like_jsonl` / `_looks_like_xlsx` | Content sniff helpers. |

##### `apps/ingestion/converters/chunk_writer.py`

| Function | Purpose |
| --- | --- |
| `chunk_filename` | `chunk_000001.csv` style names. |
| `chunk_path` | Staging path for one chunk. |
| `attach_source_rows` | Attach contiguous 1-based `_source_row` values. |
| `records_to_frame` | Build a DataFrame from record dicts with source rows. |
| `write_frame_chunk` | Write one CSV chunk and return `ChunkMeta`. |

##### `apps/ingestion/converters/csv_converter.py`

| Function | Purpose |
| --- | --- |
| `convert_csv` | Split CSV with `pd.read_csv(chunksize=...)` into staging chunks. |
| `_csv_reader` | Open a bounded Pandas CSV reader from parser config. |
| `_config_str` | Read encoding/delimiter from parser config. |

##### `apps/ingestion/converters/json_converter.py`

| Function | Purpose |
| --- | --- |
| `convert_json` | Stream a JSON array with `ijson` into CSV chunks. |
| `_iter_records` | Yield objects from the configured JSON records path. |
| `_flush_batch` | Write one bounded batch as a staging CSV. |
| `_record_path` | Resolve `json_records_path` (default `item`). |

##### `apps/ingestion/converters/jsonl_converter.py`

| Function | Purpose |
| --- | --- |
| `convert_jsonl` | Line-by-line JSONL conversion; malformed lines become conversion rejections. |
| `_parse_line` / `_decoded_record` / `_rejection` | Parse one line or record a conversion rejection. |
| `_iter_lines` | Stream `(line_number, line)` pairs. |

##### `apps/ingestion/converters/xlsx_converter.py`

| Function | Purpose |
| --- | --- |
| `convert_xlsx` | Stream worksheet rows with `openpyxl` read-only mode into CSV chunks. |
| `_iter_sheet_rows` | Yield `(excel_row_number, record)` without loading the whole workbook. |
| `_flush_xlsx_batch` / `_header_names` / `_row_mapping` | Header and batch helpers. |
| `_config_int` / `_config_str` | Worksheet name and header row from parser config. |

##### `apps/ingestion/converters/factory.py`

| Function | Purpose |
| --- | --- |
| `get_converter` | Select converter callable by extension. |
| `convert_source` | Verify format, convert, return `ConversionManifest`. |
| `_wrap_simple` | Adapt converters that have no conversion rejections. |

##### `apps/ingestion/staging.py`

| Function | Purpose |
| --- | --- |
| `staging_root` | Resolved `INGESTION_SYSTEM_ROOT`. |
| `staging_dir_for` | Owner-isolated staging directory for one ingestion. |
| `ensure_staging_dir` | Create the staging directory if missing. |
| `write_manifest` / `read_manifest` | Persist or load `manifest.json`. |
| `list_chunk_paths` | Sorted `chunk_*.csv` paths. |
| `delete_staging` | Remove staging only; never deletes original uploads. |
| `release_successful_files` | After success or partial success, delete the system copy and staging. |
| `cleanup_staging_for_ingestion` | Retention-aware cleanup for a finished ingestion. |
| `cleanup_abandoned_staging` | Sweep abandoned or expired staging directories. |

##### `apps/ingestion/parsers/profiles.py`

| Function / constant | Purpose |
| --- | --- |
| `DAILY_OHLCV_COLUMNS` / `TICK_DATA_COLUMNS` | Canonical column profiles. |
| `canonical_columns` | Return the profile for a data type. |
| `default_column_map` | Default source→canonical maps (generator column names). |

##### `apps/ingestion/parsers/column_mapping.py`

| Function | Purpose |
| --- | --- |
| `normalize_column_name` | Lowercase / underscore column names. |
| `normalize_columns` | Rename all DataFrame columns. |
| `resolve_column_map` | Merge defaults with rule `schema_mapping`. |
| `apply_column_map` | Map to canonical columns and keep `_source_row`. |

##### `apps/ingestion/parsers/normalization.py`

| Function | Purpose |
| --- | --- |
| `normalize_empty_values` | Treat blank / `na` / `null` tokens as missing. |
| `to_numeric_columns` | Coerce configured numeric fields. |
| `to_datetime_columns` | Parse timestamps to UTC. |
| `to_date_columns` | Parse trading dates. |

##### `apps/ingestion/parsers/validation.py`

| Function | Purpose |
| --- | --- |
| `ValidatorSpec` | One enabled validator from a rule-set snapshot. |
| `default_validators` | Built-in daily OHLCV and tick validator lists. |
| `load_validators` | Load DB rule rows or fall back to defaults. |
| `apply_validators` | Split into valid / rejected / duplicate DataFrames. |
| `_required` / `_numeric` / `_positive` / `_non_negative` / `_datetime` | Field validators. |
| `_ohlc` | OHLC relationship checks. |
| `_exchange_instrument_match` | Bulk exchange+symbol existence check. |
| `_duplicate_check` | Within-chunk duplicate detection. |

##### `apps/ingestion/parsers/dataframe_processor.py`

| Function | Purpose |
| --- | --- |
| `ProcessedChunk` | One staged chunk after validation. |
| `normalize_dataframe` | Column normalize → map → empty/numeric/datetime cleanup. |
| `process_staged_chunk` | Read one staged CSV with defensive `chunksize` and validate. |

##### `apps/ingestion/pipeline.py`

| Function | Purpose |
| --- | --- |
| `PipelineResult` | Manifest plus accepted/rejected/duplicate/chunk counters. |
| `stage_ingestion` | Resolve source path, convert, write manifest. |
| `iter_processed_chunks` | Yield validated splits for every staged CSV. |
| `run_conversion_pipeline` | Full conversion + validation without market-data persistence. |
| `_persist_pipeline_counters` | Update ingestion row counters after validation. |
| `_fail_conversion` | Mark ingestion `FAILED`, record transition and audit. |
| `_audit_conversion` | User or system audit event for conversion outcome. |
| `load_existing_manifest` | Read an existing staging manifest. |

##### `apps/ingestion/models.py` (Feature 006 additions)

| Symbol | Purpose |
| --- | --- |
| `ValidatorType` | Allowed validator names; no arbitrary expressions. |
| `ValidationRuleSet` | Named, versioned rule set per data type. |
| `ValidationRule` | One enabled field/frame validator with JSON parameters. |

##### `apps/ingestion/tasks.py` (Feature 006 addition)

| Task | Purpose |
| --- | --- |
| `cleanup_ingestion_staging` | Celery Beat cleanup of expired/abandoned staging. |
| `process_ingestion_file` | Still Feature 007 orchestration boundary (claim/persist). |

##### Tests

| File | Coverage |
| --- | --- |
| `apps/ingestion/tests/test_converters.py` | CSV/JSON/JSONL/XLSX chunks, mismatches, malformed JSONL, corrupt XLSX. |
| `apps/ingestion/tests/test_pipeline.py` | Staging + validation split, retention cleanup, source preservation. |
| `apps/ingestion/tests/test_scopes.py` | Two tests per scope (happy + negative) across converters, mapping, validators, staging, pipeline, audit. |
| `apps/ingestion/tests/test_tasks.py` | Staging cleanup Beat entry. |

#### Not in Feature 006

- Celery claim from `QUEUED` to `PROCESSING`
- DailyOHLCV / Trade / RejectedRow persistence
- Final ingestion success status after DB writes

### Feature 007: Chunk-based Celery processing and batch persistence

`process_ingestion_file(ingestion_id)` claims one `QUEUED`/`RETRYING` ingestion,
reuses the Feature 006 staging/validation pipeline, and persists each chunk
before the next chunk is read.

| Step | Behaviour |
| --- | --- |
| Atomic claim | Conditional DB update `QUEUED`/`RETRYING` → `PROCESSING` |
| Convert + validate | Staging CSV chunks via Feature 006 services |
| Persist valid rows | `DailyOHLCV` or `Trade` bulk inserts |
| Persist rejects | `RejectedRow` batches (conversion + validation + OHLCV conflicts) |
| Checkpoints | `IngestionBatch` unique on `(ingestion, batch_number)` |
| Finalize | `SUCCESS` / `PARTIAL_SUCCESS` / `FAILED` from counters |
| Audit | Started, completed, partial, failed, retry requested/exhausted |

Duplicate policy: identical OHLCV rows count as duplicates; conflicting OHLCV
values become rejections (never silent overwrite). Trade dedupe prefers
exchange trade ID, then sequence number, then a SHA-256 fallback key. Empty
readable files finalize as `SUCCESS`. After successful tick persistence
(`accepted_rows > 0`), Feature 008 schedules candle aggregation via
`transaction.on_commit()`.

`GET /api/v1/ingestion/{id}/rejections/` returns persisted rejected rows for the
owner.

### Feature 008: Candle aggregation and daily returns

Feature 008 aggregates persisted `Trade` rows into timeframe candles and
exposes daily simple/log returns from imported `DailyOHLCV` records.

Trade-derived candles are stored in `Candle` and remain separate from imported
`DailyOHLCV`. Aggregation never overwrites `DailyOHLCV`. Daily analytics use
`DailyOHLCV`; the candle API may also expose trade-derived `1d` candles and
labels them with `"source": "trade_derived_candle"`.

Supported timeframes: `1m`, `5m`, `15m`, `30m`, `1h`, `1d`.

- Intraday buckets use fixed UTC intervals.
- Daily buckets use midnight-to-midnight in `Exchange.timezone` (local calendar
  day, not exchange session hours).
- Empty intervals are omitted.
- Candle volume is a Decimal sum of `Trade.quantity`.
- Same-timestamp open/close ordering: `event_timestamp`, non-null
  `sequence_number` first, then `sequence_number`, then trade `id`.
- Aggregation recomputes affected buckets from trades (idempotent; late trades
  update existing candles and bump `aggregation_version`).

Analytics routes are mounted under `/api/v1/analytics/` and require JWT
authentication.

| Method | Endpoint | Purpose |
| ------ | -------- | ------- |
| GET | `/api/v1/analytics/candles/` | List persisted trade-derived candles. |
| GET | `/api/v1/analytics/returns/` | Daily simple or log returns from `DailyOHLCV`. |
| POST | `/api/v1/analytics/aggregations/` | Request async candle aggregation (`202`). |
| GET | `/api/v1/analytics/aggregations/{run_id}/` | Read aggregation run status. |

Common filters (where applicable): `instrument_id`, or natural keys
`instrument_symbol` / `instrument_name` (optionally with `exchange_code` /
`exchange_id` when the name or symbol is ambiguous), `portfolio_id` (ownership
enforced), `timeframe`, date/time range. Do not send `instrument_id` together
with symbol/name. Returns also accept `return_type=simple|log` (default
`simple`).

```http
GET /api/v1/analytics/returns/?instrument_name=Acme%20Industrial&exchange_code=XNYS&start_date=2024-01-01&end_date=2024-12-31&return_type=simple
GET /api/v1/analytics/returns/?instrument_symbol=ACME&exchange_code=XNYS&return_type=log
```

Candle and aggregation collection/detail responses use the standard success
envelope. The returns endpoint returns the same envelope via Django
`JsonResponse`. Pagination for candles nests `count` / `next` / `previous` /
`results` inside `data`.

#### Manual aggregation (`POST /api/v1/analytics/aggregations/`)

Identify the instrument with **one of** `instrument_id`, `instrument_symbol`,
or `instrument_name`, plus `exchange_code` or `exchange_id`. Do not put a name
into `instrument_id` (that field is an integer only).

Supported `timeframes` values: `1m`, `5m`, `15m`, `30m`, `1h`, `1d`. Omit
`timeframes` to aggregate all of them. Response is `202 Accepted` with a
persisted `AggregationRun` id. Poll `GET /api/v1/analytics/aggregations/{run_id}/`.

By name, one-minute candles only:

```json
{
  "instrument_name": "Glaxo Robotics",
  "exchange_code": "XNYS",
  "timeframes": ["1m"],
  "range_start": "2024-01-01T00:00:00Z",
  "range_end": "2024-01-03T00:00:00Z"
}
```

By symbol, five-minute candles:

```json
{
  "instrument_symbol": "ACME",
  "exchange_code": "XNYS",
  "timeframes": ["5m"],
  "range_start": "2024-01-01T00:00:00Z",
  "range_end": "2024-01-03T00:00:00Z"
}
```

Intraday mix (`15m` + `30m` + `1h`):

```json
{
  "instrument_name": "Glaxo Robotics",
  "exchange_code": "XNYS",
  "timeframes": ["15m", "30m", "1h"],
  "range_start": "2024-01-01T00:00:00Z",
  "range_end": "2024-01-03T00:00:00Z"
}
```

Daily candles only:

```json
{
  "instrument_symbol": "ACME",
  "exchange_code": "XNYS",
  "timeframes": ["1d"],
  "range_start": "2024-01-01T00:00:00Z",
  "range_end": "2024-01-10T00:00:00Z"
}
```

All supported timeframes (omit the field, or send the full list):

```json
{
  "instrument_name": "Glaxo Robotics",
  "exchange_code": "XNYS",
  "timeframes": ["1m", "5m", "15m", "30m", "1h", "1d"],
  "range_start": "2024-01-01T00:00:00Z",
  "range_end": "2024-01-03T00:00:00Z"
}
```

Dispatch failures are stored as `dispatch_failed` and are retryable. Manual runs
audit the requesting user; ingestion-triggered runs use system attribution.

#### Candles and returns by name

```http
GET /api/v1/analytics/candles/?instrument_name=Glaxo%20Robotics&exchange_code=XNYS&timeframe=1m
GET /api/v1/analytics/returns/?instrument_name=Glaxo%20Robotics&exchange_code=XNYS&start_date=2024-01-01&end_date=2024-12-31&return_type=simple
```

Returns payloads include metric metadata, instrument/exchange identifiers,
lookback requirements, and dated `value` observations (`null` until a prior
valid close exists). Redis caching for analytics is deferred.

How to exercise the APIs locally (Docker web on port 8000):

```bash
docker compose exec web python manage.py migrate

TOKEN=$(curl -s -X POST http://127.0.0.1:8000/api/v1/auth/login/ \
  -H 'Content-Type: application/json' \
  -d '{"username":"YOUR_USER","password":"YOUR_PASSWORD"}' \
  | python -c 'import sys,json; print(json.load(sys.stdin)["data"]["access"])')

curl -s -X POST http://127.0.0.1:8000/api/v1/analytics/aggregations/ \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "instrument_name": "Glaxo Robotics",
    "exchange_code": "XNYS",
    "timeframes": ["1m", "1d"],
    "range_start": "2024-01-01T00:00:00Z",
    "range_end": "2024-01-03T00:00:00Z"
  }' | python -m json.tool

# then poll with the returned id:
# curl -s "http://127.0.0.1:8000/api/v1/analytics/aggregations/<run_id>/" \
#   -H "Authorization: Bearer $TOKEN" | python -m json.tool

curl -s "http://127.0.0.1:8000/api/v1/analytics/candles/?instrument_name=Glaxo%20Robotics&exchange_code=XNYS&timeframe=1m" \
  -H "Authorization: Bearer $TOKEN" | python -m json.tool
```

Automated tests:

```bash
python -m pytest apps/analytics/tests/ -q
```

### Feature 009: Trend indicators (SMA, EMA, MACD)

Feature 009 calculates SMA, EMA, and MACD from persisted trade-derived `Candle`
closes. Imported `DailyOHLCV` remains the source for `/api/v1/analytics/returns/`
and is not used here. Indicators are computed on each read, so a candle corrected
by a late tick is reflected on the next request. Values are not stored.

| Method | Endpoint | Purpose |
| ------ | -------- | ------- |
| GET | `/api/v1/analytics/sma/` | Simple moving average of candle closes. |
| GET | `/api/v1/analytics/ema/` | Exponential moving average, seeded with the first full-window SMA. |
| GET | `/api/v1/analytics/macd/` | MACD line, signal EMA, and histogram. |

All three require JWT authentication and the `analytics_read` throttle. They use
the same scope filters as returns: `instrument_id`, or `instrument_symbol` /
`instrument_name` (plus `exchange_code` or `exchange_id` when the name or symbol
is ambiguous), or `portfolio_id`. A portfolio belonging to another user returns
404. Shared candle history is readable by any authenticated user; the portfolio
filter only limits which instruments are included.

`timeframe` is required (`1m`, `5m`, `15m`, `30m`, `1h`, `1d`). One request uses
one timeframe. `start` and `end` are timezone-aware event times. When both are
sent, `end` must be on or after `start` and the span must not exceed 3,660 days.
A single response reads at most 10,000 in-range candles.

SMA and EMA accept `window` from 2 through 500. The default is 20. MACD accepts
`fast_period`, `slow_period`, and `signal_period` in that same range. Defaults
are 12, 26, and 9, and the fast period must be shorter than the slow period.
`include_incomplete` defaults to false, so open candles are left out of both the
result and the warm-up window. Set it to `true` to include them.

Warm-up values are JSON `null`. They are not zero. When `start` is set, earlier
candles are used only as calculation context and are omitted from `results`.
SMA/EMA output is a price. MACD output is a price difference. Presentation uses
IEEE-754 floats; intermediate formula steps are not rounded to a fixed number of
decimals. These GET requests are not audit events.

```http
GET /api/v1/analytics/sma/?instrument_symbol=ACME&exchange_code=XNYS&timeframe=1d&window=20
GET /api/v1/analytics/ema/?instrument_name=Acme%20Industrial&exchange_code=XNYS&timeframe=1h&window=12
GET /api/v1/analytics/macd/?instrument_symbol=ACME&exchange_code=XNYS&timeframe=1d
```

### Feature 010: Volatility indicators (Bollinger Bands, ATR)

Feature 010 calculates Bollinger Bands and ATR from trade-derived `Candle` rows
(including `1d`), compute-on-read like Feature 009. Population standard deviation
(`ddof=0`) is used for Bollinger bands. ATR seeds as the mean of the first window
true ranges (first bar uses high−low when no previous close exists), then Wilder
smooths. Ordinary GETs are not audit events.

| Method | Endpoint | Purpose |
| ------ | -------- | ------- |
| GET | `/api/v1/analytics/bollinger/` | Middle, upper, lower, and width. |
| GET | `/api/v1/analytics/atr/` | Average True Range in price units. |

Shared scope filters and limits match Feature 009. Bollinger accepts `window`
(default 20) and `std_multiplier` (default 2.0, range 0.1–5.0). ATR accepts
`window` (default 14, range 2–500). Width is null when middle is zero.

```http
GET /api/v1/analytics/bollinger/?instrument_symbol=ACME&exchange_code=XNYS&timeframe=1m&window=20&std_multiplier=2
GET /api/v1/analytics/atr/?instrument_id=1&timeframe=1h&window=14
```

### Feature 011: Momentum indicators (RSI, Stochastic)

Feature 011 calculates RSI and Stochastic from trade-derived `Candle` rows.
RSI uses Wilder-smoothed average gain/loss with explicit zero-gain/loss handling
(100 / 0 / 50). Stochastic `%K` uses the lookback high/low range; `%D` is an SMA
of `%K`. A zero range yields null `%K`. Ordinary GETs are not audit events.

| Method | Endpoint | Purpose |
| ------ | -------- | ------- |
| GET | `/api/v1/analytics/rsi/` | Relative Strength Index (0–100). |
| GET | `/api/v1/analytics/stochastic/` | Stochastic `%K` and `%D`. |

RSI `window` defaults to 14. Stochastic `window` defaults to 14 and
`signal_period` defaults to 3. Limits and incomplete-candle policy match Feature
009.

```http
GET /api/v1/analytics/rsi/?instrument_symbol=ACME&exchange_code=XNYS&timeframe=1d&window=14
GET /api/v1/analytics/stochastic/?instrument_id=1&timeframe=1h&window=14&signal_period=3
```

### Feature 020: Maximum drawdown

Feature 020 calculates running drawdown and period maximum drawdown from imported
`DailyOHLCV` closes (adjusted close preferred when present). The running peak
starts at the first valid close in the requested period; earlier history outside
the range is not used. Drawdown is a fractional negative value (`-0.25` means
−25%). Dividends and splits are not modelled unless adjusted closes are supplied.
Trade-derived candles are not used. Ordinary GETs are not audit events.

| Method | Endpoint | Purpose |
| ------ | -------- | ------- |
| GET | `/api/v1/analytics/drawdown/` | Running drawdown series and period MDD. |

Scope filters match daily returns (`instrument_*`, `exchange_*`, `portfolio_id`,
`start_date`, `end_date`). Each series includes per-date `value` / `peak` and a
period-level `maximum_drawdown`.

```http
GET /api/v1/analytics/drawdown/?instrument_symbol=ACME&exchange_code=XNYS&start_date=2024-01-01&end_date=2024-12-31
```

### Feature 005: User file management and ingestion dispatch

Users upload into the local drop folder or S3. The system copies a matched file
into that user's own `files` directory, queues it, and dispatches Celery.
Conversion and validation are Feature 006; market-data persistence remains
Feature 007.

Upload locations, shared by whoever calls import:

- Local: `sample_data` on the host, mounted in Docker as `/data/ingestion`. Put files in that folder, not under `users/`.
- S3: `INGESTION_S3_BUCKET` and `INGESTION_S3_PREFIX`. The client does not send a bucket or a path.

System store, one directory per user id, written only by import. Docker keeps it on the `mda_system` volume at `/var/lib/mda/system`, not under `sample_data`:

`/var/lib/mda/system/users/<user id>/files/<filename>`

Staging uses `/var/lib/mda/system/users/<user id>/staging/<ingestion id>/`. User 1 is `users/1/files`. User 2 is `users/2/files`. Import for user 2 never writes into user 1's directory, and list APIs only return the caller's rows. `storage_prefix` is accepted and stored as `files`. After a file finishes as success or partial success, the system copy and its staging directory are deleted. A failed file is kept so it can be retried.

Each stored file becomes one Celery task on RabbitMQ. The worker process count is `CELERY_WORKER_CONCURRENCY` in the env file, passed to the Compose worker as `--concurrency`. The sample value is 15, so 15 files run at once and any further file stays in the queue until a process finishes. Raise or lower that number, then recreate the worker. Redis is used only as the API cache.

| Call | Request | What it does |
| --- | --- | --- |
| `POST /api/v1/ingestion/validation-rule-sets/` | JSON name, data type, version, and `rules`. | Creates one validation rule set. Attach its `id` when creating an ingestion rule. |
| `POST /api/v1/ingestion/files/import/` | JSON `{ "storage_backends": ["local", "s3"] }`. Omit the body to read local, and S3 when `INGESTION_S3_BUCKET` is set. | Copies matching files from the local drop folder and S3 into the system volume `users/<caller id>/files/`, queues them, and removes the local original. A file with no matching rule stays in the drop folder. |
| `GET /api/v1/ingestion/logs/` | No body. Optional `page` and `page_size`. | Returns accepted, rejected and duplicate counts from the ingestion log for the caller's finished files. It does not count rows in the trade or daily candle tables. |
| `POST /api/v1/ingestion/rules/` | JSON rule body: name, data type, prefix, file pattern, parser config, optional `validation_rule_set_id`. Daily files use `synthetic_market_clean_*.csv`. Tick files use `synthetic_ticks_*.csv`. | Creates a rule owned by the authenticated user. The rule watches `users/<user id>/files/`. |
| Celery Beat `scan_enabled_ingestion_rules` | No HTTP body. Schedule is `INGESTION_SCAN_INTERVAL_SECONDS`. | Scans administrative rules whose owner is null. It does not scan user-owned rules. User files are queued by import. |

Create instruments that are not already listed, then a validation rule set, then
the ingestion rules. These pairs use the seeded symbols and exchanges and do
not repeat an existing `(exchange, symbol)`:

| symbol | exchange_code | asset_class | currency |
| --- | --- | --- | --- |
| ACME | XNAS | equity | USD |
| BETA | XLON | etf | EUR |
| CHRP | XCME | equity | GBP |
| DLTA | XNYS | equity | JPY |
| EPSN | XNAS | crypto | CNY |
| FBNK | XLON | equity | CHF |
| GLXO | XCME | etf | USD |
| HRZN | XNYS | equity | EUR |
| INFX | XNAS | equity | GBP |
| JVNL | XLON | crypto | JPY |

```json
POST /api/v1/instruments/
{
  "exchange_code": "XNAS",
  "symbol": "ACME",
  "name": "Acme Industrial",
  "asset_class": "equity",
  "currency": "USD"
}
```

```json
POST /api/v1/ingestion/validation-rule-sets/
{
  "name": "daily-strict",
  "data_type": "DAILY_OHLCV",
  "version": "2",
  "rules": [
    {"field_name": "exchange_identifier", "validator": "REQUIRED", "sort_order": 1},
    {"field_name": "instrument_identifier", "validator": "REQUIRED", "sort_order": 2},
    {"field_name": "trading_date", "validator": "REQUIRED", "sort_order": 3},
    {"field_name": "open", "validator": "NUMERIC", "sort_order": 4},
    {"field_name": "high", "validator": "NUMERIC", "sort_order": 5},
    {"field_name": "low", "validator": "NUMERIC", "sort_order": 6},
    {"field_name": "close", "validator": "NUMERIC", "sort_order": 7},
    {"field_name": "volume", "validator": "NON_NEGATIVE", "sort_order": 8},
    {"field_name": "", "validator": "OHLC_RELATIONSHIP", "sort_order": 9},
    {"field_name": "", "validator": "EXCHANGE_INSTRUMENT_MATCH", "sort_order": 10},
    {"field_name": "", "validator": "DUPLICATE_CHECK", "sort_order": 11}
  ]
}
```

Use the returned validation rule set id on the ingestion rule. `storage_prefix`
is accepted and stored as `files`.

Use `synthetic_market_clean_*.csv` as `file_pattern`. `synthetic_market_*.csv` also copies `synthetic_market_dirty_*.csv`, which is the rejection fixture described below.

```json
POST /api/v1/ingestion/rules/
{
  "name": "ohlcv-csv",
  "data_type": "DAILY_OHLCV",
  "storage_prefix": "files",
  "file_pattern": "synthetic_market_clean_*.csv",
  "chunk_size": 5000,
  "parser_config": {"format": "csv"},
  "instrument_mapping": {"instrument_identifier": "symbol"},
  "exchange_mapping": {"exchange_identifier": "exchange"},
  "validation_rule_set_id": "<daily-strict id>"
}
```

Tick files need their own rule set and rule. Trade ids in the generator look like `ACME-2024-01-02-0000`.

```json
POST /api/v1/ingestion/validation-rule-sets/
{
  "name": "tick-strict",
  "data_type": "TICK_DATA",
  "version": "1",
  "rules": [
    {"field_name": "exchange_identifier", "validator": "REQUIRED", "sort_order": 1},
    {"field_name": "instrument_identifier", "validator": "REQUIRED", "sort_order": 2},
    {"field_name": "event_timestamp", "validator": "DATETIME", "sort_order": 3},
    {"field_name": "price", "validator": "NUMERIC", "sort_order": 4},
    {"field_name": "price", "validator": "POSITIVE", "sort_order": 5},
    {"field_name": "quantity", "validator": "NUMERIC", "sort_order": 6},
    {"field_name": "quantity", "validator": "POSITIVE", "sort_order": 7},
    {"field_name": "", "validator": "EXCHANGE_INSTRUMENT_MATCH", "sort_order": 8},
    {"field_name": "", "validator": "DUPLICATE_CHECK", "sort_order": 9}
  ]
}
```

```json
POST /api/v1/ingestion/rules/
{
  "name": "ticks-csv",
  "data_type": "TICK_DATA",
  "storage_prefix": "files",
  "file_pattern": "synthetic_ticks_*.csv",
  "chunk_size": 5000,
  "parser_config": {"format": "csv"},
  "instrument_mapping": {"instrument_identifier": "symbol"},
  "exchange_mapping": {"exchange_identifier": "exchange"},
  "validation_rule_set_id": "<tick-strict id>"
}
```

If a rule was already created with `synthetic_market_*.csv`, narrow it. Only fields you send are changed:

```json
PATCH /api/v1/ingestion/rules/{rule_id}/
{
  "file_pattern": "synthetic_market_clean_*.csv"
}
```

Drop files into `INGESTION_LOCAL_ROOT`, then:

```json
POST /api/v1/ingestion/files/import/
{
  "storage_backends": ["local", "s3"],
  "storage_prefix": "files"
}
```

Omit the body to read local files, and S3 when `INGESTION_S3_BUCKET` is set. `POST /api/v1/ingestion/{ingestion_id}/retry/` has no body. `GET /api/v1/ingestion/logs/`, `GET /api/v1/ingestion/files/`, `GET /api/v1/ingestion/rules/`, `GET /api/v1/ingestion/`, `GET /api/v1/ingestion/{id}/` and `GET /api/v1/ingestion/{id}/rejections/` have no body. List calls accept `page` and `page_size`. Ingestion list filters are query parameters: `status`, `data_type`, `rule_id`, `filename`, `start_date`, `end_date`.

### Uploading the same dates again

A second import of a file whose trade ids or daily candles are already stored does not insert those rows again.

Tick files from an earlier run are that case. Trade ids such as `ACME-2024-01-02-0000` and the rest of that series already exist, so those 2,000 rows are duplicates. `accepted_rows` stays 0, `duplicate_rows` is 2000, and the status stays `success`. The same is true for a clean daily file whose exchange, symbol, trading date, and OHLC values already match: matching candles are duplicates and the status stays `success`.

`synthetic_market_dirty_*.csv` is not a second copy of the clean file. The generator corrupts rows on purpose, and a later import of the same dates also conflicts with candles already stored. `GET /api/v1/ingestion/{id}/rejections/` then returns rows like these (`id` and `rejected_at` change per run):

```json
[
  {
    "source_row_number": 2,
    "reason_code": "PERSIST_MAPPING",
    "field_name": "",
    "error_message": "Unable to map OHLCV row."
  },
  {
    "source_row_number": 1001,
    "reason_code": "OHLC_RELATIONSHIP",
    "field_name": "ohlc",
    "error_message": "Invalid OHLC relationship."
  },
  {
    "source_row_number": 2001,
    "reason_code": "NON_NEGATIVE",
    "field_name": "volume",
    "error_message": "volume must be >= 0."
  },
  {
    "source_row_number": 4001,
    "reason_code": "PERSIST_MAPPING",
    "field_name": "",
    "error_message": "Unable to map OHLCV row."
  },
  {
    "source_row_number": 5001,
    "reason_code": "PERSIST_MAPPING",
    "field_name": "",
    "error_message": "Unable to map OHLCV row."
  },
  {
    "source_row_number": 7001,
    "reason_code": "OHLCV_CONFLICT",
    "field_name": "trading_date",
    "error_message": "Existing daily candle values conflict with the source row."
  },
  {
    "source_row_number": 8001,
    "reason_code": "OHLCV_CONFLICT",
    "field_name": "trading_date",
    "error_message": "Existing daily candle values conflict with the source row."
  },
  {
    "source_row_number": 9001,
    "reason_code": "OHLCV_CONFLICT",
    "field_name": "trading_date",
    "error_message": "Existing daily candle values conflict with the source row."
  },
  {
    "source_row_number": 10001,
    "reason_code": "EXCHANGE_INSTRUMENT_MATCH",
    "field_name": "instrument_identifier",
    "error_message": "Exchange and instrument combination was not found."
  }
]
```

On a 10,000-row dirty file those source rows are the injected faults, counted with the header line:

| Source row | Injected fault | Why it is rejected |
| --- | --- | --- |
| 2 | `volume` removed | The candle cannot be stored. `PERSIST_MAPPING`. |
| 1001 | `high` and `low` swapped | `OHLC_RELATIONSHIP`. |
| 2001 | negative `volume` | `NON_NEGATIVE`. |
| 4001 | `close` null | The candle cannot be stored. `PERSIST_MAPPING`. |
| 5001 | `volume` set to `12k` | Not a number, so the candle cannot be stored. `PERSIST_MAPPING`. |
| 7001 | `adjusted_close` blank | The date is already stored from the clean file with a different adjusted close. `OHLCV_CONFLICT`. The stored candle is kept. |
| 8001 | `low` driven negative | Same date already stored with different prices. `OHLCV_CONFLICT`. |
| 9001 | `date` shifted one day | That symbol and date already has a different candle. `OHLCV_CONFLICT`. |
| 10001 | exchange set to `ZZZZ` | No such listing. `EXCHANGE_INSTRUMENT_MATCH`. |

Other dirty rows (a broken timestamp, a malformed price, plus one exact duplicate and one conflicting duplicate appended after the last row) are rejected the same way. Retrying that ingestion does not repair them. Leave `synthetic_market_dirty_*.csv` in the drop folder by using the clean-file pattern above.

To get new rows accepted, start the day after the last stored candle. `2027-11-02` is the next weekday after a candle stored on `2027-11-01`; use the next weekday after whatever date you actually have:

```bash
python scripts/generate_market_data.py --symbols 10 --exponent 4 --ticks-per-day 100 --seed 42 --start-date 2027-11-02
```

That command writes a new clean daily file and a new tick file. Import those. The earlier tick file, with `ACME-2024-01-02-0000` and the rest, stays a duplicate run.

Administrative rules are configured in Django admin or the ORM. A rule stores
its storage backend, relative location under `INGESTION_LOCAL_ROOT`, glob
pattern, supported formats, scan interval, stability policy,
schema/instrument/exchange mappings, and scan watermark/status. Local and EFS
rules use the local filesystem adapter; the S3 choice is reserved and fails
loudly until an adapter is added. New administrative rules default to
`file_pattern="*"` and the supported formats `csv`, `json` and `xlsx`.

Inside the Compose web container:

```bash
_mda_compose exec web python3 manage.py seed_market_data
```

`seed_market_data` seeds exchanges first, then instruments. `seed_exchanges` and
`seed_instruments` also run on their own. `seed_instruments --count` defaults to
the generator symbol pool. The same `--count` flag is accepted by
`seed_market_data`. The catalog is `canonical_instruments()` in
`scripts/generate_market_data.py`, so daily files and tick files that use those
listings refer to the same instrument. Seeding is safe to repeat. It refreshes
seed-controlled names, timezones, asset classes and currencies, and leaves
`is_active` and records outside the catalog unchanged.

| Method | Endpoint                                                | Purpose                                                                                                     |
| ------ | ------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------- |
| GET    | `/api/v1/exchanges/`                                  | List shared exchanges. Optional `code` filter. No body.                                                   |
| GET    | `/api/v1/instruments/`                                | List shared instruments. Filters: `exchange_code`, `symbol`, `asset_class`, `currency`, `search`. No body. |
| POST   | `/api/v1/instruments/`                                | Create one shared instrument on an existing exchange. Body is the instrument example above.               |
| GET    | `/api/v1/portfolios/by-name/?name=`                   | Retrieve one owned portfolio by its name.                                                                   |
| POST   | `/api/v1/portfolios/by-name/instruments/bulk/`        | Bulk-attach instruments to an owned portfolio addressed by `name`.                                        |
| POST   | `/api/v1/portfolios/{portfolio_id}/instruments/bulk/` | The same bulk attach, addressed by portfolio id.                                                            |

The name match is case-insensitive and only searches portfolios you own. A missing name returns 404. If you have two portfolios with that same name, the response is 409 and you should use the existing id route. The bulk response includes both `name` and `portfolio_id`; each instrument line still uses `symbol` and `exchange`.

`GET /api/v1/portfolios/by-name/?name=Core%20book` has no body. Bulk attach creates a missing listing when `create_if_missing` is true, then attaches it. One invalid line rolls the request back.

```json
POST /api/v1/portfolios/{portfolio_id}/instruments/bulk/
{
  "create_if_missing": true,
  "instruments": [
    {
      "exchange_code": "XNAS",
      "symbol": "ACME",
      "name": "Acme Industrial",
      "asset_class": "equity",
      "currency": "USD",
      "alias": "Core holding",
      "is_active": true
    },
    {
      "exchange_code": "XLON",
      "symbol": "BETA",
      "name": "Beta Markets",
      "asset_class": "etf",
      "currency": "EUR",
      "alias": "",
      "is_active": true
    }
  ]
}
```

The by-name call uses the same `instruments` list and adds `name`:

```json
POST /api/v1/portfolios/by-name/instruments/bulk/
{
  "name": "Core book",
  "create_if_missing": true,
  "instruments": [
    {
      "exchange_code": "XNAS",
      "symbol": "ACME",
      "name": "Acme Industrial",
      "asset_class": "equity",
      "currency": "USD",
      "alias": "Core holding",
      "is_active": true
    }
  ]
}
```

Portfolio creation and single-instrument attach remain the Feature 003
endpoints. Bulk attachment is all-or-nothing: one invalid exchange, instrument
or metadata mismatch rolls the request back. An instrument that is already
attached is reported as `ALREADY_ATTACHED` and is not duplicated. Ingestion
mapping resolves `exchange` plus `symbol` to the shared instrument and does not
create a listing unless the caller explicitly opts in. Portfolio membership is
not required for that resolution.

## Project layout

```text
.
├── apps/
│   ├── accounts/          # Custom user model
│   ├── analytics/         # Candle aggregation, returns, and trend indicators
│   │   ├── services/      # Aggregation, dispatch, metrics_calculator, trend
│   │   └── tasks.py       # process_aggregation_run Celery task
│   ├── audit/             # Append-only user and system audit records
│   ├── common/            # Health, logging and middleware
│   ├── exchanges/         # Exchange master data
│   ├── ingestion/         # Rules, storage, converters, staging, validation pipeline
│   │   ├── converters/    # CSV/JSON/JSONL/XLSX → staging CSV chunks
│   │   ├── parsers/       # Column map, normalization, validators, chunk processor
│   │   ├── pipeline.py    # run_conversion_pipeline orchestration
│   │   └── staging.py     # Staging dirs, manifest, retention cleanup
│   ├── instruments/       # Instrument master data
│   ├── market_data/       # DailyOHLCV, Trade, and trade-derived Candle models
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
4. Start MySQL, Redis and RabbitMQ. MySQL is exposed on host port `3307` by default and
   listens on container port `3306`. `DB_PORT` is for Django running directly
   on your computer; Compose overrides it to `3306` for containers. RabbitMQ
   accepts Celery tasks on port `5672`.

   ```powershell
   docker compose up -d db redis rabbitmq
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
   in the one-shot `migrate` service, then starts the web and worker services
   after migrations succeed.

After setup, `dev_up`, `dev_down` and `dev_restart` work from any new WSL Bash
terminal. They accept optional service names, for example `dev_restart web`.
The commands use Docker Compose's `--env-file` option, since Compose does not
automatically read the `MDA_ENV_FILE` variable used by Django's local settings.

This starts:

- MySQL 8 on `MYSQL_HOST_PORT` (default `3307`; container port `3306`).
- Redis on `REDIS_HOST_PORT` (default `6379`).
- Django on `http://localhost:8000`.
- Celery worker.

Docker Compose reads variables from the configured env file and substitutes
`${VARIABLE}` expressions in `docker-compose.yml`. The Compose-specific values
set container hostnames, ports, paths and the cache URL; the regular `DB_HOST`,
`DB_PORT`, `CACHE_URL` and Celery URLs remain for commands run directly on your
host. MySQL uses the non-root `DB_USER` account for Django, with
`DB_ROOT_PASSWORD` reserved for the database administrator.

Compose requires the database credentials, Django secret and allowed hosts,
`MYSQL_HOST_PORT`, `REDIS_HOST_PORT`, `REDIS_IMAGE`, `WEB_HOST_PORT`, `COMPOSE_DB_HOST`,
`COMPOSE_DB_PORT`, `COMPOSE_CACHE_URL`, `COMPOSE_CELERY_BROKER_URL`,
`COMPOSE_CELERY_RESULT_BACKEND`, and the Compose ingestion
path. The web and worker containers write logs to `/var/log/mda/app.log`
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

| Variable             | Purpose                          | Default                  |
| -------------------- | -------------------------------- | ------------------------ |
| `LOG_LEVEL`        | Root log level                   | `INFO`                 |
| `LOG_FILE_PATH`    | Rotating log file path           | `/var/log/mda/app.log` |
| `LOG_MAX_BYTES`    | Maximum size of one log file     | `10485760`             |
| `LOG_BACKUP_COUNT` | Number of rotated files retained | `5`                    |

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
- `CELERY_BROKER_URL` (RabbitMQ `amqp://`), `CELERY_RESULT_BACKEND` (`rpc://`)
- `CELERY_WORKER_CONCURRENCY` — worker processes in Compose. Sample value is `15`.
- `LOG_FILE_PATH`
- `INGESTION_LOCAL_ROOT` — upload drop folder. Docker mounts `sample_data` here.
- `INGESTION_SYSTEM_ROOT` — system copies and staging. Docker uses `/var/lib/mda/system`.

Optional ingestion settings:

- `INGESTION_SCAN_INTERVAL_SECONDS` (default `60`) — Celery Beat discovery cadence for administrative rules.
- `INGESTION_HASH_CHUNK_SIZE_BYTES` (default `1048576`) — content-hash read size.
- `INGESTION_MAX_UPLOAD_BYTES` (default `1073741824`, 1 GiB) — maximum size of one uploaded file.
- `INGESTION_MIN_CHUNK_SIZE` (default `1`) — minimum `chunk_size` accepted from the API.
- `INGESTION_DEFAULT_CHUNK_SIZE` (default `5000`) — row batch size stored on a new user rule when the request omits `chunk_size`.
- `INGESTION_MAX_CHUNK_SIZE` (default `100000`) — maximum `chunk_size` accepted from the API.
- `INGESTION_IMPORT_MAX_FILES` (default `500`) — maximum number of files one configured import copies.
- `INGESTION_STAGING_RETENTION_SUCCESS_SECONDS` (default `3600`) — how long successful staging dirs are kept.
- `INGESTION_STAGING_RETENTION_FAILED_SECONDS` (default `86400`) — how long failed staging dirs are kept.
- `INGESTION_STAGING_CLEANUP_INTERVAL_SECONDS` (default `3600`) — Celery Beat cadence for staging cleanup.
- `INGESTION_REJECTED_RAW_MAX_BYTES` (default `2048`) — max UTF-8 size of one rejected raw record payload.
- `INGESTION_TASK_MAX_RETRIES` (default `5`) — Celery retries for transient ingestion failures.
- `INGESTION_TASK_SOFT_TIME_LIMIT_SECONDS` (default `1800`) — soft time limit for `process_ingestion_file`.
- `INGESTION_TASK_TIME_LIMIT_SECONDS` (default `2100`) — hard time limit for `process_ingestion_file`.
- `INGESTION_S3_BUCKET`, `INGESTION_S3_PREFIX`, `INGESTION_S3_REGION` — S3 location read by the import call. Leave the bucket empty when S3 is not used.
- `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN` — credentials read by the AWS SDK. These are not API fields.

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
