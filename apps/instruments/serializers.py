"""Serializers for shared instrument data."""

from rest_framework import serializers

from apps.exchanges.models import Exchange
from apps.exchanges.serializers import ExchangeSerializer
from apps.instruments.models import Instrument


class InstrumentWriteSerializer(serializers.ModelSerializer):
    exchange = serializers.PrimaryKeyRelatedField(queryset=Exchange.objects.filter(is_active=True))

    class Meta:
        model = Instrument
        fields = ("id", "exchange", "symbol", "name", "asset_class", "currency", "is_active")


class InstrumentReadSerializer(serializers.ModelSerializer):
    exchange = ExchangeSerializer(read_only=True)
    exchange_name = serializers.ReadOnlyField(source="exchange.name")

    class Meta:
        model = Instrument
        fields = (
            "id",
            "exchange",
            "exchange_name",
            "symbol",
            "name",
            "asset_class",
            "currency",
            "is_active",
        )
