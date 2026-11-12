"""HTTP endpoints for user-owned portfolios."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any, cast

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.audit.models import AuditAction, AuditResourceType, AuditStatus
from apps.audit.services import record_audit_event
from apps.exchanges.models import Exchange
from apps.instruments.models import Instrument
from apps.portfolios.bulk import (
    BulkAttachError,
    BulkAttachResult,
    BulkInstrumentInput,
    BulkLine,
    bulk_attach_instruments,
)
from apps.portfolios.models import Portfolio
from apps.portfolios.pagination import PortfolioPagination
from apps.portfolios.selectors import (
    AmbiguousPortfolioNameError,
    get_portfolio_instrument,
    get_user_portfolio,
    get_user_portfolio_by_name,
    list_portfolio_instruments,
    list_user_portfolios,
)
from apps.portfolios.serializers import (
    BulkAttachByNameSerializer,
    BulkAttachSerializer,
    PortfolioInstrumentReadSerializer,
    PortfolioInstrumentWriteSerializer,
    PortfolioNameQuerySerializer,
    PortfolioReadSerializer,
    PortfolioWriteSerializer,
)
from apps.portfolios.services import (
    DuplicatePortfolioInstrumentError,
    add_portfolio_instrument,
    create_portfolio,
    delete_portfolio,
    remove_portfolio_instrument,
    update_portfolio,
)


def success(
    message: str,
    data: Any = None,
    status_code: int = status.HTTP_200_OK,
) -> Response:
    return Response(
        {"success": True, "message": message, "data": data, "errors": None},
        status=status_code,
    )


def failure(
    message: str,
    errors: Any,
    status_code: int = status.HTTP_400_BAD_REQUEST,
) -> Response:
    return Response(
        {"success": False, "message": message, "data": None, "errors": errors},
        status=status_code,
    )


def portfolio_not_found() -> Response:
    """Use one indistinguishable response for missing and foreign portfolios."""

    return failure("Portfolio not found", {"detail": ["Not found."]}, status.HTTP_404_NOT_FOUND)


class PortfolioAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get_owned_portfolio(self, request: Request, portfolio_id: uuid.UUID) -> Portfolio | None:
        return get_user_portfolio(request.user, portfolio_id)


class PortfolioListCreateView(PortfolioAPIView):
    def get(self, request: Request) -> Response:
        queryset = list_user_portfolios(request.user)
        paginator = PortfolioPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        serializer = PortfolioReadSerializer(page, many=True)
        paginated = paginator.get_paginated_response(serializer.data).data
        return success("Portfolios retrieved", paginated)

    def post(self, request: Request) -> Response:
        serializer = PortfolioWriteSerializer(data=request.data)
        if not serializer.is_valid():
            return failure("Validation failed", serializer.errors)

        validated_data = cast(dict[str, Any], serializer.validated_data)
        with transaction.atomic():
            portfolio = create_portfolio(owner=request.user, data=validated_data)
            record_audit_event(
                actor=request.user,
                action=AuditAction.PORTFOLIO_CREATED,
                resource_type=AuditResourceType.PORTFOLIO,
                resource_id=portfolio.pk,
                status=AuditStatus.SUCCESS,
                request=request,
                metadata={"name": portfolio.name},
            )
        return success(
            "Portfolio created",
            PortfolioReadSerializer(portfolio).data,
            status.HTTP_201_CREATED,
        )


class PortfolioDetailView(PortfolioAPIView):
    def get(self, request: Request, portfolio_id: uuid.UUID) -> Response:
        portfolio = self.get_owned_portfolio(request, portfolio_id)
        if portfolio is None:
            return portfolio_not_found()
        return success("Portfolio retrieved", PortfolioReadSerializer(portfolio).data)

    def patch(self, request: Request, portfolio_id: uuid.UUID) -> Response:
        portfolio = self.get_owned_portfolio(request, portfolio_id)
        if portfolio is None:
            return portfolio_not_found()

        serializer = PortfolioWriteSerializer(instance=portfolio, data=request.data, partial=True)
        if not serializer.is_valid():
            return failure("Validation failed", serializer.errors)

        validated_data = cast(dict[str, Any], serializer.validated_data)
        changed_fields = sorted(validated_data)
        with transaction.atomic():
            portfolio = update_portfolio(portfolio, validated_data)
            record_audit_event(
                actor=request.user,
                action=AuditAction.PORTFOLIO_UPDATED,
                resource_type=AuditResourceType.PORTFOLIO,
                resource_id=portfolio.pk,
                status=AuditStatus.SUCCESS,
                request=request,
                metadata={"changed_fields": changed_fields},
            )
        return success("Portfolio updated", PortfolioReadSerializer(portfolio).data)

    def delete(self, request: Request, portfolio_id: uuid.UUID) -> Response:
        portfolio = self.get_owned_portfolio(request, portfolio_id)
        if portfolio is None:
            return portfolio_not_found()

        portfolio_id = portfolio.pk
        portfolio_name = portfolio.name
        with transaction.atomic():
            delete_portfolio(portfolio)
            record_audit_event(
                actor=request.user,
                action=AuditAction.PORTFOLIO_DELETED,
                resource_type=AuditResourceType.PORTFOLIO,
                resource_id=portfolio_id,
                status=AuditStatus.SUCCESS,
                request=request,
                metadata={"name": portfolio_name},
            )
        return Response(status=status.HTTP_204_NO_CONTENT)


class PortfolioInstrumentListCreateView(PortfolioAPIView):
    def get(self, request: Request, portfolio_id: uuid.UUID) -> Response:
        portfolio = self.get_owned_portfolio(request, portfolio_id)
        if portfolio is None:
            return portfolio_not_found()

        queryset = list_portfolio_instruments(portfolio)
        paginator = PortfolioPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        serializer = PortfolioInstrumentReadSerializer(page, many=True)
        paginated = paginator.get_paginated_response(serializer.data).data
        return success("Portfolio instruments retrieved", paginated)

    def post(self, request: Request, portfolio_id: uuid.UUID) -> Response:
        portfolio = self.get_owned_portfolio(request, portfolio_id)
        if portfolio is None:
            return portfolio_not_found()
        serializer = PortfolioInstrumentWriteSerializer(data=request.data)
        if not serializer.is_valid():
            return failure("Validation failed", serializer.errors)
        return _create_membership(request, portfolio, serializer.validated_data)


def _membership_fields(data: object) -> tuple[Instrument, Exchange, str, bool]:
    if not isinstance(data, Mapping):
        raise DjangoValidationError({"instrument_id": ["Invalid instrument."]})
    instrument = data.get("instrument")
    exchange = data.get("exchange")
    alias = data.get("alias", "")
    is_active = data.get("is_active", True)
    if not isinstance(instrument, Instrument) or not isinstance(exchange, Exchange):
        raise DjangoValidationError({"instrument_id": ["Invalid instrument."]})
    if not isinstance(alias, str) or not isinstance(is_active, bool):
        raise DjangoValidationError({"alias": ["Invalid membership fields."]})
    return instrument, exchange, alias, is_active


def _create_membership(request: Request, portfolio: Portfolio, data: object) -> Response:
    try:
        instrument, exchange, alias, is_active = _membership_fields(data)
        with transaction.atomic():
            portfolio_instrument = add_portfolio_instrument(
                portfolio,
                instrument=instrument,
                exchange=exchange,
                alias=alias,
                is_active=is_active,
            )
            record_audit_event(
                actor=request.user,
                action=AuditAction.PORTFOLIO_INSTRUMENT_ADDED,
                resource_type=AuditResourceType.PORTFOLIO_INSTRUMENT,
                resource_id=portfolio_instrument.pk,
                status=AuditStatus.SUCCESS,
                request=request,
                metadata={
                    "portfolio_id": str(portfolio.pk),
                    "instrument_symbol": portfolio_instrument.instrument.symbol,
                    "exchange_code": portfolio_instrument.exchange.code,
                },
            )
    except DuplicatePortfolioInstrumentError:
        return failure(
            "Portfolio instrument already exists",
            {"instrument_id": ["This instrument is already in the portfolio."]},
            status.HTTP_409_CONFLICT,
        )
    except DjangoValidationError as exc:
        return failure("Validation failed", exc.message_dict)
    return success(
        "Portfolio instrument created",
        PortfolioInstrumentReadSerializer(portfolio_instrument).data,
        status.HTTP_201_CREATED,
    )


class PortfolioInstrumentDetailView(PortfolioAPIView):
    def delete(
        self, request: Request, portfolio_id: uuid.UUID, portfolio_instrument_id: uuid.UUID
    ) -> Response:
        portfolio = self.get_owned_portfolio(request, portfolio_id)
        if portfolio is None:
            return portfolio_not_found()

        portfolio_instrument = get_portfolio_instrument(portfolio, portfolio_instrument_id)
        if portfolio_instrument is None:
            return failure(
                "Portfolio instrument not found",
                {"detail": ["Not found."]},
                status.HTTP_404_NOT_FOUND,
            )

        membership_id = portfolio_instrument.pk
        metadata = {
            "portfolio_id": str(portfolio.pk),
            "instrument_symbol": portfolio_instrument.instrument.symbol,
            "exchange_code": portfolio_instrument.exchange.code,
        }
        with transaction.atomic():
            remove_portfolio_instrument(portfolio_instrument)
            record_audit_event(
                actor=request.user,
                action=AuditAction.PORTFOLIO_INSTRUMENT_REMOVED,
                resource_type=AuditResourceType.PORTFOLIO_INSTRUMENT,
                resource_id=membership_id,
                status=AuditStatus.SUCCESS,
                request=request,
                metadata=metadata,
            )
        return Response(status=status.HTTP_204_NO_CONTENT)


def _require_text(item: dict[str, object], key: str) -> str:
    value = item.get(key)
    if not isinstance(value, str):
        raise BulkAttachError({"instruments": ["Invalid instrument metadata."]})
    return value


def _bulk_inputs(items: object) -> list[BulkInstrumentInput]:
    if not isinstance(items, list):
        raise BulkAttachError({"instruments": ["A list of instruments is required."]})
    return [_one_bulk_input(item) for item in items]


def _one_bulk_input(item: object) -> BulkInstrumentInput:
    if not isinstance(item, dict):
        raise BulkAttachError({"instruments": ["Invalid instrument entry."]})
    exchange = item.get("exchange_code")
    is_active = item.get("is_active", True)
    if not isinstance(exchange, Exchange) or not isinstance(is_active, bool):
        raise BulkAttachError({"instruments": ["Invalid instrument entry."]})
    return BulkInstrumentInput(
        exchange=exchange,
        symbol=_require_text(item, "symbol"),
        name=_require_text(item, "name"),
        asset_class=_require_text(item, "asset_class"),
        currency=_require_text(item, "currency"),
        alias=_require_text(item, "alias"),
        is_active=is_active,
    )


def _audit_created_instrument(request: Request, line: BulkLine) -> None:
    if not line.instrument_created:
        return
    record_audit_event(
        actor=request.user,
        action=AuditAction.INSTRUMENT_CREATED,
        resource_type=AuditResourceType.INSTRUMENT,
        resource_id=line.instrument_id,
        status=AuditStatus.SUCCESS,
        request=request,
        metadata={"symbol": line.symbol, "exchange_code": line.exchange_code},
    )


def _audit_membership(request: Request, result: BulkAttachResult, line: BulkLine) -> None:
    if not line.membership_created:
        return
    record_audit_event(
        actor=request.user,
        action=AuditAction.PORTFOLIO_INSTRUMENT_ADDED,
        resource_type=AuditResourceType.PORTFOLIO_INSTRUMENT,
        resource_id=line.membership_id,
        status=AuditStatus.SUCCESS,
        request=request,
        metadata={
            "portfolio_id": str(result.portfolio_id),
            "instrument_symbol": line.symbol,
            "exchange_code": line.exchange_code,
        },
    )


def _audit_bulk(request: Request, result: BulkAttachResult) -> None:
    for line in result.lines:
        _audit_created_instrument(request, line)
        _audit_membership(request, result, line)


def _bulk_payload(result: BulkAttachResult, *, include_name: bool = False) -> dict[str, object]:
    payload: dict[str, object] = {
        "portfolio_id": str(result.portfolio_id),
        "summary": result.summary,
        "instruments": [
            {"symbol": line.symbol, "exchange": line.exchange_code, "status": line.status}
            for line in result.lines
        ],
    }
    if include_name:
        portfolio = Portfolio.objects.filter(pk=result.portfolio_id).only("name").first()
        payload["name"] = "" if portfolio is None else portfolio.name
    return payload


def _validated_name(validated: object) -> str:
    if not isinstance(validated, dict):
        return ""
    name = validated.get("name")
    if not isinstance(name, str):
        return ""
    return name


def _name_lookup_error(request: Request, name: str, found: list[Portfolio]) -> Response | None:
    try:
        portfolio = get_user_portfolio_by_name(request.user, name)
    except AmbiguousPortfolioNameError:
        return failure(
            "Portfolio name is ambiguous",
            {"name": ["More than one portfolio uses this name. Use the portfolio id."]},
            status.HTTP_409_CONFLICT,
        )
    if portfolio is None:
        return portfolio_not_found()
    found.append(portfolio)
    return None


def _portfolio_by_name_response(request: Request, name: str) -> Response:
    found: list[Portfolio] = []
    error = _name_lookup_error(request, name, found)
    if error is not None:
        return error
    return success("Portfolio retrieved", PortfolioReadSerializer(found[0]).data)


class PortfolioInstrumentBulkView(PortfolioAPIView):
    """Create missing shared instruments and attach them in one transaction.

    Every item is validated before writes. One invalid item rolls the request
    back. An instrument that is already attached is reported and left unchanged.
    """

    def post(self, request: Request, portfolio_id: uuid.UUID) -> Response:
        portfolio = self.get_owned_portfolio(request, portfolio_id)
        if portfolio is None:
            return portfolio_not_found()
        serializer = BulkAttachSerializer(data=request.data)
        if not serializer.is_valid():
            return failure("Validation failed", serializer.errors)
        return _execute_bulk(request, portfolio, serializer.validated_data)


class PortfolioByNameView(PortfolioAPIView):
    """Retrieve one owned portfolio by its name."""

    def get(self, request: Request) -> Response:
        query = PortfolioNameQuerySerializer(data=request.query_params)
        if not query.is_valid():
            return failure("Validation failed", query.errors)
        return _portfolio_by_name_response(request, _validated_name(query.validated_data))


class PortfolioInstrumentBulkByNameView(PortfolioAPIView):
    """Attach instruments to an owned portfolio addressed by name."""

    def post(self, request: Request) -> Response:
        serializer = BulkAttachByNameSerializer(data=request.data)
        if not serializer.is_valid():
            return failure("Validation failed", serializer.errors)
        return _bulk_by_resolved_name(request, serializer.validated_data)


def _bulk_by_resolved_name(request: Request, validated: object) -> Response:
    found: list[Portfolio] = []
    error = _name_lookup_error(request, _validated_name(validated), found)
    if error is not None:
        return error
    return _execute_bulk(request, found[0], validated, include_name=True)


def _bulk_request(validated: object) -> tuple[bool, list[BulkInstrumentInput]]:
    if not isinstance(validated, dict):
        raise BulkAttachError({"instruments": ["Invalid request."]})
    create_if_missing = validated.get("create_if_missing")
    if not isinstance(create_if_missing, bool):
        raise BulkAttachError({"create_if_missing": ["This field is required."]})
    return create_if_missing, _bulk_inputs(validated.get("instruments"))


def _execute_bulk(
    request: Request, portfolio: Portfolio, validated: object, *, include_name: bool = False
) -> Response:
    try:
        create_if_missing, items = _bulk_request(validated)
        with transaction.atomic():
            result = bulk_attach_instruments(portfolio, items, create_if_missing=create_if_missing)
            _audit_bulk(request, result)
    except BulkAttachError as exc:
        return failure("Validation failed", exc.errors)
    except IntegrityError:
        return failure(
            "Portfolio instruments could not be saved",
            {
                "instruments": [
                    "The operation conflicted with existing records and was rolled back."
                ]
            },
            status.HTTP_409_CONFLICT,
        )
    attached = result.summary["instruments_attached"]
    status_code = status.HTTP_201_CREATED if attached else status.HTTP_200_OK
    message = "Portfolio instruments attached" if attached else "Portfolio instruments unchanged"
    return success(message, _bulk_payload(result, include_name=include_name), status_code)
