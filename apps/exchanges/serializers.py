from rest_framework import serializers
from .models import Exchange

class ExchangeSerializer(serializers.ModelSerializer):
    """
    Serializer for Exchange master data.
    """
    class Meta:
        model = Exchange
        fields = ['code', 'name', 'timezone', 'is_active']