"""HTTP endpoints for user-owned portfolios."""

from __future__ import annotations

import uuid
from collections.abc import Mapping

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.audit.models import AuditAction, AuditResourceType, AuditStatus
from apps.audit.services import record_audit_event
from apps.portfolios.models import Portfolio
from apps.portfolios.pagination import PortfolioPagination
from apps.portfolios.selectors import (
    get_portfolio_instrument,
    get_user_portfolio,
    list_portfolio_instruments,
    list_user_portfolios,
)
from apps.portfolios.serializers import (
    PortfolioInstrumentReadSerializer,
    PortfolioInstrumentWriteSerializer,
    PortfolioReadSerializer,
    PortfolioWriteSerializer,
)
from apps.portfolios.services import (
    DuplicatePortfolioInstrumentError,
    add_portfolio_instrument,
    create_portfolio,
    delete_portfolio,
    remove_portfolio_instrument,
    update_portfolio,
)


def success(
    message: str,
    data: object = None,
    status_code: int = status.HTTP_200_OK,
) -> Response:
    return Response(
        {"success": True, "message": message, "data": data, "errors": None},
        status=status_code,
    )


def failure(
    message: str,
    errors: object,
    status_code: int = status.HTTP_400_BAD_REQUEST,
) -> Response:
    return Response(
        {"success": False, "message": message, "data": None, "errors": errors},
        status=status_code,
    )


def portfolio_not_found() -> Response:
    """Use one indistinguishable response for missing and foreign portfolios."""

    return failure("Portfolio not found", {"detail": ["Not found."]}, status.HTTP_404_NOT_FOUND)


class PortfolioAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get_owned_portfolio(self, request: Request, portfolio_id: uuid.UUID) -> Portfolio | None:
        return get_user_portfolio(request.user, portfolio_id)


class PortfolioListCreateView(PortfolioAPIView):
    def get(self, request: Request) -> Response:
        queryset = list_user_portfolios(request.user)
        paginator = PortfolioPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        serializer = PortfolioReadSerializer(page, many=True)
        paginated = paginator.get_paginated_response(serializer.data).data
        return success("Portfolios retrieved", paginated)

    def post(self, request: Request) -> Response:
        serializer = PortfolioWriteSerializer(data=request.data)
        if not serializer.is_valid():
            return failure("Validation failed", serializer.errors)

        validated_data = _validated_mapping(serializer.validated_data)
        with transaction.atomic():
            portfolio = create_portfolio(owner=request.user, data=validated_data)
            record_audit_event(
                actor=request.user,
                action=AuditAction.PORTFOLIO_CREATED,
                resource_type=AuditResourceType.PORTFOLIO,
                resource_id=portfolio.pk,
                status=AuditStatus.SUCCESS,
                request=request,
                metadata={"name": portfolio.name},
            )
        return success(
            "Portfolio created",
            PortfolioReadSerializer(portfolio).data,
            status.HTTP_201_CREATED,
        )


class PortfolioDetailView(PortfolioAPIView):
    def get(self, request: Request, portfolio_id: uuid.UUID) -> Response:
        portfolio = self.get_owned_portfolio(request, portfolio_id)
        if portfolio is None:
            return portfolio_not_found()
        return success("Portfolio retrieved", PortfolioReadSerializer(portfolio).data)

    def patch(self, request: Request, portfolio_id: uuid.UUID) -> Response:
        portfolio = self.get_owned_portfolio(request, portfolio_id)
        if portfolio is None:
            return portfolio_not_found()

        serializer = PortfolioWriteSerializer(instance=portfolio, data=request.data, partial=True)
        if not serializer.is_valid():
            return failure("Validation failed", serializer.errors)

        validated_data = _validated_mapping(serializer.validated_data)
        changed_fields = sorted(validated_data)
        with transaction.atomic():
            portfolio = update_portfolio(portfolio, validated_data)
            record_audit_event(
                actor=request.user,
                action=AuditAction.PORTFOLIO_UPDATED,
                resource_type=AuditResourceType.PORTFOLIO,
                resource_id=portfolio.pk,
                status=AuditStatus.SUCCESS,
                request=request,
                metadata={"changed_fields": changed_fields},
            )
        return success("Portfolio updated", PortfolioReadSerializer(portfolio).data)

    def delete(self, request: Request, portfolio_id: uuid.UUID) -> Response:
        portfolio = self.get_owned_portfolio(request, portfolio_id)
        if portfolio is None:
            return portfolio_not_found()

        portfolio_id = portfolio.pk
        portfolio_name = portfolio.name
        with transaction.atomic():
            delete_portfolio(portfolio)
            record_audit_event(
                actor=request.user,
                action=AuditAction.PORTFOLIO_DELETED,
                resource_type=AuditResourceType.PORTFOLIO,
                resource_id=portfolio_id,
                status=AuditStatus.SUCCESS,
                request=request,
                metadata={"name": portfolio_name},
            )
        return Response(status=status.HTTP_204_NO_CONTENT)


