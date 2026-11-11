"""Serializers for user-owned portfolios and their instrument membership."""

from __future__ import annotations

from rest_framework import serializers

from apps.exchanges.models import Exchange
from apps.exchanges.serializers import ExchangeCodeField, ExchangeRelationSerializer
from apps.instruments.models import Instrument
from apps.instruments.serializers import InstrumentRelationSerializer
from apps.market_data.models import DailyOHLCV
from apps.portfolios.models import Portfolio, PortfolioInstrument


class PortfolioOwnerSerializer(serializers.Serializer):
    """Minimal public owner representation; never expose credentials."""

    username = serializers.CharField(read_only=True)


class PortfolioInstrumentReadSerializer(serializers.ModelSerializer):
    instrument = InstrumentRelationSerializer(read_only=True)
    exchange = ExchangeRelationSerializer(read_only=True)

    class Meta:
        model = PortfolioInstrument
        fields = (
            "id",
            "instrument",
            "exchange",
            "alias",
            "is_active",
            "created_at",
            "updated_at",
        )


class PortfolioReadSerializer(serializers.ModelSerializer):
    owner = PortfolioOwnerSerializer(read_only=True)
    instruments = PortfolioInstrumentReadSerializer(
        source="portfolio_instruments", many=True, read_only=True
    )

    class Meta:
        model = Portfolio
        fields = (
            "id",
            "owner",
            "name",
            "description",
            "status",
            "instruments",
            "created_at",
            "updated_at",
        )


class PortfolioWriteSerializer(serializers.ModelSerializer):
    """Accept only user-editable portfolio fields; ownership comes from the request."""

    class Meta:
        model = Portfolio
        fields = ("name", "description", "status")
        extra_kwargs = {
            "description": {"required": False},
            "status": {"required": False},
        }


def _validate_relationship_types(
    instrument: object,
    instrument_symbol: object,
    exchange: object,
    exchange_by_code: object,
) -> tuple[Instrument | None, str | None, Exchange | None, Exchange | None]:
    if instrument is not None and not isinstance(instrument, Instrument):
        raise serializers.ValidationError({"instrument_id": ["Invalid instrument."]})
    if instrument_symbol is not None and not isinstance(instrument_symbol, str):
        raise serializers.ValidationError({"instrument_symbol": ["Invalid symbol."]})
    if exchange is not None and not isinstance(exchange, Exchange):
        raise serializers.ValidationError({"exchange_id": ["Invalid exchange."]})
    if exchange_by_code is not None and not isinstance(exchange_by_code, Exchange):
        raise serializers.ValidationError({"exchange_code": ["Invalid exchange code."]})
    return (
        instrument if isinstance(instrument, Instrument) else None,
        instrument_symbol if isinstance(instrument_symbol, str) else None,
        exchange if isinstance(exchange, Exchange) else None,
        exchange_by_code if isinstance(exchange_by_code, Exchange) else None,
    )


def _extract_relationship_fields(
    attrs: dict[str, object],
) -> tuple[Instrument | None, str | None, Exchange | None, Exchange | None]:
    instrument = attrs.pop("instrument", None)
    instrument_symbol = attrs.pop("instrument_symbol", None)
    exchange = attrs.pop("exchange", None)
    exchange_by_code = attrs.pop("exchange_code", None)
    instrument, instrument_symbol, exchange, exchange_by_code = _validate_relationship_types(
        instrument, instrument_symbol, exchange, exchange_by_code
    )
    if instrument is not None and instrument_symbol is not None:
        raise serializers.ValidationError(
            {"instrument_symbol": ["Provide either instrument_id or instrument_symbol."]}
        )
    if instrument is None and instrument_symbol is None:
        raise serializers.ValidationError(
            {"instrument_symbol": ["Provide instrument_id or instrument_symbol."]}
        )
    if exchange is not None and exchange_by_code is not None:
        raise serializers.ValidationError(
            {"exchange_code": ["Provide either exchange_id or exchange_code, not both."]}
        )
    if exchange_by_code is not None:
        exchange = exchange_by_code
    return instrument, instrument_symbol, exchange, exchange_by_code


