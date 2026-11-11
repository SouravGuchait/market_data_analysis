from rest_framework import serializers
from .models import Exchange, Instrument
class InstrumentSerializer(serializers.ModelSerializer):
    """
    Serializer for Instrument master data, fetching the Exchange Code 
    instead of an internal database integer ID.
    """
    # Resolves and represents the exchange via its unique string code (e.g., 'XNYS')
    exchange = serializers.SlugRelatedField(
        slug_field='code', 
        queryset=Exchange.objects.filter(is_active=True)
    )
    exchange_name = serializers.ReadOnlyField(source='exchange.name')

    class Meta:
        model = Instrument
        fields = ['exchange', 'exchange_name', 'symbol', 'name', 'asset_class', 'currency', 'is_active']