class PortfolioInstrumentListCreateView(PortfolioAPIView):
    def get(self, request: Request, portfolio_id: uuid.UUID) -> Response:
        portfolio = self.get_owned_portfolio(request, portfolio_id)
        if portfolio is None:
            return portfolio_not_found()
        queryset = list_portfolio_instruments(portfolio)
        paginator = PortfolioPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        serializer = PortfolioInstrumentReadSerializer(page, many=True)
        paginated = paginator.get_paginated_response(serializer.data).data
        return success("Portfolio instruments retrieved", paginated)

    def post(self, request: Request, portfolio_id: uuid.UUID) -> Response:
        portfolio = self.get_owned_portfolio(request, portfolio_id)
        if portfolio is None:
            response = portfolio_not_found()
        else:
            serializer = PortfolioInstrumentWriteSerializer(data=request.data)
            if not serializer.is_valid():
                response = failure("Validation failed", serializer.errors)
            else:
                validated_data = _validated_mapping(serializer.validated_data)
                response = self._create_portfolio_instrument(request, portfolio, validated_data)
        return response

    @staticmethod
    def _create_portfolio_instrument(
        request: Request,
        portfolio: Portfolio,
        validated_data: dict[str, object],
    ) -> Response:
        try:
            with transaction.atomic():
                portfolio_instrument = add_portfolio_instrument(portfolio, validated_data)
                record_audit_event(
                    actor=request.user,
                    action=AuditAction.PORTFOLIO_INSTRUMENT_ADDED,
                    resource_type=AuditResourceType.PORTFOLIO_INSTRUMENT,
                    resource_id=portfolio_instrument.pk,
                    status=AuditStatus.SUCCESS,
                    request=request,
                    metadata={
                        "portfolio_id": str(portfolio.pk),
                        "instrument_symbol": portfolio_instrument.instrument.symbol,
                        "exchange_code": portfolio_instrument.exchange.code,
                    },
                )
        except DuplicatePortfolioInstrumentError:
            return failure(
                "Portfolio instrument already exists",
                {"instrument_id": ["This instrument is already in the portfolio."]},
                status.HTTP_409_CONFLICT,
            )
        except DjangoValidationError as exc:
            return failure("Validation failed", exc.message_dict)
        return success(
            "Portfolio instrument created",
            PortfolioInstrumentReadSerializer(portfolio_instrument).data,
            status.HTTP_201_CREATED,
        )


class PortfolioInstrumentDetailView(PortfolioAPIView):
    def delete(
        self, request: Request, portfolio_id: uuid.UUID, portfolio_instrument_id: uuid.UUID
    ) -> Response:
        portfolio = self.get_owned_portfolio(request, portfolio_id)
        if portfolio is None:
            return portfolio_not_found()
        portfolio_instrument = get_portfolio_instrument(portfolio, portfolio_instrument_id)
        if portfolio_instrument is None:
            return failure(
                "Portfolio instrument not found",
                {"detail": ["Not found."]},
                status.HTTP_404_NOT_FOUND,
            )
        membership_id = portfolio_instrument.pk
        metadata = {
            "portfolio_id": str(portfolio.pk),
            "instrument_symbol": portfolio_instrument.instrument.symbol,
            "exchange_code": portfolio_instrument.exchange.code,
        }
        with transaction.atomic():
            remove_portfolio_instrument(portfolio_instrument)
            record_audit_event(
                actor=request.user,
                action=AuditAction.PORTFOLIO_INSTRUMENT_REMOVED,
                resource_type=AuditResourceType.PORTFOLIO_INSTRUMENT,
                resource_id=membership_id,
                status=AuditStatus.SUCCESS,
                request=request,
                metadata=metadata,
            )
        return Response(status=status.HTTP_204_NO_CONTENT)


def _validated_mapping(data: object) -> dict[str, object]:
    if not isinstance(data, Mapping):
        raise TypeError("Validated serializer data must be a mapping.")
    return {key: value for key, value in data.items() if isinstance(key, str)}