def _instrument_for_symbol(
    symbol: str,
    exchange: Exchange | None,
    exchange_field: str,
) -> Instrument:
    instruments = Instrument.objects.filter(symbol__iexact=symbol, is_active=True)
    if exchange is not None:
        instruments = instruments.filter(exchange=exchange)
    matches = list(instruments[:2])
    if not matches:
        other_exchange_exists = (
            exchange is not None
            and Instrument.objects.filter(symbol__iexact=symbol, is_active=True).exists()
        )
        if other_exchange_exists:
            raise serializers.ValidationError(
                {exchange_field: ["The selected exchange must match the instrument's exchange."]}
            )
        raise serializers.ValidationError(
            {"instrument_symbol": [f"Unknown or inactive instrument symbol: {symbol}."]}
        )
    if len(matches) > 1:
        raise serializers.ValidationError(
            {
                "instrument_symbol": [
                    "Multiple instruments use this symbol; provide exchange_code or exchange_id."
                ]
            }
        )
    return matches[0]


def _validate_instrument_exchange(
    instrument: Instrument,
    exchange: Exchange | None,
    exchange_field: str,
) -> Exchange:
    target_exchange = exchange or instrument.exchange
    if target_exchange != instrument.exchange:
        raise serializers.ValidationError(
            {exchange_field: ["The selected exchange must match the instrument's exchange."]}
        )
    if not target_exchange.is_active:
        raise serializers.ValidationError(
            {exchange_field: ["The selected exchange is not active."]}
        )
    return target_exchange


class PortfolioInstrumentWriteSerializer(serializers.ModelSerializer):
    instrument_id = serializers.PrimaryKeyRelatedField(
        source="instrument",
        queryset=Instrument.objects.filter(is_active=True),
        write_only=True,
        required=False,
    )
    instrument_symbol = serializers.CharField(
        write_only=True,
        required=False,
        allow_blank=False,
    )
    exchange_id = serializers.PrimaryKeyRelatedField(
        source="exchange",
        queryset=Exchange.objects.filter(is_active=True),
        write_only=True,
        required=False,
        allow_null=True,
    )
    exchange_code = ExchangeCodeField(write_only=True, required=False)

    class Meta:
        model = PortfolioInstrument
        fields = (
            "instrument_id",
            "instrument_symbol",
            "exchange_id",
            "exchange_code",
            "alias",
            "is_active",
        )
        extra_kwargs = {
            "alias": {"required": False},
            "is_active": {"required": False},
        }

    def validate(self, attrs: dict[str, object]) -> dict[str, object]:
        instrument, symbol, exchange, exchange_by_code = _extract_relationship_fields(attrs)
        exchange_field = "exchange_code" if exchange_by_code is not None else "exchange_id"
        if instrument is None:
            assert isinstance(symbol, str)
            instrument = _instrument_for_symbol(symbol, exchange, exchange_field)
        attrs["instrument"] = instrument
        attrs["exchange"] = _validate_instrument_exchange(
            instrument,
            exchange,
            exchange_field,
        )
        return attrs


class DailyOHLCVReadSerializer(serializers.ModelSerializer):
    """Include related instrument and exchange labels with market records."""

    exchange_code = serializers.ReadOnlyField(source="instrument.exchange.code")
    exchange_name = serializers.ReadOnlyField(source="instrument.exchange.name")
    symbol = serializers.ReadOnlyField(source="instrument.symbol")
    instrument_name = serializers.ReadOnlyField(source="instrument.name")
    instrument = InstrumentRelationSerializer(read_only=True)

    class Meta:
        model = DailyOHLCV
        fields = (
            "id",
            "instrument",
            "exchange_code",
            "exchange_name",
            "symbol",
            "instrument_name",
            "trading_date",
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "adjusted_close",
            "volume",
        )
        read_only_fields = fields
