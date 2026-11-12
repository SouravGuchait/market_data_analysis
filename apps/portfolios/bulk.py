"""All-or-nothing create-and-attach for shared instruments."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from django.db import IntegrityError, transaction

from apps.exchanges.models import Exchange
from apps.instruments.models import Instrument
from apps.portfolios.models import Portfolio, PortfolioInstrument

CREATED_AND_ATTACHED = "CREATED_AND_ATTACHED"
REUSED_AND_ATTACHED = "REUSED_AND_ATTACHED"
ALREADY_ATTACHED = "ALREADY_ATTACHED"


class BulkAttachError(Exception):
    """The bulk request was rejected before any change was committed."""

    def __init__(self, errors: dict[str, list[str]]) -> None:
        self.errors = errors
        super().__init__("Bulk attach rejected")


@dataclass(frozen=True)
class BulkInstrumentInput:
    exchange: Exchange
    symbol: str
    name: str
    asset_class: str
    currency: str
    alias: str
    is_active: bool


@dataclass(frozen=True)
class BulkLine:
    symbol: str
    exchange_code: str
    status: str
    instrument_id: int
    membership_id: UUID
    instrument_created: bool
    membership_created: bool


@dataclass(frozen=True)
class BulkAttachResult:
    portfolio_id: UUID
    summary: dict[str, int]
    lines: list[BulkLine]


def _find_instrument(exchange: Exchange, symbol: str) -> Instrument | None:
    return Instrument.objects.filter(exchange=exchange, symbol__iexact=symbol).first()


def _metadata(instrument: Instrument) -> tuple[str, str, str]:
    return (instrument.name, instrument.asset_class, instrument.currency)


def _reject_invalid(items: list[BulkInstrumentInput], *, create_if_missing: bool) -> None:
    errors: list[str] = []
    for index, item in enumerate(items):
        existing = _find_instrument(item.exchange, item.symbol)
        label = f"{item.symbol} on {item.exchange.code}"
        if existing is None and not create_if_missing:
            errors.append(f"Item {index} ({label}): instrument is not registered.")
        elif existing is not None and not existing.is_active:
            errors.append(f"Item {index} ({label}): instrument is inactive.")
        elif existing is not None and _metadata(existing) != (
            item.name,
            item.asset_class,
            item.currency,
        ):
            errors.append(f"Item {index} ({label}): metadata does not match the master record.")
    if errors:
        raise BulkAttachError({"instruments": errors})


def _create_instrument(item: BulkInstrumentInput) -> tuple[Instrument, bool]:
    try:
        with transaction.atomic():
            instrument = Instrument.objects.create(
                exchange=item.exchange,
                symbol=item.symbol,
                name=item.name,
                asset_class=item.asset_class,
                currency=item.currency,
            )
    except IntegrityError:
        instrument = _find_instrument(item.exchange, item.symbol)
        if instrument is None:
            raise
        return instrument, False
    return instrument, True


def _instrument_for(
    item: BulkInstrumentInput, *, create_if_missing: bool
) -> tuple[Instrument, bool]:
    existing = _find_instrument(item.exchange, item.symbol)
    if existing is not None:
        return existing, False
    if not create_if_missing:
        raise BulkAttachError(
            {"instruments": [f"{item.symbol} on {item.exchange.code} is not registered."]}
        )
    return _create_instrument(item)


def _membership_for(
    portfolio: Portfolio, instrument: Instrument, item: BulkInstrumentInput
) -> tuple[PortfolioInstrument, bool]:
    existing = PortfolioInstrument.objects.filter(
        portfolio=portfolio, instrument=instrument
    ).first()
    if existing is not None:
        return existing, False
    try:
        with transaction.atomic():
            membership = PortfolioInstrument.objects.create(
                portfolio=portfolio,
                instrument=instrument,
                exchange=instrument.exchange,
                alias=item.alias,
                is_active=item.is_active,
            )
    except IntegrityError:
        existing = PortfolioInstrument.objects.filter(
            portfolio=portfolio, instrument=instrument
        ).first()
        if existing is None:
            raise
        return existing, False
    return membership, True


def _line(
    item: BulkInstrumentInput,
    instrument: Instrument,
    created: bool,
    attached: bool,
    membership: PortfolioInstrument,
) -> BulkLine:
    if not attached:
        status = ALREADY_ATTACHED
    elif created:
        status = CREATED_AND_ATTACHED
    else:
        status = REUSED_AND_ATTACHED
    return BulkLine(
        symbol=instrument.symbol,
        exchange_code=item.exchange.code,
        status=status,
        instrument_id=instrument.pk,
        membership_id=membership.pk,
        instrument_created=created,
        membership_created=attached,
    )


def _summary(lines: list[BulkLine]) -> dict[str, int]:
    counts = {
        "instruments_created": 0,
        "instruments_reused": 0,
        "instruments_attached": 0,
        "already_attached": 0,
    }
    for line in lines:
        if line.status == CREATED_AND_ATTACHED:
            counts["instruments_created"] += 1
            counts["instruments_attached"] += 1
        elif line.status == REUSED_AND_ATTACHED:
            counts["instruments_reused"] += 1
            counts["instruments_attached"] += 1
        else:
            counts["instruments_reused"] += 1
            counts["already_attached"] += 1
    return counts


def _attach_one(
    portfolio: Portfolio, item: BulkInstrumentInput, *, create_if_missing: bool
) -> BulkLine:
    instrument, created = _instrument_for(item, create_if_missing=create_if_missing)
    if not instrument.is_active or _metadata(instrument) != (
        item.name,
        item.asset_class,
        item.currency,
    ):
        label = f"{item.symbol} on {item.exchange.code}"
        raise BulkAttachError({"instruments": [f"{label} is not available for attachment."]})
    membership, attached = _membership_for(portfolio, instrument, item)
    return _line(item, instrument, created, attached, membership)


@transaction.atomic
def bulk_attach_instruments(
    portfolio: Portfolio,
    items: list[BulkInstrumentInput],
    *,
    create_if_missing: bool,
) -> BulkAttachResult:
    """Validate every item, then create and attach inside one transaction."""

    _reject_invalid(items, create_if_missing=create_if_missing)
    locked = Portfolio.objects.select_for_update().get(pk=portfolio.pk)
    lines = [_attach_one(locked, item, create_if_missing=create_if_missing) for item in items]
    return BulkAttachResult(portfolio_id=locked.pk, summary=_summary(lines), lines=lines)
