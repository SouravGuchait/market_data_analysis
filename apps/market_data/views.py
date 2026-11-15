"""HTTP endpoints for bounded tick/trade and daily OHLCV listing."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime

from django.contrib.auth.base_user import AbstractBaseUser
from django.http import JsonResponse
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.analytics.scope import resolve_scope
from apps.common.pagination import StandardResultsPagination
from apps.market_data.selectors import DailyOHLCVQuery, TradeQuery, list_daily_bars, list_trades
from apps.market_data.serializers import (
    DailyOHLCVFilterSerializer,
    DailyOHLCVReadSerializer,
    TickFilterSerializer,
    TradeReadSerializer,
)


def _mapping(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): item for key, item in value.items()}


def _user(request: Request) -> AbstractBaseUser:
    if isinstance(request.user, AbstractBaseUser):
        return request.user
    raise PermissionError("Authentication required.")


def _failure(message: str, errors: object, code: int = status.HTTP_400_BAD_REQUEST) -> JsonResponse:
    return JsonResponse(
        {"success": False, "message": message, "data": None, "errors": errors},
        status=code,
    )


def _as_datetime(value: object) -> datetime | None:
    return value if isinstance(value, datetime) else None


def _as_date(value: object) -> date | None:
    return value if isinstance(value, date) else None


def _as_str(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _as_int(value: object) -> int | None:
    return value if isinstance(value, int) else None


def _paginated(
    view: APIView,
    request: Request,
    queryset: object,
    serializer_cls: type[TradeReadSerializer] | type[DailyOHLCVReadSerializer],
    message: str,
) -> JsonResponse:
    paginator = StandardResultsPagination()
    page = paginator.paginate_queryset(queryset, request, view=view)
    serializer = serializer_cls(page, many=True)
    return JsonResponse(
        {
            "success": True,
            "message": message,
            "data": paginator.get_paginated_response(serializer.data).data,
            "errors": None,
        }
    )


class TickListView(APIView):
    """Paginated tick trades. Never returns unbounded history."""

    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "market_data_read"

    @extend_schema(tags=["market-data"], parameters=[TickFilterSerializer])
    def get(self, request: Request) -> JsonResponse:
        filters = TickFilterSerializer(data=request.query_params)
        if not filters.is_valid():
            return _failure("Invalid tick filters", filters.errors)
        data = _mapping(filters.validated_data)
        instrument_ids, exchange_ids, error = resolve_scope(_user(request), data)
        if error is not None:
            return error
        ordering = _as_str(data.get("ordering")) or "event_timestamp"
        queryset = list_trades(
            TradeQuery(
                instrument_ids=instrument_ids,
                exchange_ids=exchange_ids,
                range_start=_as_datetime(data.get("start")),
                range_end=_as_datetime(data.get("end")),
                exchange_trade_id=_as_str(data.get("exchange_trade_id")),
                sequence_number=_as_int(data.get("sequence_number")),
                newest_first=ordering.startswith("-"),
            )
        )
        return _paginated(self, request, queryset, TradeReadSerializer, "Ticks retrieved")


class DailyOHLCVListView(APIView):
    """Paginated imported daily OHLCV bars."""

    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "market_data_read"

    @extend_schema(tags=["market-data"], parameters=[DailyOHLCVFilterSerializer])
    def get(self, request: Request) -> JsonResponse:
        filters = DailyOHLCVFilterSerializer(data=request.query_params)
        if not filters.is_valid():
            return _failure("Invalid daily OHLCV filters", filters.errors)
        data = _mapping(filters.validated_data)
        instrument_ids, _exchange_ids, error = resolve_scope(_user(request), data)
        if error is not None:
            return error
        queryset = list_daily_bars(
            DailyOHLCVQuery(
                instrument_ids=instrument_ids,
                start_date=_as_date(data.get("start_date")),
                end_date=_as_date(data.get("end_date")),
            )
        )
        return _paginated(
            self, request, queryset, DailyOHLCVReadSerializer, "Daily OHLCV retrieved"
        )
