# pyright: reportAny=false, reportExplicitAny=false
"""Deterministic synthetic market-data generator for development and testing.

Generates clean and intentionally dirty fixtures in CSV, JSON, JSONL, and XLSX
with a consistent schema. Optional tick files use the same four formats. Runs
offline: no Django configuration, database, or market API is required.

Row count can be given as an exponent: --exponent 4 (base 10) generates
10**4 = 10,000 clean rows. Output files carry the UTC run timestamp in their
name, and every row has a calendar `date` plus a timezone-aware `timestamp`
(market close in the exchange's local timezone).

Examples:
    # Clean+dirty OHLCV and ticks as CSV only (matches default ingestion rules)
    python3 scripts/generate_market_data.py --symbols 10 --exponent 4 --ticks-per-day 20 \\
        --seed 42 --formats csv --output-dir sample_data


    python3 scripts/generate_market_data.py --exponent 5 --seed 42
    python3 scripts/generate_market_data.py --base 2 --exponent 16 --symbols 8

    python3 scripts/generate_market_data.py --symbols 10 --exponent 3 --ticks-per-day 20 --seed 42 --formats json --start-date 2025-12-02  --output-dir sample_data
        --no-dirty
"""

import argparse
import json
import logging
import math
import secrets
from collections.abc import Callable
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# One market-data row. Values are deliberately loose: dirty fixtures hold
# strings, None, or missing keys where clean rows hold numbers.
Row = dict[str, Any]

EXCHANGES = [
    ("XNYS", "New York Stock Exchange", "America/New_York"),
    ("XNAS", "Nasdaq", "America/New_York"),
    ("XLON", "London Stock Exchange", "Europe/London"),
    ("XCME", "CME Group", "America/New_York"),
]
SYMBOL_POOL = [
    ("ACME", "Acme Industrial"),
    ("BETA", "Beta Materials"),
    ("CHRP", "Chipwave Systems"),
    ("DLTA", "Delta Logistics"),
    ("EPSN", "Epsilon Energy"),
    ("FBNK", "Fjord Banking Group"),
    ("GLXO", "Glaxo Robotics"),
    ("HRZN", "Horizon Retail"),
    ("INFX", "Infinitix Data"),
    ("JVNL", "Javelin Networks"),
]
# Local trading session (open, close) per exchange timezone. The daily candle
# timestamp is the session close; ticks are spread across the session.
SESSIONS = {
    "America/New_York": (time(9, 30), time(16, 0)),
    "Europe/London": (time(8, 0), time(16, 30)),
}
DEFAULT_SESSION = (time(9, 30), time(16, 0))
MAX_TICKS_PER_DAY = 1_000  # daily volume is always >= 1,000, so each tick gets size >= 1
TICK_COLUMNS = [
    "exchange",
    "symbol",
    "trade_id",
    "date",
    "timestamp",
    "price",
    "size",
]
XLSX_MAX_ROWS = 1_048_575  # Excel limit minus header row

# Value-diversity settings. More decimals and a bounded, mean-reverting price
# walk keep prices from piling up on the same values as row counts grow.
PRICE_DECIMALS = 6  # matches the planned DecimalField(decimal_places=6)
PRICE_REVERSION = 0.01  # daily pull of price back toward its symbol's anchor
DEFAULT_VOLUME_HIGH = 10_000_000  # exclusive upper bound for daily volume

ASSET_CLASSES = ["equity", "etf", "equity", "equity", "crypto"]
CURRENCIES = ["USD", "EUR", "GBP", "JPY", "CNY", "CHF"]

COLUMNS = [
    "exchange",
    "symbol",
    "name",
    "asset_class",
    "currency",
    "date",
    "timestamp",
    "open",
    "high",
    "low",
    "close",
    "adjusted_close",
    "volume",
]

# Kinds of corruption injected into dirty daily fixtures. Every kind appears at
# least once. Two rows are also appended at the end: an exact duplicate of the
# first clean row ("duplicate_date") and a conflicting duplicate (same exchange,
# symbol and date, different volume).
ERROR_KINDS = [
    "missing_field",
    "bad_ohlc",
    "negative_volume",
    "bad_date",
    "duplicate_date",
    "null_close",
    "bad_type",
    "malformed_number",
    "empty_string",
    "negative_price",
    "date_mismatch",
    "unknown_exchange",
]

