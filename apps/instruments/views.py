"""Read and create APIs for the shared instrument catalog."""

from __future__ import annotations

from typing import Any

from django.db import IntegrityError, transaction
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.audit.models import AuditAction, AuditResourceType, AuditStatus
from apps.audit.services import record_audit_event
from apps.common.pagination import StandardResultsPagination
from apps.instruments.models import Instrument
from apps.instruments.selectors import list_instruments
from apps.instruments.serializers import (
    InstrumentFilterSerializer,
    InstrumentReadSerializer,
    InstrumentWriteSerializer,
)

FILTER_PARAMETERS = [
    OpenApiParameter(
        name="exchange_code", type=OpenApiTypes.STR, location=OpenApiParameter.QUERY, required=False
    ),
    OpenApiParameter(
        name="symbol", type=OpenApiTypes.STR, location=OpenApiParameter.QUERY, required=False
    ),
    OpenApiParameter(
        name="asset_class", type=OpenApiTypes.STR, location=OpenApiParameter.QUERY, required=False
    ),
    OpenApiParameter(
        name="currency", type=OpenApiTypes.STR, location=OpenApiParameter.QUERY, required=False
    ),
    OpenApiParameter(
        name="search",
        type=OpenApiTypes.STR,
        location=OpenApiParameter.QUERY,
        required=False,
        description="Case-insensitive match against symbol or instrument name.",
    ),
]


def _string_filters(validated: object) -> dict[str, str]:
    if not isinstance(validated, dict):
        return {}
    return {
        key: value
        for key, value in validated.items()
        if isinstance(key, str) and isinstance(value, str)
    }


def success(message: str, data: Any, status_code: int = status.HTTP_200_OK) -> Response:
    return Response(
        {"success": True, "message": message, "data": data, "errors": None},
        status=status_code,
    )


def failure(message: str, errors: Any, status_code: int = status.HTTP_400_BAD_REQUEST) -> Response:
    return Response(
        {"success": False, "message": message, "data": None, "errors": errors},
        status=status_code,
    )


class InstrumentListCreateView(APIView):
    """Shared instrument master data. Ownership is not per user."""

    permission_classes = [IsAuthenticated]

    @extend_schema(tags=["instruments"], parameters=FILTER_PARAMETERS)
    def get(self, request: Request) -> Response:
        filters = InstrumentFilterSerializer(data=request.query_params)
        if not filters.is_valid():
            return failure("Validation failed", filters.errors)
        paginator = StandardResultsPagination()
        page = paginator.paginate_queryset(
            list_instruments(_string_filters(filters.validated_data)), request, view=self
        )
        serializer = InstrumentReadSerializer(page, many=True)
        return success(
            "Instruments retrieved",
            paginator.get_paginated_response(serializer.data).data,
        )

    @extend_schema(tags=["instruments"], request=InstrumentWriteSerializer)
    def post(self, request: Request) -> Response:
        serializer = InstrumentWriteSerializer(data=request.data)
        if not serializer.is_valid():
            return failure("Validation failed", serializer.errors)
        try:
            with transaction.atomic():
                saved = serializer.save()
                if not isinstance(saved, Instrument):
                    raise IntegrityError("Instrument was not created.")
                instrument = saved
                record_audit_event(
                    actor=request.user,
                    action=AuditAction.INSTRUMENT_CREATED,
                    resource_type=AuditResourceType.INSTRUMENT,
                    resource_id=instrument.pk,
                    status=AuditStatus.SUCCESS,
                    request=request,
                    metadata={
                        "symbol": instrument.symbol,
                        "exchange_code": instrument.exchange.code,
                    },
                )
        except IntegrityError:
            return failure(
                "Validation failed",
                {"symbol": ["This symbol already exists on the selected exchange."]},
            )
        stored = Instrument.objects.select_related("exchange").get(pk=instrument.pk)
        return success(
            "Instrument created",
            InstrumentReadSerializer(stored).data,
            status.HTTP_201_CREATED,
        )
