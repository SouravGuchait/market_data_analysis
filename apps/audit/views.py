"""Read-only audit API endpoints."""

from __future__ import annotations

import uuid
from typing import Any, cast

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from .pagination import AuditPagination
from .selectors import filter_audit_events, get_visible_audit_event, list_visible_audit_events
from .serializers import AuditEventFilterSerializer, AuditEventReadSerializer


def success(message: str, data: Any = None) -> Response:
    return Response(
        {"success": True, "message": message, "data": data, "errors": None},
        status=status.HTTP_200_OK,
    )


def failure(message: str, errors: Any, status_code: int) -> Response:
    return Response(
        {"success": False, "message": message, "data": None, "errors": errors},
        status=status_code,
    )


AUDIT_FILTER_PARAMETERS = [
    OpenApiParameter(
        name="action",
        type=OpenApiTypes.STR,
        location=OpenApiParameter.QUERY,
        required=False,
        description="Exact audit action, for example login_success or portfolio_created.",
    ),
    OpenApiParameter(
        name="resource_type",
        type=OpenApiTypes.STR,
        location=OpenApiParameter.QUERY,
        required=False,
        description="Exact resource type, for example user, portfolio or ingestion.",
    ),
    OpenApiParameter(
        name="status",
        type=OpenApiTypes.STR,
        location=OpenApiParameter.QUERY,
        required=False,
        enum=["success", "failure", "denied", "accepted", "partial"],
    ),
    OpenApiParameter(
        name="start_date",
        type=OpenApiTypes.STR,
        location=OpenApiParameter.QUERY,
        required=False,
        description="Inclusive ISO 8601 date or datetime lower bound.",
    ),
    OpenApiParameter(
        name="end_date",
        type=OpenApiTypes.STR,
        location=OpenApiParameter.QUERY,
        required=False,
        description="Inclusive ISO 8601 date or datetime upper bound.",
    ),
]


class AuditEventListView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(parameters=AUDIT_FILTER_PARAMETERS, tags=["audit"])
    def get(self, request: Request) -> Response:
        filters = AuditEventFilterSerializer(data=request.query_params)
        if not filters.is_valid():
            return failure("Validation failed", filters.errors, status.HTTP_400_BAD_REQUEST)

        queryset = filter_audit_events(
            list_visible_audit_events(request.user),
            cast(dict[str, Any], filters.validated_data),
        )
        paginator = AuditPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        serializer = AuditEventReadSerializer(page, many=True)
        paginated = paginator.get_paginated_response(serializer.data).data
        return success("Audit events retrieved", paginated)


class AuditEventDetailView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(tags=["audit"])
    def get(self, request: Request, audit_id: uuid.UUID) -> Response:
        event = get_visible_audit_event(request.user, audit_id)
        if event is None:
            return failure(
                "Audit event not found",
                {"detail": ["Not found."]},
                status.HTTP_404_NOT_FOUND,
            )
        return success("Audit event retrieved", AuditEventReadSerializer(event).data)
