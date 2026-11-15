"""HTTP endpoints for candles, daily returns, trend indicators, and aggregation runs."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

from django.contrib.auth.base_user import AbstractBaseUser
from django.db.models import Model, QuerySet
from django.http import JsonResponse
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.analytics.models import AggregationTrigger
from apps.analytics.scope import failure, resolve_scope
from apps.analytics.selectors import (
    IndicatorCandleQuery,
    get_aggregation_run,
    list_candles,
    list_daily_ohlcv,
)
from apps.analytics.serializers import (
    AggregationCreateSerializer,
    AggregationRunSerializer,
    CandleFilterSerializer,
    CandleReadSerializer,
    MacdIndicatorFilterSerializer,
    ReturnsFilterSerializer,
    WindowIndicatorFilterSerializer,
)
from apps.analytics.services.dispatch import (
    AggregationRequest,
    create_aggregation_run,
    schedule_run_on_commit,
)
from apps.analytics.services.metrics_calculator import (
    IndicatorSeries,
    MacdSeries,
    build_returns_series,
)
from apps.analytics.services.trend import (
    IndicatorLimitError,
    MacdIndicatorQuery,
    WindowIndicatorQuery,
    build_ema_series,
    build_macd_series,
    build_sma_series,
)
from apps.common.pagination import StandardResultsPagination
from apps.exchanges.models import Exchange
from apps.instruments.models import Instrument

logger = logging.getLogger(__name__)


def _mapping(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): item for key, item in value.items()}


def _user(request: Request) -> AbstractBaseUser:
    if isinstance(request.user, AbstractBaseUser):
        return request.user
    raise PermissionError("Authentication required.")


def success(
    message: str,
    data: object = None,
    status_code: int = status.HTTP_200_OK,
) -> Response:
    return Response(
        {"success": True, "message": message, "data": data, "errors": None},
        status=status_code,
    )


def _paginated[M: Model](
    view: APIView,
    request: Request,
    queryset: QuerySet[M],
    serializer_class: type[serializers.BaseSerializer[Model]],
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
        instrument_ids, exchange_ids, error = resolve_scope(_user(request), data)
        if error is not None:
            return error
        timeframe = data.get("timeframe")
        range_start = data.get("start")
        range_end = data.get("end")
        queryset = list_candles(
            instrument_ids=instrument_ids,
            exchange_ids=exchange_ids,
            timeframe=timeframe if isinstance(timeframe, str) else None,
            range_start=range_start if isinstance(range_start, datetime) else None,
            range_end=range_end if isinstance(range_end, datetime) else None,
        )
        return _paginated(self, request, queryset, CandleReadSerializer, "Candles retrieved")


def _returns_json(
    *,
    success_flag: bool,
    message: str,
    data: object = None,
    errors: object = None,
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


def _optional_date(value: object) -> date | None:
    return value if isinstance(value, date) else None


def _returns_from_scope(request: Request, data: dict[str, object]) -> JsonResponse:
    instrument_ids, _exchange_ids, error = resolve_scope(_user(request), data)
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


@dataclass(frozen=True)
class _Scope:
    instrument_ids: list[int]
    exchange_ids: list[int] | None


def _int_field(data: dict[str, object], key: str) -> int | None:
    value = data.get(key)
    if isinstance(value, int):
        return value
    return None


def _text_field(data: dict[str, object], key: str) -> str | None:
    value = data.get(key)
    if isinstance(value, str) and value:
        return value
    return None


def _dt_field(data: dict[str, object], key: str) -> datetime | None:
    value = data.get(key)
    if isinstance(value, datetime):
        return value
    return None


def _scoped_ids(request: Request, data: dict[str, object]) -> _Scope | Response:
    instrument_ids, exchange_ids, error = resolve_scope(_user(request), data)
    if error is not None:
        return error
    return _Scope(instrument_ids or [], exchange_ids)


def _candle_query(data: dict[str, object], scope: _Scope, timeframe: str) -> IndicatorCandleQuery:
    return IndicatorCandleQuery(
        instrument_ids=scope.instrument_ids,
        exchange_ids=scope.exchange_ids,
        timeframe=timeframe,
        range_start=_dt_field(data, "start"),
        range_end=_dt_field(data, "end"),
        include_incomplete=data.get("include_incomplete") is True,
    )


def _window_query(data: dict[str, object], scope: _Scope) -> WindowIndicatorQuery | None:
    timeframe = _text_field(data, "timeframe")
    window = _int_field(data, "window")
    if timeframe is None or window is None:
        return None
    return WindowIndicatorQuery(candles=_candle_query(data, scope, timeframe), window=window)


def _log_indicator(request: Request, metric: str, timeframe: str, series_count: int) -> None:
    logger.info(
        "Computed trend indicator.",
        extra={
            "user_id": request.user.pk,
            "metric": metric,
            "timeframe": timeframe,
            "series_count": series_count,
        },
    )


def _window_outcome(
    data: dict[str, object],
    scope: _Scope,
    metric: str,
) -> Response | tuple[WindowIndicatorQuery, list[IndicatorSeries]]:
    query = _window_query(data, scope)
    if query is None:
        return failure(f"Invalid {metric} filters", {"detail": ["Invalid indicator parameters."]})
    outcome = build_sma_series(query) if metric == "sma" else build_ema_series(query)
    if isinstance(outcome, IndicatorLimitError):
        return failure(outcome.message, outcome.errors)
    return query, outcome


def _prepare_window(
    request: Request,
    metric: str,
) -> Response | tuple[WindowIndicatorQuery, list[IndicatorSeries]]:
    filters = WindowIndicatorFilterSerializer(data=request.query_params)
    if not filters.is_valid():
        return failure(f"Invalid {metric} filters", filters.errors)
    scope = _scoped_ids(request, _mapping(filters.validated_data))
    if isinstance(scope, Response):
        return scope
    return _window_outcome(_mapping(filters.validated_data), scope, metric)


def window_indicator_response(request: Request, *, metric: str, message: str) -> Response:
    prepared = _prepare_window(request, metric)
    if isinstance(prepared, Response):
        return prepared
    query, outcome = prepared
    _log_indicator(request, metric, query.candles.timeframe, len(outcome))
    return success(message, {"count": len(outcome), "results": outcome})


def _macd_query(data: dict[str, object], scope: _Scope) -> MacdIndicatorQuery | None:
    timeframe = _text_field(data, "timeframe")
    fast = _int_field(data, "fast_period")
    slow = _int_field(data, "slow_period")
    signal = _int_field(data, "signal_period")
    if timeframe is None or fast is None or slow is None or signal is None:
        return None
    return MacdIndicatorQuery(
        candles=_candle_query(data, scope, timeframe),
        fast_period=fast,
        slow_period=slow,
        signal_period=signal,
    )


def _macd_outcome(
    data: dict[str, object],
    scope: _Scope,
) -> Response | tuple[MacdIndicatorQuery, list[MacdSeries]]:
    query = _macd_query(data, scope)
    if query is None:
        return failure("Invalid macd filters", {"detail": ["Invalid indicator parameters."]})
    outcome = build_macd_series(query)
    if isinstance(outcome, IndicatorLimitError):
        return failure(outcome.message, outcome.errors)
    return query, outcome


def _prepare_macd(
    request: Request,
) -> Response | tuple[MacdIndicatorQuery, list[MacdSeries]]:
    filters = MacdIndicatorFilterSerializer(data=request.query_params)
    if not filters.is_valid():
        return failure("Invalid macd filters", filters.errors)
    data = _mapping(filters.validated_data)
    scope = _scoped_ids(request, data)
    if isinstance(scope, Response):
        return scope
    return _macd_outcome(data, scope)


def macd_indicator_response(request: Request) -> Response:
    prepared = _prepare_macd(request)
    if isinstance(prepared, Response):
        return prepared
    query, outcome = prepared
    _log_indicator(request, "macd", query.candles.timeframe, len(outcome))
    return success("MACD retrieved", {"count": len(outcome), "results": outcome})


class SmaListView(APIView):
    """SMA of trade-derived candle closes. Ordinary GETs are not audit events."""

    permission_classes = [IsAuthenticated]
    throttle_scope = "analytics_read"

    @extend_schema(parameters=[WindowIndicatorFilterSerializer], tags=["analytics"])
    def get(self, request: Request) -> Response:
        return window_indicator_response(request, metric="sma", message="SMA retrieved")


class EmaListView(APIView):
    """EMA of trade-derived candle closes, seeded with the first full-window SMA."""

    permission_classes = [IsAuthenticated]
    throttle_scope = "analytics_read"

    @extend_schema(parameters=[WindowIndicatorFilterSerializer], tags=["analytics"])
    def get(self, request: Request) -> Response:
        return window_indicator_response(request, metric="ema", message="EMA retrieved")


class MacdListView(APIView):
    """MACD, signal, and histogram from trade-derived candle closes."""

    permission_classes = [IsAuthenticated]
    throttle_scope = "analytics_read"

    @extend_schema(parameters=[MacdIndicatorFilterSerializer], tags=["analytics"])
    def get(self, request: Request) -> Response:
        return macd_indicator_response(request)


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


def _resolve_manual_instrument(data: dict[str, object], exchange: Exchange) -> Instrument | None:
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
    data: dict[str, object],
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


def _string_list(value: object) -> list[str] | None:
    if not isinstance(value, list):
        return None
    names = [item for item in value if isinstance(item, str)]
    if len(names) != len(value):
        return None
    return names


def _create_manual_run(request: Request, data: dict[str, object]) -> Response:
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
            timeframes=_string_list(data.get("timeframes")),
            requested_by=_user(request),
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