# Tick-level faults. Validators reject by row; a readable dirty tick file ends
# PARTIAL_SUCCESS when some rows remain valid, or FAILED when none do.
TICK_ERROR_KINDS = [
    "missing_price",
    "negative_price",
    "zero_size",
    "bad_timestamp",
    "bad_type",
    "unknown_exchange",
    "unknown_symbol",
    "duplicate_trade_id",
]


def canonical_instruments(count: int | None = None) -> list[Row]:
    """Return the instrument catalog shared by generation and database seeding.

    The default count is the symbol pool, so each pool symbol is listed once.
    A larger count uses the same index rules as ``build_instruments``, including
    the numeric generation suffix.
    """
    size = len(SYMBOL_POOL) if count is None else count
    return build_instruments(size)


def build_instruments(count: int) -> list[Row]:
    """Return `count` instrument descriptors drawn from a fixed vocabulary."""
    if count < 1:
        raise ValueError("At least one instrument is required.")
    instruments: list[Row] = []
    for index in range(count):
        base_symbol, base_name = SYMBOL_POOL[index % len(SYMBOL_POOL)]
        generation = index // len(SYMBOL_POOL)
        symbol = base_symbol if generation == 0 else f"{base_symbol}{generation}"
        exchange_code, exchange_name, timezone = EXCHANGES[index % len(EXCHANGES)]
        instruments.append(
            {
                "exchange": exchange_code,
                "exchange_name": exchange_name,
                "timezone": timezone,
                "symbol": symbol,
                "name": base_name if generation == 0 else f"{base_name} {generation}",
                "asset_class": ASSET_CLASSES[index % len(ASSET_CLASSES)],
                "currency": CURRENCIES[index % len(CURRENCIES)],
            }
        )
    return instruments


def build_dates(start: date, days: int) -> list[date]:
    """Return `days` consecutive weekdays so symbols share trading dates."""
    if days < 1:
        raise ValueError("At least one trading day is required.")
    dates: list[date] = []
    current = start
    while len(dates) < days:
        if current.weekday() < 5:
            dates.append(current)
        current += timedelta(days=1)
    return dates


def _assert_clean(row: Row) -> None:
    """Guard against accidentally invalid rows in the clean dataset."""
    assert row["low"] <= min(row["open"], row["close"]), row
    assert row["high"] >= max(row["open"], row["close"]), row
    assert row["open"] > 0 and row["close"] > 0, row
    assert row["adjusted_close"] is None or row["adjusted_close"] > 0, row
    assert isinstance(row["volume"], int) and row["volume"] >= 0, row


def volume_ceiling(target_rows: int, ticks_per_day: int) -> int:
    """Pick the daily-volume upper bound so volumes (and tick sizes) stay diverse.

    A fixed range runs out of distinct values as the row count grows. Distinct
    tick sizes need roughly rows * ticks_per_day**2 of headroom, because each
    daily volume is split across its ticks. Small runs keep the 10M default.
    """
    return max(DEFAULT_VOLUME_HIGH, 3 * target_rows * max(ticks_per_day, 1) ** 2)


