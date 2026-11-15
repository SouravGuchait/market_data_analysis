"""HTTP endpoints for candles, returns, trend/volatility indicators, and aggregation."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
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
    AtrIndicatorFilterSerializer,
    BollingerIndicatorFilterSerializer,
    CandleFilterSerializer,
    CandleReadSerializer,
    DrawdownFilterSerializer,
    MacdIndicatorFilterSerializer,
    OverviewFilterSerializer,
    ReturnsFilterSerializer,
    RsiIndicatorFilterSerializer,
    StochasticIndicatorFilterSerializer,
    WindowIndicatorFilterSerializer,
)
from apps.analytics.services.dispatch import (
    AggregationRequest,
    create_aggregation_run,
    schedule_run_on_commit,
)
from apps.analytics.services.metrics_calculator import (
    build_drawdown_series,
    build_returns_series,
)
from apps.analytics.services.momentum import (
    RsiIndicatorQuery,
    StochasticIndicatorQuery,
    build_rsi_series,
    build_stochastic_series,
)
from apps.analytics.services.overview import OverviewRequest, build_overview
from apps.analytics.services.trend import (
    IndicatorLimitError,
    MacdIndicatorQuery,
    WindowIndicatorQuery,
    build_ema_series,
    build_macd_series,
    build_sma_series,
)
from apps.analytics.services.volatility import (
    AtrIndicatorQuery,
    BollingerIndicatorQuery,
    build_atr_series,
    build_bollinger_series,
)
from apps.common.cache_service import (
    cache_load_dict,
    cache_set,
    get_or_build,
    is_recent_range,
    market_key,
    ttl_candles,
    ttl_indicators,
    ttl_returns,
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


def api_json(
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


def success(
    message: str,
    data: object = None,
    status_code: int = status.HTTP_200_OK,
) -> JsonResponse:
    return api_json(
        success_flag=True,
        message=message,
        data=data,
        status_code=status_code,
    )


def _paginated[M: Model](
    view: APIView,
    request: Request,
    queryset: QuerySet[M],
    serializer_class: type[serializers.BaseSerializer[Model]],
    message: str,
) -> dict[str, object]:
    paginator = StandardResultsPagination()
    page = paginator.paginate_queryset(queryset, request, view=view)
    serializer = serializer_class(page, many=True)
    page_data = paginator.get_paginated_response(serializer.data).data
    return {
        "success": True,
        "message": message,
        "data": page_data,
        "errors": None,
    }


def _page_parts(request: Request) -> dict[str, object]:
    return {
        "page": request.query_params.get("page", "1"),
        "page_size": request.query_params.get("page_size", ""),
    }


def _scope_parts(
    instrument_ids: list[int] | None,
    exchange_ids: list[int] | None,
    data: dict[str, object],
) -> dict[str, object]:
    parts: dict[str, object] = {
        "instrument_ids": instrument_ids or [],
        "exchange_ids": exchange_ids,
    }
    if data.get("portfolio_id") is not None:
        parts["portfolio_id"] = str(data["portfolio_id"])
        parts["user_id"] = data.get("_user_id")
    return parts


class CandleListView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_scope = "analytics_read"

    def get(self, request: Request) -> JsonResponse:
        filters = CandleFilterSerializer(data=request.query_params)
        if not filters.is_valid():
            return failure("Invalid candle filters", filters.errors)
        data = _mapping(filters.validated_data)
        user = _user(request)
        instrument_ids, exchange_ids, error = resolve_scope(user, data)
        if error is not None:
            return error
        timeframe = data.get("timeframe")
        range_start = data.get("start")
        range_end = data.get("end")
        end_dt = range_end if isinstance(range_end, datetime) else None
        data["_user_id"] = user.pk
        parts = {
            **_scope_parts(instrument_ids, exchange_ids, data),
            **_page_parts(request),
            "timeframe": timeframe,
            "start": range_start,
            "end": range_end,
        }
        key = market_key("candles", parts)
        ttl = ttl_candles(recent=is_recent_range(end_dt))

        def build() -> dict[str, object]:
            queryset = list_candles(
                instrument_ids=instrument_ids,
                exchange_ids=exchange_ids,
                timeframe=timeframe if isinstance(timeframe, str) else None,
                range_start=range_start if isinstance(range_start, datetime) else None,
                range_end=end_dt,
            )
            return _paginated(self, request, queryset, CandleReadSerializer, "Candles retrieved")

        return JsonResponse(get_or_build(key, ttl, build))


def _optional_date(value: object) -> date | None:
    return value if isinstance(value, date) else None


def _returns_from_scope(request: Request, data: dict[str, object]) -> JsonResponse:
    user = _user(request)
    instrument_ids, exchange_ids, error = resolve_scope(user, data)
    if error is not None:
        return error
    ids = instrument_ids or []
    start_date = _optional_date(data.get("start_date"))
    end_date = _optional_date(data.get("end_date"))
    data["_user_id"] = user.pk
    parts = {
        **_scope_parts(ids, exchange_ids, data),
        "start_date": start_date,
        "end_date": end_date,
        "return_type": str(data.get("return_type") or "simple"),
    }
    key = market_key("returns", parts)

    def build() -> dict[str, object]:
        series = build_returns_series(
            list(list_daily_ohlcv(instrument_ids=ids, start_date=start_date, end_date=end_date)),
            start_date=start_date,
            end_date=end_date,
            return_type=str(data.get("return_type") or "simple"),
        )
        return {
            "success": True,
            "message": "Daily returns retrieved",
            "data": {"count": len(series), "results": series},
            "errors": None,
        }

    return JsonResponse(get_or_build(key, ttl_returns(), build))


class ReturnsListView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_scope = "analytics_read"

    def get(self, request: Request) -> JsonResponse:
        filters = ReturnsFilterSerializer(data=request.query_params)
        if not filters.is_valid():
            return api_json(
                success_flag=False,
                message="Invalid return filters",
                errors=filters.errors,
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        return _returns_from_scope(request, _mapping(filters.validated_data))


def _drawdown_from_scope(request: Request, data: dict[str, object]) -> JsonResponse:
    user = _user(request)
    instrument_ids, exchange_ids, error = resolve_scope(user, data)
    if error is not None:
        return error
    start_date = _optional_date(data.get("start_date"))
    end_date = _optional_date(data.get("end_date"))
    ids = instrument_ids or []
    data["_user_id"] = user.pk
    parts = {
        **_scope_parts(ids, exchange_ids, data),
        "start_date": start_date,
        "end_date": end_date,
    }
    key = market_key("drawdown", parts)

    def build() -> dict[str, object]:
        series = build_drawdown_series(
            list(list_daily_ohlcv(instrument_ids=ids, start_date=start_date, end_date=end_date)),
            start_date=start_date,
            end_date=end_date,
        )
        return {
            "success": True,
            "message": "Drawdown retrieved",
            "data": {"count": len(series), "results": series},
            "errors": None,
        }

    return JsonResponse(get_or_build(key, ttl_returns(), build))


class DrawdownListView(APIView):
    """Running and period maximum drawdown from DailyOHLCV. Ordinary GETs are not audit events."""

    permission_classes = [IsAuthenticated]
    throttle_scope = "analytics_read"

    @extend_schema(parameters=[DrawdownFilterSerializer], tags=["analytics"])
    def get(self, request: Request) -> JsonResponse:
        filters = DrawdownFilterSerializer(data=request.query_params)
        if not filters.is_valid():
            return failure("Invalid drawdown filters", filters.errors)
        return _drawdown_from_scope(request, _mapping(filters.validated_data))


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


def _scoped_ids(request: Request, data: dict[str, object]) -> _Scope | JsonResponse:
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


@dataclass(frozen=True)
class _IndicatorCacheSpec:
    key: str
    ttl: int
    metric: str
    message: str
    timeframe: str


def _indicator_cache_parts(
    request: Request,
    candles: IndicatorCandleQuery,
    params: dict[str, object],
) -> dict[str, object]:
    parts: dict[str, object] = {
        "instrument_ids": candles.instrument_ids,
        "exchange_ids": candles.exchange_ids,
        "timeframe": candles.timeframe,
        "start": candles.range_start,
        "end": candles.range_end,
        "include_incomplete": candles.include_incomplete,
        **params,
    }
    portfolio_id = request.query_params.get("portfolio_id")
    if portfolio_id:
        parts["portfolio_id"] = portfolio_id
        parts["user_id"] = request.user.pk
    return parts


def _indicator_spec(
    request: Request,
    candles: IndicatorCandleQuery,
    params: dict[str, object],
    message: str,
) -> _IndicatorCacheSpec:
    metric = str(params.get("metric", ""))
    key = market_key("indicators", _indicator_cache_parts(request, candles, params))
    ttl = ttl_indicators(recent=is_recent_range(candles.range_end))
    return _IndicatorCacheSpec(
        key=key, ttl=ttl, metric=metric, message=message, timeframe=candles.timeframe
    )


def _store_indicator(
    request: Request,
    spec: _IndicatorCacheSpec,
    results: list[object],
) -> JsonResponse:
    _log_indicator(request, spec.metric, spec.timeframe, len(results))
    payload: dict[str, object] = {
        "success": True,
        "message": spec.message,
        "data": {"count": len(results), "results": results},
        "errors": None,
    }
    cache_set(spec.key, payload, spec.ttl)
    return JsonResponse(payload)


def _indicator_hit(spec: _IndicatorCacheSpec) -> JsonResponse | None:
    hit = cache_load_dict(spec.key)
    if hit is None:
        return None
    return JsonResponse(hit)


def _scoped_filter_data(
    request: Request,
    serializer_cls: type[serializers.Serializer],
    message: str,
) -> JsonResponse | tuple[dict[str, object], _Scope]:
    filters = serializer_cls(data=request.query_params)
    if not filters.is_valid():
        return failure(message, filters.errors)
    data = _mapping(filters.validated_data)
    scope = _scoped_ids(request, data)
    if isinstance(scope, JsonResponse):
        return scope
    return data, scope


def _require_query[Q](query: Q | None, message: str) -> JsonResponse | Q:
    if query is None:
        return failure(message, {"detail": ["Invalid indicator parameters."]})
    return query


def _compute_or_fail(
    request: Request,
    spec: _IndicatorCacheSpec,
    outcome: IndicatorLimitError | Sequence[object],
) -> JsonResponse:
    if isinstance(outcome, IndicatorLimitError):
        return failure(outcome.message, outcome.errors)
    return _store_indicator(request, spec, list(outcome))


def _cached_compute(
    request: Request,
    spec: _IndicatorCacheSpec,
    compute: Callable[[], IndicatorLimitError | Sequence[object]],
) -> JsonResponse:
    cached = _indicator_hit(spec)
    if cached is not None:
        return cached
    return _compute_or_fail(request, spec, compute())


def _window_cached(
    request: Request,
    query: WindowIndicatorQuery,
    metric: str,
    message: str,
) -> JsonResponse:
    spec = _indicator_spec(
        request, query.candles, {"metric": metric, "window": query.window}, message
    )

    def compute() -> IndicatorLimitError | Sequence[object]:
        return build_sma_series(query) if metric == "sma" else build_ema_series(query)

    return _cached_compute(request, spec, compute)


def window_indicator_response(request: Request, *, metric: str, message: str) -> JsonResponse:
    err_msg = f"Invalid {metric} filters"
    prepared = _scoped_filter_data(request, WindowIndicatorFilterSerializer, err_msg)
    if isinstance(prepared, JsonResponse):
        return prepared
    data, scope = prepared
    query = _require_query(_window_query(data, scope), err_msg)
    if isinstance(query, JsonResponse):
        return query
    return _window_cached(request, query, metric, message)


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


def _macd_cached(request: Request, query: MacdIndicatorQuery) -> JsonResponse:
    params = {
        "metric": "macd",
        "fast_period": query.fast_period,
        "slow_period": query.slow_period,
        "signal_period": query.signal_period,
    }
    spec = _indicator_spec(request, query.candles, params, "MACD retrieved")
    return _cached_compute(request, spec, lambda: build_macd_series(query))


def macd_indicator_response(request: Request) -> JsonResponse:
    prepared = _scoped_filter_data(request, MacdIndicatorFilterSerializer, "Invalid macd filters")
    if isinstance(prepared, JsonResponse):
        return prepared
    data, scope = prepared
    query = _require_query(_macd_query(data, scope), "Invalid macd filters")
    if isinstance(query, JsonResponse):
        return query
    return _macd_cached(request, query)


class SmaListView(APIView):
    """SMA of trade-derived candle closes. Ordinary GETs are not audit events."""

    permission_classes = [IsAuthenticated]
    throttle_scope = "analytics_read"

    @extend_schema(parameters=[WindowIndicatorFilterSerializer], tags=["analytics"])
    def get(self, request: Request) -> JsonResponse:
        return window_indicator_response(request, metric="sma", message="SMA retrieved")


class EmaListView(APIView):
    """EMA of trade-derived candle closes, seeded with the first full-window SMA."""

    permission_classes = [IsAuthenticated]
    throttle_scope = "analytics_read"

    @extend_schema(parameters=[WindowIndicatorFilterSerializer], tags=["analytics"])
    def get(self, request: Request) -> JsonResponse:
        return window_indicator_response(request, metric="ema", message="EMA retrieved")


class MacdListView(APIView):
    """MACD, signal, and histogram from trade-derived candle closes."""

    permission_classes = [IsAuthenticated]
    throttle_scope = "analytics_read"

    @extend_schema(parameters=[MacdIndicatorFilterSerializer], tags=["analytics"])
    def get(self, request: Request) -> JsonResponse:
        return macd_indicator_response(request)


def _float_field(data: dict[str, object], key: str) -> float | None:
    value = data.get(key)
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    return None


def _bollinger_query(data: dict[str, object], scope: _Scope) -> BollingerIndicatorQuery | None:
    timeframe = _text_field(data, "timeframe")
    window = _int_field(data, "window")
    multiplier = _float_field(data, "std_multiplier")
    if timeframe is None or window is None or multiplier is None:
        return None
    return BollingerIndicatorQuery(
        candles=_candle_query(data, scope, timeframe),
        window=window,
        std_multiplier=multiplier,
    )


def _bollinger_cached(request: Request, query: BollingerIndicatorQuery) -> JsonResponse:
    params = {
        "metric": "bollinger",
        "window": query.window,
        "std_multiplier": query.std_multiplier,
    }
    spec = _indicator_spec(request, query.candles, params, "Bollinger Bands retrieved")
    return _cached_compute(request, spec, lambda: build_bollinger_series(query))


def bollinger_indicator_response(request: Request) -> JsonResponse:
    prepared = _scoped_filter_data(
        request, BollingerIndicatorFilterSerializer, "Invalid bollinger filters"
    )
    if isinstance(prepared, JsonResponse):
        return prepared
    data, scope = prepared
    query = _require_query(_bollinger_query(data, scope), "Invalid bollinger filters")
    if isinstance(query, JsonResponse):
        return query
    return _bollinger_cached(request, query)


def _atr_query(data: dict[str, object], scope: _Scope) -> AtrIndicatorQuery | None:
    timeframe = _text_field(data, "timeframe")
    window = _int_field(data, "window")
    if timeframe is None or window is None:
        return None
    return AtrIndicatorQuery(candles=_candle_query(data, scope, timeframe), window=window)


def _atr_cached(request: Request, query: AtrIndicatorQuery) -> JsonResponse:
    params = {"metric": "atr", "window": query.window}
    spec = _indicator_spec(request, query.candles, params, "ATR retrieved")
    return _cached_compute(request, spec, lambda: build_atr_series(query))


def atr_indicator_response(request: Request) -> JsonResponse:
    prepared = _scoped_filter_data(request, AtrIndicatorFilterSerializer, "Invalid atr filters")
    if isinstance(prepared, JsonResponse):
        return prepared
    data, scope = prepared
    query = _require_query(_atr_query(data, scope), "Invalid atr filters")
    if isinstance(query, JsonResponse):
        return query
    return _atr_cached(request, query)


class BollingerListView(APIView):
    """Bollinger Bands from trade-derived candle closes. Ordinary GETs are not audit events."""

    permission_classes = [IsAuthenticated]
    throttle_scope = "analytics_read"

    @extend_schema(parameters=[BollingerIndicatorFilterSerializer], tags=["analytics"])
    def get(self, request: Request) -> JsonResponse:
        return bollinger_indicator_response(request)


class AtrListView(APIView):
    """Average True Range from trade-derived candle OHLC. Ordinary GETs are not audit events."""

    permission_classes = [IsAuthenticated]
    throttle_scope = "analytics_read"

    @extend_schema(parameters=[AtrIndicatorFilterSerializer], tags=["analytics"])
    def get(self, request: Request) -> JsonResponse:
        return atr_indicator_response(request)


def _rsi_query(data: dict[str, object], scope: _Scope) -> RsiIndicatorQuery | None:
    timeframe = _text_field(data, "timeframe")
    window = _int_field(data, "window")
    if timeframe is None or window is None:
        return None
    return RsiIndicatorQuery(candles=_candle_query(data, scope, timeframe), window=window)


def _rsi_cached(request: Request, query: RsiIndicatorQuery) -> JsonResponse:
    params = {"metric": "rsi", "window": query.window}
    spec = _indicator_spec(request, query.candles, params, "RSI retrieved")
    return _cached_compute(request, spec, lambda: build_rsi_series(query))


def rsi_indicator_response(request: Request) -> JsonResponse:
    prepared = _scoped_filter_data(request, RsiIndicatorFilterSerializer, "Invalid rsi filters")
    if isinstance(prepared, JsonResponse):
        return prepared
    data, scope = prepared
    query = _require_query(_rsi_query(data, scope), "Invalid rsi filters")
    if isinstance(query, JsonResponse):
        return query
    return _rsi_cached(request, query)


def _stochastic_query(data: dict[str, object], scope: _Scope) -> StochasticIndicatorQuery | None:
    timeframe = _text_field(data, "timeframe")
    window = _int_field(data, "window")
    signal = _int_field(data, "signal_period")
    if timeframe is None or window is None or signal is None:
        return None
    return StochasticIndicatorQuery(
        candles=_candle_query(data, scope, timeframe),
        window=window,
        signal_period=signal,
    )


def _stochastic_cached(request: Request, query: StochasticIndicatorQuery) -> JsonResponse:
    params = {
        "metric": "stochastic",
        "window": query.window,
        "signal_period": query.signal_period,
    }
    spec = _indicator_spec(request, query.candles, params, "Stochastic retrieved")
    return _cached_compute(request, spec, lambda: build_stochastic_series(query))


def stochastic_indicator_response(request: Request) -> JsonResponse:
    prepared = _scoped_filter_data(
        request, StochasticIndicatorFilterSerializer, "Invalid stochastic filters"
    )
    if isinstance(prepared, JsonResponse):
        return prepared
    data, scope = prepared
    query = _require_query(_stochastic_query(data, scope), "Invalid stochastic filters")
    if isinstance(query, JsonResponse):
        return query
    return _stochastic_cached(request, query)


class RsiListView(APIView):
    """RSI from trade-derived candle closes. Ordinary GETs are not audit events."""

    permission_classes = [IsAuthenticated]
    throttle_scope = "analytics_read"

    @extend_schema(parameters=[RsiIndicatorFilterSerializer], tags=["analytics"])
    def get(self, request: Request) -> JsonResponse:
        return rsi_indicator_response(request)


class StochasticListView(APIView):
    """Stochastic %K/%D from trade-derived candles. Ordinary GETs are not audit events."""

    permission_classes = [IsAuthenticated]
    throttle_scope = "analytics_read"

    @extend_schema(parameters=[StochasticIndicatorFilterSerializer], tags=["analytics"])
    def get(self, request: Request) -> JsonResponse:
        return stochastic_indicator_response(request)


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
) -> JsonResponse | None:
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
) -> tuple[Instrument, Exchange, datetime, datetime] | JsonResponse:
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


def _create_manual_run(request: Request, data: dict[str, object]) -> JsonResponse:
    validated = _validate_manual_targets(data)
    if isinstance(validated, JsonResponse):
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

    def post(self, request: Request) -> JsonResponse:
        serializer = AggregationCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return failure("Invalid aggregation request", serializer.errors)
        return _create_manual_run(request, _mapping(serializer.validated_data))


class AggregationDetailView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_scope = "analytics_read"

    def get(self, request: Request, run_id: UUID) -> JsonResponse:
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


def _int_or_default(data: dict[str, object], key: str, default: int) -> int:
    value = data.get(key)
    return value if isinstance(value, int) else default


def _float_or_default(data: dict[str, object], key: str, default: float) -> float:
    value = data.get(key)
    if isinstance(value, bool):
        return default
    if isinstance(value, int | float):
        return float(value)
    return default


def _overview_request(
    data: dict[str, object],
    instrument_ids: list[int],
    exchange_ids: list[int] | None,
) -> OverviewRequest:
    from apps.analytics.services.metrics_calculator import (
        DEFAULT_BOLLINGER_MULTIPLIER,
        DEFAULT_STOCHASTIC_SIGNAL,
        DEFAULT_WINDOW,
        MACD_DEFAULT_FAST,
        MACD_DEFAULT_SIGNAL,
        MACD_DEFAULT_SLOW,
    )

    metrics = data.get("parsed_metrics")
    metric_tuple = metrics if isinstance(metrics, tuple) else ()
    return OverviewRequest(
        instrument_ids=instrument_ids,
        exchange_ids=exchange_ids,
        metrics=tuple(str(item) for item in metric_tuple),
        timeframe=_text_field(data, "timeframe"),
        start=_dt_field(data, "start"),
        end=_dt_field(data, "end"),
        start_date=_optional_date(data.get("start_date")),
        end_date=_optional_date(data.get("end_date")),
        include_incomplete=data.get("include_incomplete") is True,
        return_type=str(data.get("return_type") or "simple"),
        window=_int_or_default(data, "window", DEFAULT_WINDOW),
        std_multiplier=_float_or_default(data, "std_multiplier", DEFAULT_BOLLINGER_MULTIPLIER),
        fast_period=_int_or_default(data, "fast_period", MACD_DEFAULT_FAST),
        slow_period=_int_or_default(data, "slow_period", MACD_DEFAULT_SLOW),
        signal_period=_int_or_default(data, "signal_period", MACD_DEFAULT_SIGNAL),
        stochastic_signal=_int_or_default(data, "stochastic_signal", DEFAULT_STOCHASTIC_SIGNAL),
    )


def overview_response(request: Request) -> JsonResponse:
    prepared = _scoped_filter_data(request, OverviewFilterSerializer, "Invalid overview filters")
    if isinstance(prepared, JsonResponse):
        return prepared
    data, scope = prepared
    outcome = build_overview(_overview_request(data, scope.instrument_ids, scope.exchange_ids))
    if isinstance(outcome, IndicatorLimitError):
        return failure(outcome.message, outcome.errors)
    return success("Analytics overview retrieved", outcome)


class OverviewListView(APIView):
    """Combined analytics overview. Reuses existing calculation services."""

    permission_classes = [IsAuthenticated]
    throttle_scope = "analytics_read"

    @extend_schema(parameters=[OverviewFilterSerializer], tags=["analytics"])
    def get(self, request: Request) -> JsonResponse:
        return overview_response(request)
