"""Serializers for shared instrument data."""

from rest_framework import serializers

from apps.exchanges.models import Exchange
from apps.exchanges.serializers import ExchangeCodeField, ExchangeRelationSerializer
from apps.instruments.models import Instrument


class InstrumentWriteSerializer(serializers.ModelSerializer):
    exchange = serializers.PrimaryKeyRelatedField(
        queryset=Exchange.objects.filter(is_active=True),
        required=False,
    )
    exchange_code = ExchangeCodeField(write_only=True, required=False)

    class Meta:
        model = Instrument
        fields = (
            "id",
            "exchange",
            "exchange_code",
            "symbol",
            "name",
            "asset_class",
            "currency",
            "is_active",
        )
        # The default UniqueTogetherValidator cannot resolve the exchange
        # injected by validate() when exchange_code is used.
        validators = []

    def validate(self, attrs: dict[str, object]) -> dict[str, object]:
        exchange = attrs.pop("exchange", None)
        exchange_by_code = attrs.pop("exchange_code", None)

        if exchange is not None and exchange_by_code is not None:
            raise serializers.ValidationError(
                {"exchange_code": ["Provide either exchange or exchange_code, not both."]}
            )
        if exchange is None and exchange_by_code is None:
            raise serializers.ValidationError(
                {"exchange_code": ["Provide either exchange or exchange_code."]}
            )

        resolved = exchange_by_code or exchange
        if not isinstance(resolved, Exchange):
            raise serializers.ValidationError({"exchange_code": ["Invalid exchange."]})

        symbol = attrs.get("symbol", getattr(self.instance, "symbol", None))
        if isinstance(symbol, str):
            duplicates = Instrument.objects.filter(
                exchange=resolved,
                symbol__iexact=symbol,
            )
            if self.instance is not None:
                duplicates = duplicates.exclude(pk=self.instance.pk)
            if duplicates.exists():
                raise serializers.ValidationError(
                    {"symbol": ["This symbol already exists on the selected exchange."]}
                )

        attrs["exchange"] = resolved
        return attrs


class InstrumentRelationSerializer(serializers.ModelSerializer):
    """Human-readable instrument representation when nested as a relationship."""

    exchange = ExchangeRelationSerializer(read_only=True)

    class Meta:
        model = Instrument
        fields = ("symbol", "name", "exchange", "asset_class", "currency", "is_active")


class InstrumentReadSerializer(serializers.ModelSerializer):
    exchange = ExchangeRelationSerializer(read_only=True)
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
