"""Authenticated ingestion file, rule, scan and dispatch endpoints."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping
from datetime import datetime

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
    list_owned_rejections,
    list_owned_rules,
    list_storage_files,
)
from .serializers import (
    ConfiguredImportSerializer,
    DispatchRequestSerializer,
    IngestionListFilterSerializer,
    IngestionReadSerializer,
    IngestionRuleReadSerializer,
    IngestionRuleWriteSerializer,
    IngestionScanReadSerializer,
    RejectedRowReadSerializer,
    ScanRequestSerializer,
    StorageFileReadSerializer,
    rule_write_payload,
)
from .storage import StorageError
from .user_services import (
    IngestionRequestError,
    create_user_rule,
    dispatch_owned_ingestions,
    resolve_owned_rule,
    retry_owned_ingestion,
    scan_owned_rule,
    update_user_rule,
)

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
    return backends, _text(payload, "storage_prefix") or "market"


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


def _resolved_scan_rule(request: Request, reference: str) -> Response | IngestionRule:
    try:
        rule = resolve_owned_rule(_owner(request), reference)
    except IngestionRequestError as exc:
        return _request_failure(exc)
    if rule is None:
        return _not_found("Rule")
    return rule


def _scan_rule(request: Request) -> Response | IngestionRule:
    serializer = ScanRequestSerializer(data=request.data)
    if not serializer.is_valid():
        return failure("Validation failed", serializer.errors, status.HTTP_400_BAD_REQUEST)
    return _resolved_scan_rule(request, _text(_mapping(serializer.validated_data), "rule_id"))


def _run_owned_scan(request: Request, rule: IngestionRule) -> Response:
    try:
        scan = scan_owned_rule(_owner(request), rule)
    except IngestionRequestError as exc:
        return _request_failure(exc)
    _audit(
        request,
        action=AuditAction.INGESTION_STARTED,
        resource_id=scan.pk,
        audit_status=AuditStatus.SUCCESS if scan.failed_count == 0 else AuditStatus.FAILURE,
        metadata={
            "scope": "manual_scan",
            "rule_name": rule.name,
            "discovered_count": scan.discovered_count,
            "queued_count": scan.queued_count,
            "skipped_count": scan.skipped_count,
            "failed_count": scan.failed_count,
        },
    )
    return success("Scan completed.", IngestionScanReadSerializer(scan).data)


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


class IngestionScanView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_scope = "ingestion_scan"

    @extend_schema(tags=["ingestion"])
    def post(self, request: Request) -> Response:
        rule = _scan_rule(request)
        if isinstance(rule, Response):
            return rule
        return _run_owned_scan(request, rule)


class IngestionDispatchView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_scope = "ingestion_dispatch"

    @extend_schema(tags=["ingestion"])
    def post(self, request: Request) -> Response:
        serializer = DispatchRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return failure("Validation failed", serializer.errors, status.HTTP_400_BAD_REQUEST)
        payload = _mapping(serializer.validated_data)
        try:
            result = dispatch_owned_ingestions(_owner(request), _text_list(payload))
        except IngestionRequestError as exc:
            return _request_failure(exc)
        _audit(
            request,
            action=AuditAction.INGESTION_STARTED,
            resource_id="",
            audit_status=AuditStatus.ACCEPTED,
            metadata={
                "scope": "dispatch",
                "queued": result["queued"],
                "skipped": result["skipped"],
            },
        )
        return success("Dispatch completed.", result)


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


def _uuid_value(payload: Mapping[str, object], key: str) -> uuid.UUID | None:
    value = payload.get(key)
    if isinstance(value, uuid.UUID):
        return value
    return None


def _text_list_key(payload: Mapping[str, object], key: str) -> list[str] | None:
    if key not in payload:
        return None
    value = payload[key]
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _text_list(payload: Mapping[str, object]) -> list[str] | None:
    if "ingestion_ids" not in payload:
        return None
    value = payload["ingestion_ids"]
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _as_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    return None


def _paginated(
    view: APIView,
    request: Request,
    queryset: object,
    serializer_class: type[StorageFileReadSerializer]
    | type[IngestionRuleReadSerializer]
    | type[IngestionReadSerializer]
    | type[RejectedRowReadSerializer],
    message: str,
) -> Response:
    paginator = IngestionPagination()
    page = paginator.paginate_queryset(queryset, request, view=view)
    serializer = serializer_class(page, many=True)
    return success(message, paginator.get_paginated_response(serializer.data).data)
