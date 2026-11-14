"""Serializers for analytics candle, return, and aggregation APIs."""

from __future__ import annotations

from datetime import date, datetime

from rest_framework import serializers

from apps.analytics.models import AggregationRun
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
