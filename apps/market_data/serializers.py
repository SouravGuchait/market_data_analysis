"""Serializers for tick/trade and daily OHLCV list APIs."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from rest_framework import serializers

from apps.exchanges.serializers import ExchangeRelationSerializer
from apps.instruments.serializers import InstrumentRelationSerializer
from apps.market_data.models import DailyOHLCV, Trade
from apps.market_data.selectors import MAX_DAILY_RANGE_DAYS, MAX_TICK_RANGE_DAYS


class TradeReadSerializer(serializers.ModelSerializer):
    instrument = InstrumentRelationSerializer(read_only=True)
    exchange = ExchangeRelationSerializer(read_only=True)

    class Meta:
        model = Trade
        fields = (
            "id",
            "instrument",
            "exchange",
            "event_timestamp",
            "price",
            "quantity",
            "exchange_trade_id",
            "sequence_number",
            "side",
            "ingestion_timestamp",
        )


class DailyOHLCVReadSerializer(serializers.ModelSerializer):
    instrument = InstrumentRelationSerializer(read_only=True)
    exchange = serializers.SerializerMethodField()

    class Meta:
        model = DailyOHLCV
        fields = (
            "id",
            "instrument",
            "exchange",
            "trading_date",
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "adjusted_close",
            "volume",
        )

    def get_exchange(self, obj: DailyOHLCV) -> dict[str, object]:
        exchange = obj.instrument.exchange
        return {
            "code": exchange.code,
            "name": exchange.name,
            "timezone": exchange.timezone,
            "is_active": exchange.is_active,
        }


class _ScopeFilterSerializer(serializers.Serializer):
    instrument_id = serializers.IntegerField(required=False, min_value=1)
    instrument_symbol = serializers.CharField(required=False, allow_blank=False)
    instrument_name = serializers.CharField(required=False, allow_blank=False)
    exchange_id = serializers.IntegerField(required=False, min_value=1)
    exchange_code = serializers.CharField(required=False, allow_blank=False)
    portfolio_id = serializers.UUIDField(required=False)

    def validate(self, attrs: dict[str, object]) -> dict[str, object]:
        has_id = attrs.get("instrument_id") is not None
        has_key = (
            attrs.get("instrument_symbol") is not None or attrs.get("instrument_name") is not None
        )
        if has_id and has_key:
            raise serializers.ValidationError(
                {
                    "instrument_id": [
                        "Provide instrument_id or instrument_symbol/instrument_name, not both."
                    ]
                }
            )
        if attrs.get("exchange_id") is not None and attrs.get("exchange_code") is not None:
            raise serializers.ValidationError(
                {"exchange_code": ["Provide either exchange_id or exchange_code, not both."]}
            )
        return attrs


class TickFilterSerializer(_ScopeFilterSerializer):
    start = serializers.DateTimeField(required=False)
    end = serializers.DateTimeField(required=False)
    exchange_trade_id = serializers.CharField(required=False, allow_blank=False)
    sequence_number = serializers.IntegerField(required=False)
    ordering = serializers.ChoiceField(
        choices=("event_timestamp", "-event_timestamp"),
        required=False,
        default="event_timestamp",
    )

    def validate(self, attrs: dict[str, object]) -> dict[str, object]:
        attrs = super().validate(attrs)
        start = attrs.get("start")
        end = attrs.get("end")
        if isinstance(start, datetime) and isinstance(end, datetime):
            if end < start:
                raise serializers.ValidationError({"end": ["end must be on or after start."]})
            if end - start > timedelta(days=MAX_TICK_RANGE_DAYS):
                raise serializers.ValidationError(
                    {
                        "end": [
                            f"Date range must not exceed {MAX_TICK_RANGE_DAYS} days for tick queries."
                        ]
                    }
                )
        return attrs


class DailyOHLCVFilterSerializer(_ScopeFilterSerializer):
    start_date = serializers.DateField(required=False)
    end_date = serializers.DateField(required=False)

    def validate(self, attrs: dict[str, object]) -> dict[str, object]:
        attrs = super().validate(attrs)
        start = attrs.get("start_date")
        end = attrs.get("end_date")
        if isinstance(start, date) and isinstance(end, date):
            if end < start:
                raise serializers.ValidationError(
                    {"end_date": ["end_date must be on or after start_date."]}
                )
            if (end - start).days > MAX_DAILY_RANGE_DAYS:
                raise serializers.ValidationError(
                    {
                        "end_date": [
                            f"Date range must not exceed {MAX_DAILY_RANGE_DAYS} days."
                        ]
                    }
                )
        return attrs
