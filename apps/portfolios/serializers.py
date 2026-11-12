"""Serializers for user-owned portfolios and their instrument membership."""

from __future__ import annotations

from typing import NoReturn

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
        instrument, symbol, exchange, exchange_by_code = _membership_inputs(attrs)
        _reject_duplicate_identifiers(instrument, symbol, exchange, exchange_by_code)
        if exchange_by_code is not None:
            exchange = exchange_by_code
        if not isinstance(instrument, Instrument):
            instrument = _instrument_from_symbol(symbol, exchange, exchange_by_code)
        attrs["instrument"] = instrument
        attrs["exchange"] = _matching_exchange(instrument, exchange, exchange_by_code)
        return attrs


def _membership_inputs(
    attrs: dict[str, object],
) -> tuple[Instrument | None, str | None, Exchange | None, Exchange | None]:
    instrument = attrs.pop("instrument", None)
    symbol = attrs.pop("instrument_symbol", None)
    exchange = attrs.pop("exchange", None)
    exchange_by_code = attrs.pop("exchange_code", None)
    if instrument is not None and not isinstance(instrument, Instrument):
        raise serializers.ValidationError({"instrument_id": ["Invalid instrument."]})
    if symbol is not None and not isinstance(symbol, str):
        raise serializers.ValidationError({"instrument_symbol": ["Invalid symbol."]})
    if exchange is not None and not isinstance(exchange, Exchange):
        raise serializers.ValidationError({"exchange_id": ["Invalid exchange."]})
    if exchange_by_code is not None and not isinstance(exchange_by_code, Exchange):
        raise serializers.ValidationError({"exchange_code": ["Invalid exchange code."]})
    return instrument, symbol, exchange, exchange_by_code


def _reject_duplicate_identifiers(
    instrument: object, symbol: object, exchange: object, exchange_by_code: object
) -> None:
    if instrument is not None and symbol is not None:
        raise serializers.ValidationError(
            {"instrument_symbol": ["Provide either instrument_id or instrument_symbol."]}
        )
    if instrument is None and symbol is None:
        raise serializers.ValidationError(
            {"instrument_symbol": ["Provide instrument_id or instrument_symbol."]}
        )
    if exchange is not None and exchange_by_code is not None:
        raise serializers.ValidationError(
            {"exchange_code": ["Provide either exchange_id or exchange_code, not both."]}
        )


def _unknown_symbol(
    symbol: str, exchange: Exchange | None, exchange_by_code: Exchange | None
) -> NoReturn:
    if (
        exchange is not None
        and Instrument.objects.filter(symbol__iexact=symbol, is_active=True).exists()
    ):
        field = "exchange_code" if exchange_by_code is not None else "exchange_id"
        raise serializers.ValidationError(
            {field: ["The selected exchange must match the instrument's exchange."]}
        )
    raise serializers.ValidationError(
        {"instrument_symbol": [f"Unknown or inactive instrument symbol: {symbol}."]}
    )


def _instrument_from_symbol(
    symbol: object, exchange: Exchange | None, exchange_by_code: Exchange | None
) -> Instrument:
    if not isinstance(symbol, str):
        raise serializers.ValidationError({"instrument_symbol": ["Invalid symbol."]})
    instruments = Instrument.objects.filter(symbol__iexact=symbol, is_active=True)
    if exchange is not None:
        instruments = instruments.filter(exchange=exchange)
    matches = list(instruments[:2])
    if not matches:
        _unknown_symbol(symbol, exchange, exchange_by_code)
    if len(matches) > 1:
        raise serializers.ValidationError(
            {
                "instrument_symbol": [
                    "Multiple instruments use this symbol; provide exchange_code or exchange_id."
                ]
            }
        )
    return matches[0]


def _matching_exchange(
    instrument: Instrument, exchange: Exchange | None, exchange_by_code: Exchange | None
) -> Exchange:
    target = exchange or instrument.exchange
    field = "exchange_code" if exchange_by_code is not None else "exchange_id"
    if target != instrument.exchange:
        raise serializers.ValidationError(
            {field: ["The selected exchange must match the instrument's exchange."]}
        )
    if not target.is_active:
        raise serializers.ValidationError({field: ["The selected exchange is not active."]})
    return target


class BulkInstrumentItemSerializer(serializers.Serializer):
    """One shared instrument to create, when missing, and attach."""

    exchange_code = ExchangeCodeField()
    symbol = serializers.CharField(max_length=50)
    name = serializers.CharField(max_length=255)
    asset_class = serializers.ChoiceField(choices=Instrument.AssetClass.choices)
    currency = serializers.ChoiceField(choices=Instrument.Currency.choices)
    alias = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")
    is_active = serializers.BooleanField(required=False, default=True)

    def validate_symbol(self, value: str) -> str:
        symbol = value.strip()
        if not symbol:
            raise serializers.ValidationError("Symbol cannot be blank.")
        return symbol


class PortfolioNameQuerySerializer(serializers.Serializer):
    """Look up one owned portfolio by the name stored on the portfolio."""

    name = serializers.CharField(max_length=255)

    def validate_name(self, value: str) -> str:
        name = value.strip()
        if not name:
            raise serializers.ValidationError("Portfolio name cannot be blank.")
        return name


class BulkAttachSerializer(serializers.Serializer):
    """Validate a bulk create-and-attach request before any database write."""

    create_if_missing = serializers.BooleanField()
    instruments = BulkInstrumentItemSerializer(many=True, allow_empty=False)

    def validate(self, attrs: dict[str, object]) -> dict[str, object]:
        items = attrs.get("instruments")
        if not isinstance(items, list):
            raise serializers.ValidationError(
                {"instruments": ["A list of instruments is required."]}
            )
        seen: set[tuple[int, str]] = set()
        duplicates: list[str] = []
        for item in items:
            if not isinstance(item, dict):
                raise serializers.ValidationError({"instruments": ["Invalid instrument entry."]})
            exchange = item.get("exchange_code")
            symbol = item.get("symbol")
            if not isinstance(exchange, Exchange) or not isinstance(symbol, str):
                raise serializers.ValidationError({"instruments": ["Invalid instrument entry."]})
            key = (exchange.pk, symbol.casefold())
            if key in seen:
                duplicates.append(f"{symbol} on {exchange.code}")
            seen.add(key)
        if duplicates:
            raise serializers.ValidationError(
                {
                    "instruments": [
                        f"Duplicate instrument in request: {label}." for label in duplicates
                    ]
                }
            )
        return attrs


class BulkAttachByNameSerializer(BulkAttachSerializer):
    """Bulk attach addressed by the portfolio name instead of its UUID."""

    name = serializers.CharField(max_length=255)

    def validate_name(self, value: str) -> str:
        name = value.strip()
        if not name:
            raise serializers.ValidationError("Portfolio name cannot be blank.")
        return name


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
