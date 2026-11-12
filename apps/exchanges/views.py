"""Read API for shared exchange master data."""

from __future__ import annotations

from typing import Any

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.pagination import StandardResultsPagination
from apps.exchanges.selectors import list_exchanges
from apps.exchanges.serializers import ExchangeSerializer


def success(message: str, data: Any) -> Response:
    return Response({"success": True, "message": message, "data": data, "errors": None})


class ExchangeListView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["exchanges"],
        parameters=[
            OpenApiParameter(
                name="code",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Case-insensitive exchange code.",
            )
        ],
    )
    def get(self, request: Request) -> Response:
        code = request.query_params.get("code", "")
        paginator = StandardResultsPagination()
        page = paginator.paginate_queryset(list_exchanges(code), request, view=self)
        serializer = ExchangeSerializer(page, many=True)
        return success(
            "Exchanges retrieved", paginator.get_paginated_response(serializer.data).data
        )
