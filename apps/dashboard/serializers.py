"""Serializers for dashboard bootstrap."""

from __future__ import annotations

from rest_framework import serializers

from apps.exchanges.serializers import ExchangeSerializer
from apps.instruments.serializers import InstrumentReadSerializer
from apps.portfolios.models import Portfolio


class BootstrapPortfolioSerializer(serializers.ModelSerializer):
    """Bounded portfolio summary without nested market history."""

    instrument_count = serializers.SerializerMethodField()

    class Meta:
        model = Portfolio
        fields = (
            "id",
            "name",
            "description",
            "status",
            "instrument_count",
            "created_at",
            "updated_at",
        )

    def get_instrument_count(self, obj: Portfolio) -> int:
        from apps.portfolios.models import PortfolioInstrument

        return PortfolioInstrument.objects.filter(portfolio=obj).count()


class BootstrapQuerySerializer(serializers.Serializer):
    portfolio_id = serializers.UUIDField(required=False)


class BootstrapResponseSerializer(serializers.Serializer):
    user = serializers.DictField()
    portfolios = BootstrapPortfolioSerializer(many=True)
    exchanges = ExchangeSerializer(many=True)
    instruments = InstrumentReadSerializer(many=True)
    defaults = serializers.DictField()
    instrument_choices = serializers.DictField()
