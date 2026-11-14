"""Bulk instrument resolution for ingestion persistence."""

from __future__ import annotations

from collections.abc import Iterable

from django.db.models.functions import Lower

from apps.instruments.models import Instrument


def load_instrument_map(
    pairs: Iterable[tuple[str, str]],
) -> dict[tuple[str, str], Instrument]:
    """Map (exchange_code, symbol) lowercased keys to active instruments."""

    wanted = {
        (exchange.strip().lower(), symbol.strip().lower())
        for exchange, symbol in pairs
        if str(exchange).strip() and str(symbol).strip()
    }
    if not wanted:
        return {}
    exchange_codes = {item[0] for item in wanted}
    symbols = {item[1] for item in wanted}
    rows = (
        Instrument.objects.filter(is_active=True, exchange__is_active=True)
        .annotate(_ex=Lower("exchange__code"), _sym=Lower("symbol"))
        .filter(_ex__in=exchange_codes, _sym__in=symbols)
        .select_related("exchange")
    )
    result: dict[tuple[str, str], Instrument] = {}
    for instrument in rows:
        key = (instrument.exchange.code.lower(), instrument.symbol.lower())
        if key in wanted:
            result[key] = instrument
    return result
