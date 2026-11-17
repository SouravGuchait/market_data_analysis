"""Authenticated APIs for user-owned validation configurations."""

from __future__ import annotations

import uuid
from collections.abc import Mapping

from django.db import IntegrityError, transaction
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAdminUser, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.audit.models import AuditAction, AuditResourceType, AuditStatus
from apps.audit.services import record_audit_event
from apps.common.pagination import StandardResultsPagination

from .models import UserValidationConfiguration, ValidationConfigurationVersion
from .schema import schema_for_asset_type
from .selectors import get_owned_configuration, list_owned_configurations
from .serializers import (
    ValidationConfigurationCreateSerializer,
    ValidationConfigurationDetailReadSerializer,
    ValidationConfigurationPatchSerializer,
    ValidationConfigurationReadSerializer,
    ValidationSchemaQuerySerializer,
)
from .services import (
    ConfigurationConflictError,
    ConfigurationNotFoundError,
    approve_configuration,
    create_configuration,
    import_compiled_config,
    reject_configuration,
    update_configuration,
)
from .validators import CompiledConfigValidationError


def success(
    message: str,
    data: object = None,
    status_code: int = status.HTTP_200_OK,
) -> Response:
    return Response(
        {"success": True, "message": message, "data": data, "errors": None},
        status=status_code,
    )


def failure(message: str, errors: object, status_code: int) -> Response:
    return Response(
        {"success": False, "message": message, "data": None, "errors": errors},
        status=status_code,
    )


def _validated_text(values: Mapping[str, object], field: str) -> str:
    value = values.get(field)
    if not isinstance(value, str):
        raise TypeError(f"Validated field {field!r} must be a string.")
    return value


