"""Serializer for exchange master data."""

from rest_framework import serializers

from apps.exchanges.models import Exchange


class ExchangeCodeField(serializers.Field):
    """Accept an exchange code and resolve it to an active Exchange instance."""

    def to_internal_value(self, data: object) -> Exchange:
        if not isinstance(data, str):
            raise serializers.ValidationError("Exchange code must be a string.")
        code = data.strip()
        if not code:
            raise serializers.ValidationError("Exchange code cannot be blank.")

        exchange = Exchange.objects.filter(code__iexact=code, is_active=True).first()
        if exchange is None:
            raise serializers.ValidationError(f"Unknown or inactive exchange code: {code}.")
        return exchange

    def to_representation(self, value: Exchange) -> str:
        return value.code


class ExchangeSerializer(serializers.ModelSerializer):
    class Meta:
        model = Exchange
        fields = ("id", "code", "name", "timezone", "is_active")


class ExchangeRelationSerializer(serializers.ModelSerializer):
    """Human-readable exchange representation when nested as a relationship."""

    class Meta:
        model = Exchange
        fields = ("code", "name", "timezone", "is_active")
