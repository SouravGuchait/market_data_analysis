"""Read serializers for portfolio relationships."""

from rest_framework import serializers

from apps.instruments.serializers import InstrumentReadSerializer
from apps.market_data.models import DailyOHLCV
from apps.portfolios.models import Portfolio, PortfolioInstrument


class PortfolioInstrumentReadSerializer(serializers.ModelSerializer):
    instrument = InstrumentReadSerializer(read_only=True)

    class Meta:
        model = PortfolioInstrument
        fields = ("id", "instrument", "alias", "is_active", "created_at", "updated_at")


class PortfolioReadSerializer(serializers.ModelSerializer):
    instruments = PortfolioInstrumentReadSerializer(
        source="portfolio_instruments", many=True, read_only=True
    )

    class Meta:
        model = Portfolio
        fields = ("id", "name", "description", "status", "instruments", "created_at", "updated_at")


class DailyOHLCVReadSerializer(serializers.ModelSerializer):
    """Include related instrument and exchange labels with market records."""

    exchange_code = serializers.ReadOnlyField(source="instrument.exchange.code")
    exchange_name = serializers.ReadOnlyField(source="instrument.exchange.name")
    symbol = serializers.ReadOnlyField(source="instrument.symbol")
    instrument_name = serializers.ReadOnlyField(source="instrument.name")
    instrument = InstrumentReadSerializer(read_only=True)

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
