"""Authenticated ingestion file and rule endpoints."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping

from django.conf import settings
from django.contrib.auth.models import User
from django.db import transaction
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.audit.models import AuditAction, AuditResourceType, AuditStatus
from apps.audit.services import record_audit_event

from .imports import import_configured_files
from .models import IngestionRule
from .pagination import IngestionPagination
from .selectors import (
    filter_owned_ingestions,
    get_owned_ingestion,
    get_owned_rule,
    list_owned_ingestions,
    list_owned_processing_logs,
    list_owned_rejections,
    list_owned_rules,
    list_storage_files,
    list_validation_rule_sets,
)
from .serializers import (
    ConfiguredImportSerializer,
    IngestionListFilterSerializer,
    IngestionLogSerializer,
    IngestionReadSerializer,
    IngestionRuleReadSerializer,
    IngestionRuleWriteSerializer,
    RejectedRowReadSerializer,
    StorageFileReadSerializer,
    ValidationRuleSetReadSerializer,
    ValidationRuleSetWriteSerializer,
    rule_write_payload,
)
from .storage import StorageError
from .user_services import (
    IngestionRequestError,
    create_user_rule,
    retry_owned_ingestion,
    update_user_rule,
)
from .validation_sets import create_validation_rule_set

logger = logging.getLogger("apps.ingestion.views")


def success(message: str, data: object = None, status_code: int = status.HTTP_200_OK) -> Response:
    return Response(
        {"success": True, "message": message, "data": data, "errors": None},
        status=status_code,
    )


def failure(message: str, errors: object, status_code: int) -> Response:
    return Response(
        {"success": False, "message": message, "data": None, "errors": errors},
        status=status_code,
    )


def _not_found(label: str) -> Response:
    return failure(f"{label} not found", {"detail": ["Not found."]}, status.HTTP_404_NOT_FOUND)


def _owner(request: Request) -> User:
    user = request.user
    if not isinstance(user, User):
        raise IngestionRequestError(
            "Authentication required", {"detail": ["Authentication required."]}
        )
    return user


def _audit(
    request: Request,
    *,
    action: str,
    resource_id: object,
    audit_status: str,
    metadata: dict[str, object],
) -> None:
    record_audit_event(
        actor=request.user,
        action=action,
        resource_type=AuditResourceType.INGESTION,
        resource_id=str(resource_id),
        status=audit_status,
        request=request,
        metadata=metadata,
    )


def _prepared_import(request: Request) -> Response | tuple[list[str], str]:
    serializer = ConfiguredImportSerializer(data=request.data)
    if not serializer.is_valid():
        return failure("Validation failed", serializer.errors, status.HTTP_400_BAD_REQUEST)
    payload = _mapping(serializer.validated_data)
    backends = _text_list_key(payload, "storage_backends")
    if backends is None:
        backends = ["local"]
        if settings.INGESTION_S3_BUCKET:
            backends.append("s3")
    return backends, _text(payload, "storage_prefix") or "files"


def _execute_import(request: Request, backends: list[str], prefix: str) -> Response:
    try:
        batch = import_configured_files(_owner(request), backends, prefix)
    except IngestionRequestError as exc:
        return _request_failure(exc)
    except StorageError:
        logger.exception("Configured ingestion import failed.")
        return failure(
            "Unable to read the configured storage.",
            {
                "storage_backends": [
                    "The configured local directory or S3 prefix could not be read."
                ]
            },
            status.HTTP_400_BAD_REQUEST,
        )
    for record in batch.stored:
        _audit(
            request,
            action=AuditAction.INGESTION_STARTED,
            resource_id=record.pk,
            audit_status=AuditStatus.SUCCESS,
            metadata={
                "scope": "configured_import",
                "original_filename": record.original_filename,
                "content_hash": record.content_hash,
                "detected_format": record.detected_format,
            },
        )
    return success(
        "Configured files stored",
        {
            "stored": StorageFileReadSerializer(batch.stored, many=True).data,
            "skipped": [{"name": item.name, "reason": item.reason} for item in batch.skipped],
            "queued": [
                {"name": item.name, "id": item.ingestion_id, "rule_id": item.rule_id}
                for item in batch.queued
            ],
        },
        status.HTTP_201_CREATED,
    )


def _update_owned_rule(request: Request, rule: IngestionRule) -> Response:
    serializer = IngestionRuleWriteSerializer(data=request.data, partial=True)
    if not serializer.is_valid():
        return failure("Validation failed", serializer.errors, status.HTTP_400_BAD_REQUEST)
    payload = rule_write_payload(_mapping(serializer.validated_data), creating=False)
    try:
        with transaction.atomic():
            updated = update_user_rule(rule, _owner(request), payload)
            _audit(
                request,
                action=AuditAction.INGESTION_RULE_CHANGED,
                resource_id=updated.pk,
                audit_status=AuditStatus.SUCCESS,
                metadata={"change": "updated", "rule_name": updated.name},
            )
    except IngestionRequestError as exc:
        return _request_failure(exc)
    return success("Rule updated", IngestionRuleReadSerializer(updated).data)


def _request_failure(exc: IngestionRequestError) -> Response:
    code = (
        status.HTTP_401_UNAUTHORIZED
        if "Authentication" in exc.message
        else status.HTTP_400_BAD_REQUEST
    )
    return failure(exc.message, exc.errors, code)


class ConfiguredImportView(APIView):
    """Copy every file from the configured local directory and S3 prefix."""

    permission_classes = [IsAuthenticated]
    throttle_scope = "ingestion_upload"

    @extend_schema(tags=["ingestion"])
    def post(self, request: Request) -> Response:
        prepared = _prepared_import(request)
        if isinstance(prepared, Response):
            return prepared
        return _execute_import(request, prepared[0], prepared[1])


class StorageFileListView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(tags=["ingestion"])
    def get(self, request: Request) -> Response:
        return _paginated(
            self,
            request,
            list_storage_files(_owner(request)),
            StorageFileReadSerializer,
            "Files retrieved",
        )


class ValidationRuleSetCreateView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(tags=["ingestion"])
    def get(self, request: Request) -> Response:
        data_type = request.query_params.get("data_type", "")
        return _paginated(
            self,
            request,
            list_validation_rule_sets(data_type=data_type),
            ValidationRuleSetReadSerializer,
            "Validation rule sets retrieved",
        )

    @extend_schema(tags=["ingestion"])
    def post(self, request: Request) -> Response:
        serializer = ValidationRuleSetWriteSerializer(data=request.data)
        if not serializer.is_valid():
            return failure("Validation failed", serializer.errors, status.HTTP_400_BAD_REQUEST)
        try:
            rule_set = create_validation_rule_set(_mapping(serializer.validated_data))
        except IngestionRequestError as exc:
            return _request_failure(exc)
        return success(
            "Validation rule set created",
            ValidationRuleSetReadSerializer(rule_set).data,
            status.HTTP_201_CREATED,
        )


class IngestionRuleListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(tags=["ingestion"])
    def get(self, request: Request) -> Response:
        return _paginated(
            self,
            request,
            list_owned_rules(_owner(request)),
            IngestionRuleReadSerializer,
            "Rules retrieved",
        )

    @extend_schema(tags=["ingestion"])
    def post(self, request: Request) -> Response:
        serializer = IngestionRuleWriteSerializer(data=request.data)
        if not serializer.is_valid():
            return failure("Validation failed", serializer.errors, status.HTTP_400_BAD_REQUEST)
        payload = rule_write_payload(_mapping(serializer.validated_data), creating=True)
        try:
            with transaction.atomic():
                rule = create_user_rule(_owner(request), payload)
                _audit(
                    request,
                    action=AuditAction.INGESTION_RULE_CHANGED,
                    resource_id=rule.pk,
                    audit_status=AuditStatus.SUCCESS,
                    metadata={
                        "change": "created",
                        "rule_name": rule.name,
                        "data_type": rule.data_type,
                    },
                )
        except IngestionRequestError as exc:
            return _request_failure(exc)
        return success(
            "Rule created",
            IngestionRuleReadSerializer(rule).data,
            status.HTTP_201_CREATED,
        )


class IngestionRuleDetailView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(tags=["ingestion"])
    def get(self, request: Request, rule_id: uuid.UUID) -> Response:
        rule = get_owned_rule(_owner(request), rule_id)
        if rule is None:
            return _not_found("Rule")
        return success("Rule retrieved", IngestionRuleReadSerializer(rule).data)

    @extend_schema(tags=["ingestion"])
    def patch(self, request: Request, rule_id: uuid.UUID) -> Response:
        rule = get_owned_rule(_owner(request), rule_id)
        if rule is None:
            return _not_found("Rule")
        return _update_owned_rule(request, rule)


class IngestionLogListView(APIView):
    """Accepted and rejected rows from the ingestion log for the caller."""

    permission_classes = [IsAuthenticated]

    @extend_schema(tags=["ingestion"])
    def get(self, request: Request) -> Response:
        return _paginated(
            self,
            request,
            list_owned_processing_logs(_owner(request)),
            IngestionLogSerializer,
            "Ingestion logs retrieved",
        )


class IngestionListView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(tags=["ingestion"])
    def get(self, request: Request) -> Response:
        filters = IngestionListFilterSerializer(data=request.query_params)
        if not filters.is_valid():
            return failure("Validation failed", filters.errors, status.HTTP_400_BAD_REQUEST)
        queryset = filter_owned_ingestions(
            list_owned_ingestions(_owner(request)),
            _mapping(filters.validated_data),
        )
        return _paginated(self, request, queryset, IngestionReadSerializer, "Ingestions retrieved")


class IngestionDetailView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(tags=["ingestion"])
    def get(self, request: Request, ingestion_id: uuid.UUID) -> Response:
        ingestion = get_owned_ingestion(_owner(request), ingestion_id)
        if ingestion is None:
            return _not_found("Ingestion")
        return success("Ingestion retrieved", IngestionReadSerializer(ingestion).data)


class IngestionRejectionListView(APIView):
    """List rejected rows for one owned ingestion."""

    permission_classes = [IsAuthenticated]

    @extend_schema(tags=["ingestion"])
    def get(self, request: Request, ingestion_id: uuid.UUID) -> Response:
        ingestion = get_owned_ingestion(_owner(request), ingestion_id)
        if ingestion is None:
            return _not_found("Ingestion")
        queryset = list_owned_rejections(_owner(request), ingestion.pk)
        return _paginated(
            self,
            request,
            queryset,
            RejectedRowReadSerializer,
            "Rejections retrieved",
        )


class IngestionRetryView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(tags=["ingestion"])
    def post(self, request: Request, ingestion_id: uuid.UUID) -> Response:
        ingestion = get_owned_ingestion(_owner(request), ingestion_id)
        if ingestion is None:
            return _not_found("Ingestion")
        try:
            updated = retry_owned_ingestion(ingestion)
        except IngestionRequestError as exc:
            return _request_failure(exc)
        _audit(
            request,
            action=AuditAction.INGESTION_STARTED,
            resource_id=updated.pk,
            audit_status=AuditStatus.ACCEPTED,
            metadata={"scope": "retry", "filename": updated.file_name},
        )
        return success("Ingestion retry queued", IngestionReadSerializer(updated).data)


def _mapping(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): item for key, item in value.items()}


def _text(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    return value if isinstance(value, str) else ""


def _text_list_key(payload: Mapping[str, object], key: str) -> list[str] | None:
    if key not in payload:
        return None
    value = payload[key]
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _paginated(
    view: APIView,
    request: Request,
    queryset: object,
    serializer_class: type[StorageFileReadSerializer]
    | type[IngestionRuleReadSerializer]
    | type[IngestionReadSerializer]
    | type[IngestionLogSerializer]
    | type[RejectedRowReadSerializer]
    | type[ValidationRuleSetReadSerializer],
    message: str,
) -> Response:
    paginator = IngestionPagination()
    page = paginator.paginate_queryset(queryset, request, view=view)
    serializer = serializer_class(page, many=True)
    return success(message, paginator.get_paginated_response(serializer.data).data)