def _validated_mapping(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError("Validated serializer data must be an object.")
    result: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise TypeError("Validated serializer keys must be strings.")
        result[key] = item
    return result


def _service_failure(
    error: (
        ConfigurationNotFoundError
        | ConfigurationConflictError
        | CompiledConfigValidationError
        | IntegrityError
    ),
) -> Response:
    if isinstance(error, ConfigurationNotFoundError):
        return failure(
            "Validation configuration not found",
            {"detail": ["Not found."]},
            status.HTTP_404_NOT_FOUND,
        )
    if isinstance(error, CompiledConfigValidationError):
        return failure(
            "Validation failed",
            {error.field: [str(error)]},
            status.HTTP_400_BAD_REQUEST,
        )
    detail = (
        "Reload the configuration and retry the update."
        if isinstance(error, IntegrityError)
        else str(error)
    )
    return failure(
        "Configuration transition conflict",
        {"detail": [detail]},
        status.HTTP_409_CONFLICT,
    )


def _audit_change(
    request: Request,
    configuration: UserValidationConfiguration,
    version: ValidationConfigurationVersion,
    action: str,
) -> None:
    record_audit_event(
        actor=request.user,
        action=action,
        resource_type=AuditResourceType.VALIDATION_CONFIGURATION,
        resource_id=configuration.pk,
        status=AuditStatus.SUCCESS,
        request=request,
        metadata={"asset_type": configuration.asset_type, "version": version.version},
    )


class ValidationConfigurationListCreateView(APIView):
    """List owned configurations or create a natural-language draft."""

    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "validation_rule_api"

    @extend_schema(
        operation_id="validation_configuration_list",
        responses={200: OpenApiTypes.OBJECT},
        tags=["validation-rules"],
    )
    def get(self, request: Request) -> Response:
        configurations = list_owned_configurations(request.user)
        paginator: PageNumberPagination = StandardResultsPagination()
        page = paginator.paginate_queryset(configurations, request, view=self)
        if page is None:
            return success(
                "Validation configurations retrieved",
                {"count": 0, "next": None, "previous": None, "results": []},
            )
        serialized = ValidationConfigurationReadSerializer(page, many=True)
        return success(
            "Validation configurations retrieved",
            paginator.get_paginated_response(serialized.data).data,
        )

    @extend_schema(
        request=ValidationConfigurationCreateSerializer,
        responses={201: ValidationConfigurationReadSerializer},
        tags=["validation-rules"],
    )
    def post(self, request: Request) -> Response:
        serializer = ValidationConfigurationCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return failure("Validation failed", serializer.errors, status.HTTP_400_BAD_REQUEST)
        validated_data = _validated_mapping(serializer.validated_data)
        asset_type = _validated_text(validated_data, "asset_type")
        description = _validated_text(validated_data, "description")
        try:
            with transaction.atomic():
                configuration = create_configuration(
                    request.user,
                    asset_type=asset_type,
                    description=description,
                )
                version = configuration.current_version
                if version is None:
                    raise ConfigurationConflictError("Configuration has no current version.")
                _audit_change(
                    request,
                    configuration,
                    version,
                    AuditAction.VALIDATION_CONFIGURATION_CREATED,
                )
        except IntegrityError:
            return failure(
                "Validation configuration already exists",
                {"asset_type": ["A configuration already exists for this asset type."]},
                status.HTTP_409_CONFLICT,
            )
        return success(
            "Validation configuration created",
            ValidationConfigurationReadSerializer(configuration).data,
            status.HTTP_201_CREATED,
        )


class ValidationConfigurationDetailView(APIView):
    """Retrieve or edit one owned configuration."""

    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "validation_rule_api"

    @extend_schema(
        operation_id="validation_configuration_detail",
        responses={200: OpenApiTypes.OBJECT},
        tags=["validation-rules"],
    )
    def get(self, request: Request, configuration_id: uuid.UUID) -> Response:
        configuration = get_owned_configuration(
            request.user,
            configuration_id,
            include_history=True,
        )
        if configuration is None:
            return _service_failure(ConfigurationNotFoundError())
        return success(
            "Validation configuration retrieved",
            ValidationConfigurationDetailReadSerializer(configuration).data,
        )

    @extend_schema(
        request=ValidationConfigurationPatchSerializer,
        responses={200: ValidationConfigurationReadSerializer},
        tags=["validation-rules"],
    )
    def patch(self, request: Request, configuration_id: uuid.UUID) -> Response:
        serializer = ValidationConfigurationPatchSerializer(data=request.data)
        if not serializer.is_valid():
            return failure("Validation failed", serializer.errors, status.HTTP_400_BAD_REQUEST)
        try:
            with transaction.atomic():
                configuration = update_configuration(
                    request.user,
                    configuration_id,
                    _validated_mapping(serializer.validated_data),
                )
                version = configuration.current_version
                if version is None:
                    raise ConfigurationConflictError("Configuration has no current version.")
                _audit_change(
                    request,
                    configuration,
                    version,
                    AuditAction.VALIDATION_CONFIGURATION_UPDATED,
                )
        except (ConfigurationNotFoundError, ConfigurationConflictError, IntegrityError) as exc:
            return _service_failure(exc)
        return success(
            "Validation configuration updated",
            ValidationConfigurationReadSerializer(configuration).data,
        )


class CompiledConfigImportView(APIView):
    """Accept a staff-only manual JSON import for an owned draft."""

    permission_classes = [IsAuthenticated, IsAdminUser]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "validation_rule_api"

    @extend_schema(
        request=OpenApiTypes.OBJECT,
        responses={200: ValidationConfigurationReadSerializer},
        tags=["validation-rules"],
    )
    def post(self, request: Request, configuration_id: uuid.UUID) -> Response:
        try:
            with transaction.atomic():
                configuration, version = import_compiled_config(
                    request.user,
                    configuration_id,
                    request.data,
                )
                _audit_change(
                    request,
                    configuration,
                    version,
                    AuditAction.VALIDATION_CONFIG_IMPORTED,
                )
        except (
            ConfigurationNotFoundError,
            ConfigurationConflictError,
            CompiledConfigValidationError,
        ) as exc:
            return _service_failure(exc)
        return success(
            "Compiled configuration imported for review",
            ValidationConfigurationReadSerializer(configuration).data,
        )


class ValidationConfigurationApproveView(APIView):
    """Approve a validated version as the authenticated owner."""

    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "validation_rule_api"

    @extend_schema(
        request=None,
        responses={200: ValidationConfigurationReadSerializer},
        tags=["validation-rules"],
    )
    def post(self, request: Request, configuration_id: uuid.UUID) -> Response:
        try:
            with transaction.atomic():
                configuration, version = approve_configuration(request.user, configuration_id)
                _audit_change(
                    request,
                    configuration,
                    version,
                    AuditAction.VALIDATION_CONFIGURATION_APPROVED,
                )
        except (
            ConfigurationNotFoundError,
            ConfigurationConflictError,
            CompiledConfigValidationError,
        ) as exc:
            return _service_failure(exc)
        return success(
            "Validation configuration approved",
            ValidationConfigurationReadSerializer(configuration).data,
        )


class ValidationConfigurationRejectView(APIView):
    """Reject a pending review as the authenticated owner."""

    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "validation_rule_api"

    @extend_schema(
        request=None,
        responses={200: ValidationConfigurationReadSerializer},
        tags=["validation-rules"],
    )
    def post(self, request: Request, configuration_id: uuid.UUID) -> Response:
        try:
            with transaction.atomic():
                configuration, version = reject_configuration(request.user, configuration_id)
                _audit_change(
                    request,
                    configuration,
                    version,
                    AuditAction.VALIDATION_CONFIGURATION_REJECTED,
                )
        except (ConfigurationNotFoundError, ConfigurationConflictError) as exc:
            return _service_failure(exc)
        return success(
            "Validation configuration rejected",
            ValidationConfigurationReadSerializer(configuration).data,
        )


class ValidationSchemaView(APIView):
    """Expose server-controlled asset columns and supported rule operators."""

    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "validation_rule_api"

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="asset_type",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=True,
                enum=["ohlcv", "tick"],
            ),
        ],
        responses={200: OpenApiTypes.OBJECT},
        tags=["validation-rules"],
    )
    def get(self, request: Request) -> Response:
        serializer = ValidationSchemaQuerySerializer(data=request.query_params)
        if not serializer.is_valid():
            return failure("Validation failed", serializer.errors, status.HTTP_400_BAD_REQUEST)
        validated_data = _validated_mapping(serializer.validated_data)
        asset_type = _validated_text(validated_data, "asset_type")
        return success(
            "Validation schema retrieved",
            schema_for_asset_type(asset_type),
        )
