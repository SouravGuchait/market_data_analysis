"""Health and readiness endpoints."""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from .health import run_readiness_checks
from .serializers import HealthResponseSerializer


def _response_payload(
    *,
    success: bool,
    message: str,
    data: dict[str, Any],
    errors: dict[str, list[str]] | None,
) -> dict[str, Any]:
    return {
        "success": success,
        "message": message,
        "data": data,
        "errors": errors,
    }


class HealthLivenessView(APIView):
    """Report that the web process is running, without touching dependencies."""

    authentication_classes: list[type] = []
    permission_classes = [AllowAny]

    @extend_schema(
        responses={status.HTTP_200_OK: HealthResponseSerializer},
        tags=["health"],
    )
    def get(self, request: Request) -> Response:
        data = {
            "status": "ok",
            "version": settings.APP_VERSION,
            "timestamp": timezone.now(),
        }
        return Response(
            _response_payload(
                success=True,
                message="Service is alive",
                data=data,
                errors=None,
            ),
            status=status.HTTP_200_OK,
        )


class HealthReadinessView(APIView):
    """Report whether the service and its required dependencies are ready."""

    authentication_classes: list[type] = []
    permission_classes = [AllowAny]

    @extend_schema(
        responses={
            status.HTTP_200_OK: HealthResponseSerializer,
            status.HTTP_503_SERVICE_UNAVAILABLE: HealthResponseSerializer,
        },
        tags=["health"],
    )
    def get(self, request: Request) -> Response:
        result = run_readiness_checks()
        checks = {name: {"status": check.status} for name, check in result.checks.items()}
        data = {
            "status": "ok" if result.is_ready else "degraded",
            "version": settings.APP_VERSION,
            "timestamp": timezone.now(),
            "checks": checks,
        }

        if result.is_ready:
            return Response(
                _response_payload(
                    success=True,
                    message="Service is ready",
                    data=data,
                    errors=None,
                ),
                status=status.HTTP_200_OK,
            )

        return Response(
            _response_payload(
                success=False,
                message="Service is not ready",
                data=data,
                errors=result.errors,
            ),
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
