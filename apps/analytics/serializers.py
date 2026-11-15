"""Serializers for analytics candle, return, and aggregation APIs."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from rest_framework import serializers

from apps.analytics.models import AggregationRun
from apps.analytics.services.metrics_calculator import (
    DEFAULT_ATR_WINDOW,
    DEFAULT_BOLLINGER_MULTIPLIER,
    DEFAULT_RSI_WINDOW,
    DEFAULT_STOCHASTIC_SIGNAL,
    DEFAULT_STOCHASTIC_WINDOW,
    DEFAULT_WINDOW,
    MACD_DEFAULT_FAST,
    MACD_DEFAULT_SIGNAL,
    MACD_DEFAULT_SLOW,
    MAX_BOLLINGER_MULTIPLIER,
    MAX_INDICATOR_PERIOD,
    MAX_INDICATOR_RANGE_DAYS,
    MIN_BOLLINGER_MULTIPLIER,
    MIN_INDICATOR_PERIOD,
)
from apps.analytics.services.timeframes import SUPPORTED_TIMEFRAMES
from apps.exchanges.models import Exchange
from apps.instruments.models import Instrument
from apps.market_data.models import Candle, CandleTimeframe


class AnalyticsInstrumentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Instrument
        fields = ("id", "symbol", "name")


class AnalyticsExchangeSerializer(serializers.ModelSerializer):
    class Meta:
        model = Exchange
        fields = ("id", "code", "name")


class CandleReadSerializer(serializers.ModelSerializer):
    """Trade-derived candle; ``timestamp`` exposes ``bucket_start``."""

    instrument = AnalyticsInstrumentSerializer(read_only=True)
    exchange = AnalyticsExchangeSerializer(read_only=True)
    timestamp = serializers.DateTimeField(source="bucket_start", read_only=True)
    source = serializers.SerializerMethodField()

    class Meta:
        model = Candle
        fields = (
            "id",
            "instrument",
            "exchange",
            "timeframe",
            "timestamp",
            "bucket_start",
            "bucket_end",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "trade_count",
            "is_complete",
            "aggregation_version",
            "source",
            "created_at",
            "updated_at",
        )

    def get_source(self, _obj: Candle) -> str:
        return "trade_derived_candle"


def _reject_dual_exchange(attrs: dict[str, object]) -> None:
    if attrs.get("exchange_id") is not None and attrs.get("exchange_code") is not None:
        raise serializers.ValidationError(
            {"exchange_code": ["Provide either exchange_id or exchange_code, not both."]}
        )


def _reject_dual_instrument(attrs: dict[str, object]) -> None:
    has_id = attrs.get("instrument_id") is not None
    has_key = attrs.get("instrument_symbol") is not None or attrs.get("instrument_name") is not None
    if has_id and has_key:
        raise serializers.ValidationError(
            {
                "instrument_id": [
                    "Provide instrument_id or instrument_symbol/instrument_name, not both."
                ]
            }
        )


class CandleFilterSerializer(serializers.Serializer):
    instrument_id = serializers.IntegerField(required=False, min_value=1)
    instrument_symbol = serializers.CharField(required=False, allow_blank=False)
    instrument_name = serializers.CharField(required=False, allow_blank=False)
    exchange_id = serializers.IntegerField(required=False, min_value=1)
    exchange_code = serializers.CharField(required=False, allow_blank=False)
    portfolio_id = serializers.UUIDField(required=False)
    timeframe = serializers.ChoiceField(choices=CandleTimeframe.choices, required=False)
    start = serializers.DateTimeField(required=False)
    end = serializers.DateTimeField(required=False)

    def validate(self, attrs: dict[str, object]) -> dict[str, object]:
        _reject_dual_exchange(attrs)
        _reject_dual_instrument(attrs)
        start = attrs.get("start")
        end = attrs.get("end")
        if isinstance(start, datetime) and isinstance(end, datetime) and end < start:
            raise serializers.ValidationError({"end": ["end must be on or after start."]})
        return attrs


def _validate_daily_metric_scope(attrs: dict[str, object]) -> dict[str, object]:
    _reject_dual_exchange(attrs)
    _reject_dual_instrument(attrs)
    start_date = attrs.get("start_date")
    end_date = attrs.get("end_date")
    if isinstance(start_date, date) and isinstance(end_date, date) and end_date < start_date:
        raise serializers.ValidationError(
            {"end_date": ["end_date must be on or after start_date."]}
        )
    has_scope = any(
        attrs.get(key) is not None
        for key in (
            "instrument_id",
            "instrument_symbol",
            "instrument_name",
            "portfolio_id",
            "exchange_id",
            "exchange_code",
        )
    )
    if not has_scope:
        raise serializers.ValidationError(
            {
                "detail": [
                    "Provide instrument_id, instrument_symbol, instrument_name, "
                    "portfolio_id, or an exchange filter."
                ]
            }
        )
    return attrs


class ReturnsFilterSerializer(serializers.Serializer):
    instrument_id = serializers.IntegerField(required=False, min_value=1)
    instrument_symbol = serializers.CharField(required=False, allow_blank=False)
    instrument_name = serializers.CharField(required=False, allow_blank=False)
    exchange_id = serializers.IntegerField(required=False, min_value=1)
    exchange_code = serializers.CharField(required=False, allow_blank=False)
    portfolio_id = serializers.UUIDField(required=False)
    start_date = serializers.DateField(required=False)
    end_date = serializers.DateField(required=False)
    return_type = serializers.ChoiceField(
        choices=("simple", "log"),
        required=False,
        default="simple",
    )

    def validate(self, attrs: dict[str, object]) -> dict[str, object]:
        return _validate_daily_metric_scope(attrs)


class DrawdownFilterSerializer(serializers.Serializer):
    """Query contract for running and maximum drawdown from DailyOHLCV closes."""

    instrument_id = serializers.IntegerField(required=False, min_value=1)
    instrument_symbol = serializers.CharField(required=False, allow_blank=False)
    instrument_name = serializers.CharField(required=False, allow_blank=False)
    exchange_id = serializers.IntegerField(required=False, min_value=1)
    exchange_code = serializers.CharField(required=False, allow_blank=False)
    portfolio_id = serializers.UUIDField(required=False)
    start_date = serializers.DateField(required=False)
    end_date = serializers.DateField(required=False)

    def validate(self, attrs: dict[str, object]) -> dict[str, object]:
        return _validate_daily_metric_scope(attrs)


def _require_analytics_scope(attrs: dict[str, object]) -> None:
    has_scope = any(
        attrs.get(key) is not None
        for key in (
            "instrument_id",
            "instrument_symbol",
            "instrument_name",
            "portfolio_id",
            "exchange_id",
            "exchange_code",
        )
    )
    if not has_scope:
        raise serializers.ValidationError(
            {
                "detail": [
                    "Provide instrument_id, instrument_symbol, instrument_name, "
                    "portfolio_id, or an exchange filter."
                ]
            }
        )


def _validate_indicator_range(attrs: dict[str, object]) -> None:
    start = attrs.get("start")
    end = attrs.get("end")
    if not isinstance(start, datetime) or not isinstance(end, datetime):
        return
    if end < start:
        raise serializers.ValidationError({"end": ["end must be on or after start."]})
    limit = timedelta(days=MAX_INDICATOR_RANGE_DAYS)
    if end - start > limit:
        raise serializers.ValidationError(
            {"end": [f"Date range must not exceed {MAX_INDICATOR_RANGE_DAYS} days."]}
        )


class _IndicatorScopeSerializer(serializers.Serializer):
    instrument_id = serializers.IntegerField(required=False, min_value=1)
    instrument_symbol = serializers.CharField(required=False, allow_blank=False)
    instrument_name = serializers.CharField(required=False, allow_blank=False)
    exchange_id = serializers.IntegerField(required=False, min_value=1)
    exchange_code = serializers.CharField(required=False, allow_blank=False)
    portfolio_id = serializers.UUIDField(required=False)
    timeframe = serializers.ChoiceField(choices=CandleTimeframe.choices)
    start = serializers.DateTimeField(required=False)
    end = serializers.DateTimeField(required=False)
    include_incomplete = serializers.BooleanField(required=False, default=False)

    def validate(self, attrs: dict[str, object]) -> dict[str, object]:
        _reject_dual_exchange(attrs)
        _reject_dual_instrument(attrs)
        _require_analytics_scope(attrs)
        _validate_indicator_range(attrs)
        return attrs


class WindowIndicatorFilterSerializer(_IndicatorScopeSerializer):
    """Query contract for SMA and EMA on trade-derived candle closes."""

    window = serializers.IntegerField(
        required=False,
        default=DEFAULT_WINDOW,
        min_value=MIN_INDICATOR_PERIOD,
        max_value=MAX_INDICATOR_PERIOD,
    )


class MacdIndicatorFilterSerializer(_IndicatorScopeSerializer):
    """Query contract for MACD. Defaults are the documented 12, 26, and 9."""

    fast_period = serializers.IntegerField(
        required=False,
        default=MACD_DEFAULT_FAST,
        min_value=MIN_INDICATOR_PERIOD,
        max_value=MAX_INDICATOR_PERIOD,
    )
    slow_period = serializers.IntegerField(
        required=False,
        default=MACD_DEFAULT_SLOW,
        min_value=MIN_INDICATOR_PERIOD,
        max_value=MAX_INDICATOR_PERIOD,
    )
    signal_period = serializers.IntegerField(
        required=False,
        default=MACD_DEFAULT_SIGNAL,
        min_value=MIN_INDICATOR_PERIOD,
        max_value=MAX_INDICATOR_PERIOD,
    )

    def validate(self, attrs: dict[str, object]) -> dict[str, object]:
        attrs = super().validate(attrs)
        fast = attrs.get("fast_period")
        slow = attrs.get("slow_period")
        if isinstance(fast, int) and isinstance(slow, int) and fast >= slow:
            raise serializers.ValidationError(
                {"fast_period": ["fast_period must be less than slow_period."]}
            )
        return attrs


class BollingerIndicatorFilterSerializer(_IndicatorScopeSerializer):
    """Query contract for Bollinger Bands on trade-derived candle closes."""

    window = serializers.IntegerField(
        required=False,
        default=DEFAULT_WINDOW,
        min_value=MIN_INDICATOR_PERIOD,
        max_value=MAX_INDICATOR_PERIOD,
    )
    std_multiplier = serializers.FloatField(
        required=False,
        default=DEFAULT_BOLLINGER_MULTIPLIER,
        min_value=MIN_BOLLINGER_MULTIPLIER,
        max_value=MAX_BOLLINGER_MULTIPLIER,
    )


class AtrIndicatorFilterSerializer(_IndicatorScopeSerializer):
    """Query contract for ATR. Default window is the documented period 14."""

    window = serializers.IntegerField(
        required=False,
        default=DEFAULT_ATR_WINDOW,
        min_value=MIN_INDICATOR_PERIOD,
        max_value=MAX_INDICATOR_PERIOD,
    )


class RsiIndicatorFilterSerializer(_IndicatorScopeSerializer):
    """Query contract for RSI. Default window is the documented period 14."""

    window = serializers.IntegerField(
        required=False,
        default=DEFAULT_RSI_WINDOW,
        min_value=MIN_INDICATOR_PERIOD,
        max_value=MAX_INDICATOR_PERIOD,
    )


class StochasticIndicatorFilterSerializer(_IndicatorScopeSerializer):
    """Query contract for Stochastic %K/%D on trade-derived candles."""

    window = serializers.IntegerField(
        required=False,
        default=DEFAULT_STOCHASTIC_WINDOW,
        min_value=MIN_INDICATOR_PERIOD,
        max_value=MAX_INDICATOR_PERIOD,
    )
    signal_period = serializers.IntegerField(
        required=False,
        default=DEFAULT_STOCHASTIC_SIGNAL,
        min_value=MIN_INDICATOR_PERIOD,
        max_value=MAX_INDICATOR_PERIOD,
    )


class AggregationCreateSerializer(serializers.Serializer):
    instrument_id = serializers.IntegerField(required=False, min_value=1)
    instrument_symbol = serializers.CharField(required=False, allow_blank=False)
    instrument_name = serializers.CharField(required=False, allow_blank=False)
    exchange_id = serializers.IntegerField(required=False, min_value=1)
    exchange_code = serializers.CharField(required=False, allow_blank=False)
    timeframes = serializers.ListField(
        child=serializers.ChoiceField(choices=[(value, value) for value in SUPPORTED_TIMEFRAMES]),
        required=False,
        allow_empty=False,
    )
    range_start = serializers.DateTimeField(required=True)
    range_end = serializers.DateTimeField(required=True)

    def validate(self, attrs: dict[str, object]) -> dict[str, object]:
        _reject_dual_exchange(attrs)
        _reject_dual_instrument(attrs)
        if attrs.get("exchange_id") is None and not attrs.get("exchange_code"):
            raise serializers.ValidationError(
                {"exchange_id": ["Provide exchange_id or exchange_code."]}
            )
        has_instrument = any(
            attrs.get(key) is not None
            for key in ("instrument_id", "instrument_symbol", "instrument_name")
        )
        if not has_instrument:
            raise serializers.ValidationError(
                {"instrument_id": ["Provide instrument_id, instrument_symbol, or instrument_name."]}
            )
        range_start = attrs.get("range_start")
        range_end = attrs.get("range_end")
        if (
            isinstance(range_start, datetime)
            and isinstance(range_end, datetime)
            and range_end < range_start
        ):
            raise serializers.ValidationError(
                {"range_end": ["range_end must be on or after range_start."]}
            )
        return attrs


class AggregationRunSerializer(serializers.ModelSerializer):
    class Meta:
        model = AggregationRun
        fields = (
            "id",
            "status",
            "trigger",
            "instrument_ids",
            "exchange_ids",
            "timeframes",
            "range_start",
            "range_end",
            "candles_created",
            "candles_updated",
            "candles_unchanged",
            "error_summary",
            "dispatch_attempts",
            "created_at",
            "started_at",
            "finished_at",
            "updated_at",
        )