def generate_rows(
    instruments: list[Row],
    dates: list[date],
    rng: np.random.Generator,
    volume_high: int | None = None,
) -> list[Row]:
    """Generate plausible OHLCV rows with rounding-safe intra-day ranges."""
    if volume_high is None:
        volume_high = volume_ceiling(len(dates) * len(instruments), 0)
    rows: list[Row] = []
    anchors: dict[str, float] = {}
    last_close: dict[str, float] = {}
    for day in dates:
        for item in instruments:
            symbol = item["symbol"]
            if symbol not in anchors:
                anchors[symbol] = float(rng.uniform(20, 150))
            anchor = anchors[symbol]
            base = last_close.get(symbol, anchor)
            # Mean-reverting walk: keeps each symbol near its anchor instead of
            # drifting to the 1.0 floor, where thousands of prices would collide.
            pull = PRICE_REVERSION * (anchor / base - 1.0)
            close = max(1.0, base * (1.0 + pull + float(rng.normal(0.0, 0.02))))
            open_price = max(1.0, close * float(rng.normal(1.0, 0.005)))
            high = max(open_price, close) * float(rng.uniform(1.0001, 1.02))
            low = min(open_price, close) * float(rng.uniform(0.98, 0.9999))
            adjusted = close * float(rng.uniform(0.99, 0.999))
            volume = int(rng.integers(1_000, volume_high))
            last_close[symbol] = close
            row = {
                "exchange": item["exchange"],
                "exchange_name": item["exchange_name"],
                "timezone": item["timezone"],
                "symbol": symbol,
                "name": item["name"],
                "asset_class": item["asset_class"],
                "currency": item["currency"],
                "date": day.isoformat(),
                "timestamp": datetime.combine(
                    day,
                    SESSIONS.get(item["timezone"], DEFAULT_SESSION)[1],
                    tzinfo=ZoneInfo(item["timezone"]),
                ).isoformat(),
                "open": round(open_price, PRICE_DECIMALS),
                "high": round(high, PRICE_DECIMALS),
                "low": round(low, PRICE_DECIMALS),
                "close": round(close, PRICE_DECIMALS),
                "adjusted_close": round(adjusted, PRICE_DECIMALS),
                "volume": volume,
            }
            _assert_clean(row)
            rows.append(row)
    return rows


