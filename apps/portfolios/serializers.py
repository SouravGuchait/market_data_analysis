from rest_framework import serializers
from apps.market_data.models import DailyOHLCV

class DailyOHLCVSerializer(serializers.ModelSerializer):
    """
    Serializer for Daily Market Candlestick records. Fully flattens relational 
    identifiers to expose explicit symbols and exchange names matching the ingestion contract.
    """
    # Flattens relationship keys into descriptive human-readable strings
    exchange_code = serializers.ReadOnlyField(source='instrument.exchange.code')
    exchange_name = serializers.ReadOnlyField(source='instrument.exchange.name')
    symbol = serializers.ReadOnlyField(source='instrument.symbol')
    instrument_name = serializers.ReadOnlyField(source='instrument.name')

    class Meta:
        model = DailyOHLCV
        fields = [
            'exchange_code',
            'exchange_name',
            'symbol',
            'instrument_name',
            'trading_date',
            'timestamp',
            'open',
            'high',
            'low',
            'close',
            'adjusted_close',
            'volume'
        ]
        read_only_fields = fields

    def to_representation(self, instance):
        """
        Ensures pricing dimensions align exactly with numeric precisions 
        when serialized to external string schemas.
        """
        representation = super().to_representation(instance)
        # Format decimal fields cleanly to remove trailing padding zeros where appropriate
        for field in ['open', 'high', 'low', 'close']:
            if representation[field]:
                representation[field] = str(float(instance.__getattribute__(field)))
        if representation['adjusted_close']:
            representation['adjusted_close'] = str(float(instance.adjusted_close))
        return representation