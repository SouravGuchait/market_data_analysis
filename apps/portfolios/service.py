import logging
from datetime import date
from decimal import Decimal, InvalidOperation
from django.db import transaction
from django.utils.dateparse import parse_date, parse_datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from apps.market_data.models import Instrument, DailyOHLCV
from apps.exchanges.models import Exchange

logger = logging.getLogger(__name__)

class DataMappingError(Exception):
    """Custom exception raised for unrecoverable data integrity or validation failures."""
    def __init__(self, message, error_type=None, row_data=None):
        super().__init__(message)
        self.error_type = error_type
        self.row_data = row_data


class MarketDataMappingService:
    """
    Handles validation, parsing, and normalization of raw synthetic generator rows 
    into standard Exchange, Instrument, and DailyOHLCV database models.
    """

    @classmethod
    def clean_decimal(cls, value: str, field_name: str) -> Decimal:
        """Coerces numeric values to accurate Decimals; catches malformed numbers or blanks."""
        try:
            # Strip commas or unwanted formatting strings if present in dirty rows
            value = value.replace(",", "").strip()
            return Decimal(value)
        except (ValueError, InvalidOperation):
            raise DataMappingError(f"Field '{field_name}' contains malformed numeric data: {value}", "malformed_number")

    @classmethod
    def validate_ohlc_relationships(cls, open_p: Decimal, high: Decimal, low: Decimal, close_p: Decimal):
        """Enforces physical price geometry constraints prior to database hits."""
        if open_p <= 0 or high <= 0 or low <= 0 or close_p <= 0:
            raise DataMappingError("Prices must be strictly positive values.", "negative_price")
        if high < max(open_p, close_p):
            raise DataMappingError(f"High price ({high}) cannot be lower than Open ({open_p}) or Close ({close_p}).", "bad_ohlc")
        if low > min(open_p, close_p):
            raise DataMappingError(f"Low price ({low}) cannot be higher than Open ({open_p}) or Close ({close_p}).", "bad_ohlc")
        if high < low:
            raise DataMappingError(f"High price ({high}) cannot be less than Low price ({low}).", "bad_ohlc")

    @classmethod
    @transaction.atomic
    def map_and_persist_row(cls, row: dict, allow_instrument_creation: bool = True) -> tuple[DailyOHLCV, str]:
        """
        Processes a single raw data dictionary, maps relationships, and resolves conflicts.
        
        Returns:
            tuple: (DailyOHLCV instance, action_taken string ['inserted', 'skipped_duplicate'])
        """
        # 1. Structural Check: Ensure vital identifier labels exist
        exchange_code = row.get("exchange")
        symbol = row.get("symbol")
        if not exchange_code or not symbol:
            raise DataMappingError("Missing primary index attributes: 'exchange' or 'symbol'.", "missing_field", row)

        # 2. Master Table Phase: Resolve targeted Exchange reference
        try:
            exchange = Exchange.objects.get(code=exchange_code, is_active=True)
        except Exchange.DoesNotExist:
            raise DataMappingError(f"Exchange node '{exchange_code}' is unknown or inactive.", "unknown_exchange", row)

        # 3. Master Table Phase: Resolve or create Instrument listing 
        instrument = None
        if allow_instrument_creation:
            # Safely create without modifying or overwriting distinct metadata if it exists
            instrument, _ = Instrument.objects.get_or_create(
                exchange=exchange,
                symbol=symbol,
                defaults={
                    "name": row.get("name", f"{symbol} Asset"),
                    "asset_class": row.get("asset_class", "equity"),
                    "currency": row.get("currency", "USD"),
                    "is_active": True
                }
            )
        else:
            try:
                instrument = Instrument.objects.get(exchange=exchange, symbol=symbol, is_active=True)
            except Instrument.DoesNotExist:
                raise DataMappingError(f"Instrument listing '{symbol}' missing on exchange '{exchange_code}'.", "missing_instrument", row)

        # 4. Ingestion Date/Time Validation and Parsing
        raw_date = row.get("date")
        raw_timestamp = row.get("timestamp")
        if not raw_date or not raw_timestamp:
            raise DataMappingError("Missing timeline vectors: 'date' or 'timestamp' are required.", "missing_field", row)

        trading_date = parse_date(str(raw_date)) if not isinstance(raw_date, date) else raw_date
        if not trading_date:
            raise DataMappingError(f"Malformed date structure: {raw_date}", "bad_date", row)

        try:
            # Handle ISO formats produced by generator
            naive_or_aware_dt = parse_datetime(str(raw_timestamp))
            if not naive_or_aware_dt:
                raise DataMappingError(f"Malformed timestamp string layout: {raw_timestamp}", "bad_date", row)
            
            # Normalize to system UTC baseline configuration
            utc_timestamp = naive_or_aware_dt.astimezone(ZoneInfo("UTC"))
        except (ValueError, ZoneInfoNotFoundError):
            raise DataMappingError(f"Datetime conversion failure on token: {raw_timestamp}", "bad_date", row)

        # Verify time-zone offset validity against internal exchange operational rules
        try:
            local_zone = ZoneInfo(exchange.timezone)
            local_dt = utc_timestamp.astimezone(local_zone)
            if local_dt.date() != trading_date:
                raise DataMappingError(
                    f"Date mismatch! Given calendar date '{trading_date}' differs from exchange local time date '{local_dt.date()}'.", 
                    "date_mismatch", row
                )
        except ZoneInfoNotFoundError:
            raise DataMappingError(f"Configured exchange timezone invalid: {exchange.timezone}", "bad_zone", row)

        # 5. Core Metric Metrics Parsing & Domain Checking
        open_price: Decimal = cls.clean_decimal(str(row.get("open")), "open")
        high_price: Decimal = cls.clean_decimal(str(row.get("high")), "high")
        low_price: Decimal  = cls.clean_decimal(str(row.get("low")), "low")
        close_price: Decimal = cls.clean_decimal(str(row.get("close")), "close")
        
        # Nullable handle for optional adjusted fields
        adj_close_raw = row.get("adjusted_close")
        adjusted_close: Decimal | None = cls.clean_decimal(adj_close_raw, "adjusted_close") if adj_close_raw else None

        # Integer volume extraction check
        raw_vol = row.get("volume")
        if raw_vol is None or raw_vol == "":
            raise DataMappingError("Volume indicator column cannot be null.", "missing_field", row)
        try:
            volume = int(raw_vol)
            if volume < 0:
                raise DataMappingError(f"Negative volume value encountered: {volume}", "negative_volume", row)
        except ValueError:
            raise DataMappingError(f"Invalid volume field type format: {raw_vol}", "bad_type", row)

        # Verify pricing geometry bounds match operational rules
        cls.validate_ohlc_relationships(open_price, high_price, low_price, close_price)

        # 6. Database Conflict & Duplicate Strategy Layer
        # Check against unique constraint index (instrument + trading_date)
        existing_candle = DailyOHLCV.objects.filter(instrument=instrument, trading_date=trading_date).first()
        
        if existing_candle:
            # Double check properties to see if it is an exact duplicate
            is_exact_match = (
                existing_candle.open == open_price and
                existing_candle.high == high_price and
                existing_candle.low == low_price and
                existing_candle.close == close_price and
                existing_candle.volume == volume and
                (existing_candle.adjusted_close == adjusted_close)
            )
            if is_exact_match:
                logger.info(f"Skipping exact duplicate candle for {symbol} on {trading_date}.")
                return existing_candle, "skipped_duplicate"
            else:
                # Conflicting row parameters detected with alternate data metrics
                raise DataMappingError(
                    f"Conflict detected for {symbol} on {trading_date}. Existing metrics disagree with incoming update row.", 
                    "duplicate_date", row
                )

        # 7. Creation and Safe Persistence Execution
        candle = DailyOHLCV.objects.create(
            instrument=instrument,
            trading_date=trading_date,
            timestamp=utc_timestamp,
            open=open_price,
            high=high_price,
            low=low_price,
            close=close_price,
            adjusted_close=adjusted_close,
            volume=volume
        )
        return candle, "inserted"