#!/usr/bin/env bash
# Seed exchanges/instruments and remind how to populate candles for the Streamlit home page.
set -euo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root_dir"

if ! command -v docker >/dev/null 2>&1; then
  printf 'docker is required\n' >&2
  exit 1
fi

compose=(docker compose)
if [[ -n "${MDA_ENV_FILE:-}" && -f "${MDA_ENV_FILE}" ]]; then
  compose+=(--env-file "$MDA_ENV_FILE")
elif [[ -f "$HOME/.config/env_vars/dev.env" ]]; then
  compose+=(--env-file "$HOME/.config/env_vars/dev.env")
fi

printf 'Seeding exchanges and instruments inside the web container...\n'
"${compose[@]}" exec web python manage.py seed_exchanges
"${compose[@]}" exec web python manage.py seed_instruments
printf 'Optional: generate and import sample files, then aggregate candles.\n'
printf '  # First run (clean OHLCV + ticks as CSV). ACME is XNYS in the seeded catalog.\n'
printf '  python scripts/generate_market_data.py --symbols 10 --exponent 4 --ticks-per-day 20 \\\n'
printf '      --seed 42 --formats csv --output-dir sample_data\n'
printf '  # Later runs: use the printed Next non-duplicate --start-date=... and --no-dirty\n'
printf '  Then POST /api/v1/ingestion/files/import/ (rules must match synthetic_market_clean_*.csv\n'
printf '  and synthetic_ticks_*.csv). Tick success auto-schedules candle aggregation.\n'
printf 'Done.\n'
