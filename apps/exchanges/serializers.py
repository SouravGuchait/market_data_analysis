"""Serializer for exchange master data."""

from rest_framework import serializers

from apps.exchanges.models import Exchange


class ExchangeSerializer(serializers.ModelSerializer):
    class Meta:
        model = Exchange
        fields = ("id", "code", "name", "timezone", "is_active")