def make_dirty_rows(rows: list[Row], rng: np.random.Generator) -> list[Row]:
    """Corrupt a copy of the rows so every error kind appears at least once.

    Very small datasets apply only the corruption kinds that fit; the
    duplicate-date row is always appended last.
    """
    dirty = [dict(row) for row in rows]
    count = len(dirty)
    slot_kinds = [kind for kind in ERROR_KINDS if kind != "duplicate_date"]
    apply_kinds = slot_kinds[: min(count, len(slot_kinds))]
    # Corruption sites spread evenly across the file; rng drives the actual
    # error values so the whole output stays seed-deterministic.
    steps = max(len(apply_kinds) - 1, 1)
    slots = [(i * (count - 1)) // steps for i in range(len(apply_kinds))]
    for kind, index in zip(apply_kinds, slots):
        row = dirty[index]
        if kind == "missing_field":
            row.pop("volume", None)
        elif kind == "bad_ohlc":
            row["high"], row["low"] = row["low"], row["high"]
        elif kind == "negative_volume":
            row["volume"] = -int(rng.integers(1, 5_000))
        elif kind == "bad_date":
            row["timestamp"] = f"{row['timestamp']}tail"
        elif kind == "null_close":
            row["close"] = None
        elif kind == "bad_type":
            row["volume"] = "12k"
        elif kind == "malformed_number":
            row["open"] = f"{row['open']:,.{PRICE_DECIMALS}f}"
        elif kind == "empty_string":
            row["adjusted_close"] = ""
        elif kind == "negative_price":
            row["low"] = round(row["low"] - row["close"] * 2, PRICE_DECIMALS)
        elif kind == "date_mismatch":
            # `date` disagrees with the exchange-local date of `timestamp`.
            row["date"] = (date.fromisoformat(row["date"]) + timedelta(days=1)).isoformat()
        elif kind == "unknown_exchange":
            row["exchange"] = "ZZZZ"
    exact_duplicate = dict(rows[0])
    conflicting_duplicate = dict(rows[0])
    conflicting_duplicate["volume"] = int(rows[0]["volume"]) + 1
    return dirty + [exact_duplicate, conflicting_duplicate]


def make_dirty_ticks(ticks: list[Row], rng: np.random.Generator) -> list[Row]:
    """Corrupt tick rows for row-level rejection testing against tick validators."""
    if not ticks:
        return []
    dirty = [dict(row) for row in ticks]
    count = len(dirty)
    apply_kinds = TICK_ERROR_KINDS[: min(count, len(TICK_ERROR_KINDS))]
    steps = max(len(apply_kinds) - 1, 1)
    slots = [(i * (count - 1)) // steps for i in range(len(apply_kinds))]
    for kind, index in zip(apply_kinds, slots, strict=True):
        row = dirty[index]
        if kind == "missing_price":
            row.pop("price", None)
        elif kind == "negative_price":
            row["price"] = -abs(float(row["price"]))
        elif kind == "zero_size":
            row["size"] = 0
        elif kind == "bad_timestamp":
            row["timestamp"] = f"{row['timestamp']}tail"
        elif kind == "bad_type":
            row["size"] = "12k"
        elif kind == "unknown_exchange":
            row["exchange"] = "ZZZZ"
        elif kind == "unknown_symbol":
            row["symbol"] = "ZZZZZZ"
        elif kind == "duplicate_trade_id":
            row["trade_id"] = ticks[0]["trade_id"]
    exact_duplicate = dict(ticks[0])
    conflicting = dict(ticks[0])
    bump = float(rng.uniform(0.01, 0.5))
    conflicting["price"] = round(float(ticks[0]["price"]) + bump, PRICE_DECIMALS)
    return dirty + [exact_duplicate, conflicting]


def generate_ticks(rows: list[Row], ticks_per_day: int, rng: np.random.Generator) -> list[Row]:
    """Expand each clean daily candle into `ticks_per_day` individual trades.

    Ticks are consistent with their candle: the first price is the open, the
    last is the close, the extremes equal the high and low, and sizes sum to the
    daily volume. Timestamps are timezone-aware and fall inside the session.
    """
    if not 4 <= ticks_per_day <= MAX_TICKS_PER_DAY:
        raise ValueError(f"--ticks-per-day must be between 4 and {MAX_TICKS_PER_DAY}.")
    ticks: list[Row] = []
    for row in rows:
        zone = ZoneInfo(row["timezone"])
        open_t, close_t = SESSIONS.get(row["timezone"], DEFAULT_SESSION)
        day = date.fromisoformat(row["date"])
        start = datetime.combine(day, open_t, tzinfo=zone)
        span_ms = int((datetime.combine(day, close_t, tzinfo=zone) - start).total_seconds() * 1000)
        offsets = np.sort(rng.integers(0, span_ms, size=ticks_per_day))
        offsets[0], offsets[-1] = 0, span_ms
        prices = rng.uniform(row["low"], row["high"], size=ticks_per_day)
        prices[0], prices[-1] = row["open"], row["close"]
        hi_idx, lo_idx = rng.choice(np.arange(1, ticks_per_day - 1), size=2, replace=False)
        prices[hi_idx], prices[lo_idx] = row["high"], row["low"]
        prices = np.round(prices, PRICE_DECIMALS)
        volume = int(row["volume"])
        # Uneven (Dirichlet) trade sizes instead of near-identical ones.
        weights = rng.dirichlet(np.ones(ticks_per_day))
        sizes = 1 + rng.multinomial(volume - ticks_per_day, weights)
        for i in range(ticks_per_day):
            ticks.append(
                {
                    "exchange": row["exchange"],
                    "symbol": row["symbol"],
                    "trade_id": f"{row['symbol']}-{row['date']}-{i:04d}",
                    "date": row["date"],
                    "timestamp": (start + timedelta(milliseconds=int(offsets[i]))).isoformat(
                        timespec="milliseconds"
                    ),
                    "price": float(prices[i]),
                    "size": int(sizes[i]),
                }
            )
    return ticks


def write_csv(rows: list[Row], path: Path, columns: list[str] = COLUMNS) -> None:
    pd.DataFrame(rows, columns=pd.Index(columns)).to_csv(path, index=False)


def write_json(rows: list[Row], path: Path, columns: list[str] = COLUMNS) -> None:
    payload = [{key: row.get(key) for key in columns} for row in rows]
    _ = path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def write_jsonl(rows: list[Row], path: Path, columns: list[str] = COLUMNS) -> None:
    """Write one JSON object per line, using the selected columns."""
    lines = [json.dumps({key: row.get(key) for key in columns}) for row in rows]
    text = "\n".join(lines)
    if text:
        text += "\n"
    _ = path.write_text(text, encoding="utf-8")


def write_xlsx(rows: list[Row], path: Path, columns: list[str] = COLUMNS) -> None:
    pd.DataFrame(rows, columns=pd.Index(columns)).to_excel(path, index=False)


RowWriter = Callable[[list[Row], Path, list[str]], None]
WRITERS: dict[str, RowWriter] = {
    "csv": write_csv,
    "json": write_json,
    "jsonl": write_jsonl,
    "xlsx": write_xlsx,
}


def write_payload(
    output_dir: Path,
    stem: str,
    rows: list[Row],
    columns: list[str],
    counts: dict[str, int],
    formats: tuple[str, ...] = ("csv", "json", "jsonl", "xlsx"),
) -> None:
    """Write one dataset in the selected formats (default: all four)."""
    for extension in formats:
        writer = WRITERS[extension]
        if extension == "xlsx" and len(rows) > XLSX_MAX_ROWS:
            logger.warning("Skipping %s.xlsx: %d rows exceeds Excel limit", stem, len(rows))
            continue
        name = f"{stem}.{extension}"
        writer(rows, output_dir / name, columns)
        counts[name] = len(rows)


def next_weekday_after(last_day: date) -> date:
    """Return the next Mon–Fri calendar day after ``last_day``."""
    candidate = last_day + timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return candidate


def log_catalog(instruments: list[Row], dates: list[date]) -> None:
    """Log seeded (exchange, symbol) pairs and the next non-overlapping start date."""
    logger.info("Canonical listings (must match seed_instruments / seed_market_data):")
    for item in instruments:
        logger.info(
            "  %s @ %s  %s  %s/%s",
            item["symbol"],
            item["exchange"],
            item["name"],
            item["asset_class"],
            item["currency"],
        )
    if dates:
        nxt = next_weekday_after(dates[-1])
        logger.info(
            "Trading span %s .. %s (%d weekdays). Next non-duplicate --start-date=%s",
            dates[0].isoformat(),
            dates[-1].isoformat(),
            len(dates),
            nxt.isoformat(),
        )


def resolve_row_target(symbols: int, days: int, exponent: int | None, base: int) -> tuple[int, int]:
    """Return (target_rows, days_needed). Exponent mode overrides `days`."""
    if exponent is None:
        return symbols * days, days
    if exponent < 0 or base < 2:
        raise ValueError("--exponent must be >= 0 and --base must be >= 2.")
    target = base**exponent
    return target, math.ceil(target / symbols)


def generate(
    output_dir: Path,
    symbols: int = 5,
    days: int = 100,
    seed: int | None = None,
    start: date | None = None,
    exponent: int | None = None,
    base: int = 10,
    ticks_per_day: int = 0,
    formats: tuple[str, ...] = ("csv", "json", "jsonl", "xlsx"),
    include_dirty: bool = True,
    include_unexpected: bool = True,
) -> dict[str, int]:
    """Write clean, dirty, and optional tick fixtures; return row counts.

    Clean and dirty market data, and ticks when requested, are written in the
    selected formats. With `exponent`, exactly base**exponent clean rows are
    produced. File names are stamped with the UTC run time, e.g.
    synthetic_market_clean_20261109T101500Z.csv.

    Column names match ingestion default maps: daily ``exchange``/``symbol``/
    ``date``/OHLCV, ticks ``trade_id``/``size`` → ``exchange_trade_id``/
    ``quantity``. Listings match ``canonical_instruments()`` used by seeding.
    """
    resolved_seed = seed if seed is not None else secrets.randbits(32)
    logger.info("Using synthetic-data seed=%d", resolved_seed)
    rng = np.random.default_rng(resolved_seed)
    run_stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    target_rows, days_needed = resolve_row_target(symbols, days, exponent, base)
    logger.info("Target clean rows=%d (%d symbols x %d days)", target_rows, symbols, days_needed)
    instruments = build_instruments(symbols)
    try:
        dates = build_dates(start or date(2024, 1, 2), days_needed)
    except OverflowError as exc:
        raise ValueError(
            "Too many days for the calendar; raise --symbols or lower --exponent."
        ) from exc
    volume_high = volume_ceiling(target_rows, ticks_per_day)
    rows = generate_rows(instruments, dates, rng, volume_high)[:target_rows]
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    counts: dict[str, int] = {}
    write_payload(
        out, f"synthetic_market_clean_{run_stamp}", rows, COLUMNS, counts, formats=formats
    )
    if include_dirty:
        dirty_rows = make_dirty_rows(rows, rng)
        write_payload(
            out,
            f"synthetic_market_dirty_{run_stamp}",
            dirty_rows,
            COLUMNS,
            counts,
            formats=formats,
        )
    if ticks_per_day > 0:
        ticks = generate_ticks(rows, ticks_per_day, rng)
        write_payload(
            out, f"synthetic_ticks_{run_stamp}", ticks, TICK_COLUMNS, counts, formats=formats
        )
        if include_dirty:
            dirty_ticks = make_dirty_ticks(ticks, rng)
            write_payload(
                out,
                f"synthetic_ticks_dirty_{run_stamp}",
                dirty_ticks,
                TICK_COLUMNS,
                counts,
                formats=formats,
            )

    if include_unexpected:
        # Unexpected extra column appended to an otherwise valid subset.
        unexpected = [
            dict(row, unexpected_extra="ignore-me") for row in rows[: max(len(rows) // 4, 1)]
        ]
        name = f"synthetic_market_unexpected_columns_{run_stamp}.csv"
        counts[name] = len(unexpected)
        write_csv(unexpected, out / name, columns=COLUMNS + ["unexpected_extra"])
    log_catalog(instruments, dates)
    return counts


class Args(argparse.Namespace):
    """Typed view of the CLI arguments."""

    symbols: int = 5
    days: int = 100
    exponent: int | None = None
    base: int = 10
    ticks_per_day: int = 0
    seed: int | None = None
    output_dir: Path = Path("sample_data")
    start_date: str = "2024-01-02"
    formats: list[str] = ["csv", "json", "jsonl", "xlsx"]
    no_dirty: bool = False
    no_unexpected: bool = False


def parse_args(argv: list[str] | None = None) -> Args:
    parser = argparse.ArgumentParser(
        description=(
            "Generate synthetic OHLCV and tick fixtures aligned with MDA "
            "Instrument/DailyOHLCV/Trade column maps."
        )
    )
    _ = parser.add_argument(
        "--symbols", type=int, default=5, help="number of instruments (default: 5)"
    )
    _ = parser.add_argument(
        "--days",
        type=int,
        default=100,
        help="trading days per instrument (default: 100)",
    )
    _ = parser.add_argument(
        "--exponent",
        type=int,
        default=None,
        help="generate base**exponent clean rows (overrides --days)",
    )
    _ = parser.add_argument(
        "--base",
        type=int,
        default=10,
        help="base used with --exponent (default: 10)",
    )
    _ = parser.add_argument(
        "--ticks-per-day",
        type=int,
        default=0,
        help=(
            "also write tick CSV, JSON, JSONL, and XLSX with this many trades "
            f"per daily candle (4-{MAX_TICKS_PER_DAY}; default: 0 = no ticks)"
        ),
    )
    _ = parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="seed for reproducible data (default: fresh random seed per run)",
    )
    _ = parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("sample_data"),
        help="directory for generated files (default: sample_data)",
    )
    _ = parser.add_argument(
        "--start-date",
        type=str,
        default="2024-01-02",
        help=(
            "first trading date, YYYY-MM-DD (default: 2024-01-02). "
            "Advance past the last stored candle to avoid OHLCV/trade duplicates."
        ),
    )
    _ = parser.add_argument(
        "--formats",
        nargs="+",
        choices=sorted(WRITERS),
        default=["csv", "json", "jsonl", "xlsx"],
        help="output formats (default: csv json jsonl xlsx). Use: --formats csv",
    )
    _ = parser.add_argument(
        "--no-dirty",
        action="store_true",
        help="skip dirty OHLCV/tick fixtures (clean-only ingestion runs)",
    )
    _ = parser.add_argument(
        "--no-unexpected",
        action="store_true",
        help="skip the unexpected-columns CSV fixture",
    )
    return parser.parse_args(argv, namespace=Args())


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    args = parse_args(argv)
    start = date.fromisoformat(args.start_date)
    counts = generate(
        args.output_dir,
        symbols=args.symbols,
        days=args.days,
        seed=args.seed,
        start=start,
        exponent=args.exponent,
        base=args.base,
        ticks_per_day=args.ticks_per_day,
        formats=tuple(args.formats),
        include_dirty=not args.no_dirty,
        include_unexpected=not args.no_unexpected,
    )
    total = sum(counts.values())
    logger.info(
        "Generated %d files (%d fixture rows) in %s",
        len(counts),
        total,
        args.output_dir,
    )
    logger.info(
        "Ingestion patterns: synthetic_market_clean_*.csv  synthetic_ticks_*.csv  "
        "(leave dirty fixtures out of the rule pattern or use --no-dirty)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())