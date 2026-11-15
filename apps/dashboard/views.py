"""Dashboard bootstrap HTTP endpoint."""

from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID

from django.contrib.auth.base_user import AbstractBaseUser
from django.http import JsonResponse
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.dashboard.serializers import BootstrapQuerySerializer, BootstrapResponseSerializer
from apps.dashboard.services import build_bootstrap


def _user(request: Request) -> AbstractBaseUser:
    if isinstance(request.user, AbstractBaseUser):
        return request.user
    raise PermissionError("Authentication required.")


def _success(data: object) -> JsonResponse:
    return JsonResponse(
        {"success": True, "message": "Dashboard bootstrap retrieved", "data": data, "errors": None}
    )


def _failure(message: str, errors: object, code: int) -> JsonResponse:
    return JsonResponse(
        {"success": False, "message": message, "data": None, "errors": errors},
        status=code,
    )


class DashboardBootstrapView(APIView):
    """Minimum reference data to initialize the Streamlit dashboard."""

    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "dashboard_read"

    @extend_schema(
        tags=["dashboard"],
        parameters=[BootstrapQuerySerializer],
        responses={200: BootstrapResponseSerializer},
    )
    def get(self, request: Request) -> JsonResponse:
        query = BootstrapQuerySerializer(data=request.query_params)
        if not query.is_valid():
            return _failure("Validation failed", query.errors, status.HTTP_400_BAD_REQUEST)
        validated = query.validated_data if isinstance(query.validated_data, Mapping) else {}
        portfolio_id = validated.get("portfolio_id")
        payload = build_bootstrap(
            _user(request),
            portfolio_id=portfolio_id if isinstance(portfolio_id, UUID) else None,
        )
        if payload is None:
            return _failure(
                "Portfolio not found",
                {"detail": ["Not found."]},
                status.HTTP_404_NOT_FOUND,
            )
        return _success(payload)
