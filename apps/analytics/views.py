"""HTTP endpoints for candles, daily returns, and aggregation runs."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any
from uuid import UUID

from django.db.models import QuerySet
from django.http import JsonResponse
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.analytics.models import AggregationTrigger
from apps.analytics.scope import failure, resolve_scope
from apps.analytics.selectors import get_aggregation_run, list_candles, list_daily_ohlcv
from apps.analytics.serializers import (
    AggregationCreateSerializer,
    AggregationRunSerializer,
    CandleFilterSerializer,
    CandleReadSerializer,
    ReturnsFilterSerializer,
)
from apps.analytics.services.dispatch import (
    AggregationRequest,
    create_aggregation_run,
    schedule_run_on_commit,
)
from apps.analytics.services.metrics_calculator import build_returns_series
from apps.common.pagination import StandardResultsPagination
from apps.exchanges.models import Exchange
from apps.instruments.models import Instrument


def _mapping(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): item for key, item in value.items()}


def success(
    message: str,
    data: Any = None,
    status_code: int = status.HTTP_200_OK,
) -> Response:
    return Response(
        {"success": True, "message": message, "data": data, "errors": None},
        status=status_code,
    )


def _paginated(
    view: APIView,
    request: Request,
    queryset: QuerySet[Any],
    serializer_class: type,
    message: str,
) -> Response:
    paginator = StandardResultsPagination()
    page = paginator.paginate_queryset(queryset, request, view=view)
    serializer = serializer_class(page, many=True)
    return success(message, paginator.get_paginated_response(serializer.data).data)


class CandleListView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_scope = "analytics_read"

    def get(self, request: Request) -> Response:
        filters = CandleFilterSerializer(data=request.query_params)
        if not filters.is_valid():
            return failure("Invalid candle filters", filters.errors)
        data = _mapping(filters.validated_data)
        instrument_ids, exchange_ids, error = resolve_scope(request.user, data)
        if error is not None:
            return error
        queryset = list_candles(
            instrument_ids=instrument_ids,
            exchange_ids=exchange_ids,
            timeframe=data.get("timeframe") if isinstance(data.get("timeframe"), str) else None,
            range_start=data.get("start") if isinstance(data.get("start"), datetime) else None,
            range_end=data.get("end") if isinstance(data.get("end"), datetime) else None,
        )
        return _paginated(self, request, queryset, CandleReadSerializer, "Candles retrieved")


def _returns_json(
    *,
    success_flag: bool,
    message: str,
    data: Any = None,
    errors: Any = None,
    status_code: int = status.HTTP_200_OK,
) -> JsonResponse:
    return JsonResponse(
        {
            "success": success_flag,
            "message": message,
            "data": data,
            "errors": errors,
        },
        status=status_code,
    )


def _optional_date(value: object):
    from datetime import date

    return value if isinstance(value, date) else None


def _returns_from_scope(request: Request, data: dict[str, Any]) -> JsonResponse:
    instrument_ids, _exchange_ids, error = resolve_scope(request.user, data)
    if error is not None:
        payload = error.data if isinstance(error.data, dict) else {}
        return _returns_json(
            success_flag=False,
            message=str(payload.get("message", "Request failed")),
            errors=payload.get("errors"),
            status_code=int(error.status_code),
        )
    ids = instrument_ids or []
    start_date = _optional_date(data.get("start_date"))
    end_date = _optional_date(data.get("end_date"))
    series = build_returns_series(
        list(list_daily_ohlcv(instrument_ids=ids, start_date=start_date, end_date=end_date)),
        start_date=start_date,
        end_date=end_date,
        return_type=str(data.get("return_type") or "simple"),
    )
    return _returns_json(
        success_flag=True,
        message="Daily returns retrieved",
        data={"count": len(series), "results": series},
    )


class ReturnsListView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_scope = "analytics_read"

    def get(self, request: Request) -> JsonResponse:
        filters = ReturnsFilterSerializer(data=request.query_params)
        if not filters.is_valid():
            return _returns_json(
                success_flag=False,
                message="Invalid return filters",
                errors=filters.errors,
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        return _returns_from_scope(request, _mapping(filters.validated_data))


def _as_optional_int(value: object) -> int | None:
    return value if isinstance(value, int) else None


def _as_optional_str(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _resolve_exchange(exchange_id: int | None, exchange_code: str | None) -> Exchange | None:
    from apps.analytics.selectors import resolve_exchange_ids

    ids = resolve_exchange_ids(exchange_id=exchange_id, exchange_code=exchange_code)
    if not ids:
        return None
    return Exchange.objects.filter(pk=ids[0]).first()


def _resolve_manual_instrument(data: dict[str, Any], exchange: Exchange) -> Instrument | None:
    from apps.analytics.selectors import resolve_instrument_ids_by_keys

    instrument_id = _as_optional_int(data.get("instrument_id"))
    if instrument_id is not None:
        return Instrument.objects.filter(
            pk=instrument_id, is_active=True, exchange=exchange
        ).first()
    ids = resolve_instrument_ids_by_keys(
        instrument_symbol=_as_optional_str(data.get("instrument_symbol")),
        instrument_name=_as_optional_str(data.get("instrument_name")),
        exchange_ids=[exchange.pk],
    )
    if not ids:
        return None
    return Instrument.objects.filter(pk=ids[0], is_active=True).first()


def _manual_lookup_error(
    exchange: Exchange | None,
    instrument: Instrument | None,
) -> Response | None:
    if exchange is None:
        return failure(
            "Exchange not found",
            {"exchange_id": ["Unknown exchange."]},
            status.HTTP_404_NOT_FOUND,
        )
    if instrument is None:
        return failure(
            "Instrument not found",
            {"instrument_id": ["Unknown or inactive instrument for the requested exchange."]},
            status.HTTP_404_NOT_FOUND,
        )
    return None


def _validate_manual_targets(
    data: dict[str, Any],
) -> tuple[Instrument, Exchange, datetime, datetime] | Response:
    exchange = _resolve_exchange(
        _as_optional_int(data.get("exchange_id")),
        _as_optional_str(data.get("exchange_code")),
    )
    instrument = None if exchange is None else _resolve_manual_instrument(data, exchange)
    lookup_error = _manual_lookup_error(exchange, instrument)
    if lookup_error is not None:
        return lookup_error
    range_start = data.get("range_start")
    range_end = data.get("range_end")
    if (
        exchange is None
        or instrument is None
        or not isinstance(range_start, datetime)
        or not isinstance(range_end, datetime)
    ):
        return failure("Invalid aggregation request", {"detail": ["Invalid date range."]})
    return instrument, exchange, range_start, range_end


def _create_manual_run(request: Request, data: dict[str, Any]) -> Response:
    validated = _validate_manual_targets(data)
    if isinstance(validated, Response):
        return validated
    instrument, exchange, range_start, range_end = validated
    run = create_aggregation_run(
        AggregationRequest(
            trigger=AggregationTrigger.MANUAL,
            range_start=range_start,
            range_end=range_end,
            instrument_ids=[instrument.pk],
            exchange_ids=[exchange.pk],
            timeframes=data.get("timeframes"),
            requested_by=request.user,
        )
    )
    schedule_run_on_commit(run.pk)
    return success(
        "Aggregation accepted",
        AggregationRunSerializer(run).data,
        status_code=status.HTTP_202_ACCEPTED,
    )


class AggregationCreateView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_scope = "analytics_aggregate"

    def post(self, request: Request) -> Response:
        serializer = AggregationCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return failure("Invalid aggregation request", serializer.errors)
        return _create_manual_run(request, _mapping(serializer.validated_data))


class AggregationDetailView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_scope = "analytics_read"

    def get(self, request: Request, run_id: UUID) -> Response:
        run = get_aggregation_run(run_id)
        if run is None:
            return failure(
                "Aggregation run not found",
                {"detail": ["Not found."]},
                status.HTTP_404_NOT_FOUND,
            )
        actor = run.requested_by
        foreign = actor is not None and actor.pk != request.user.pk
        if foreign and not getattr(request.user, "is_staff", False):
            return failure(
                "Aggregation run not found",
                {"detail": ["Not found."]},
                status.HTTP_404_NOT_FOUND,
            )
        return success("Aggregation run retrieved", AggregationRunSerializer(run).data)
