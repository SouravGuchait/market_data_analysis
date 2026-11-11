"""HTTP endpoints for user-owned portfolios."""

from __future__ import annotations

import uuid
from typing import Any, cast

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

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
    data: Any = None,
    status_code: int = status.HTTP_200_OK,
) -> Response:
    return Response(
        {"success": True, "message": message, "data": data, "errors": None},
        status=status_code,
    )


def failure(
    message: str,
    errors: Any,
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

        validated_data = cast(dict[str, Any], serializer.validated_data)
        portfolio = create_portfolio(owner=request.user, data=validated_data)
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

        validated_data = cast(dict[str, Any], serializer.validated_data)
        portfolio = update_portfolio(portfolio, validated_data)
        return success("Portfolio updated", PortfolioReadSerializer(portfolio).data)

    def delete(self, request: Request, portfolio_id: uuid.UUID) -> Response:
        portfolio = self.get_owned_portfolio(request, portfolio_id)
        if portfolio is None:
            return portfolio_not_found()

        delete_portfolio(portfolio)
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
            return portfolio_not_found()

        serializer = PortfolioInstrumentWriteSerializer(data=request.data)
        if not serializer.is_valid():
            return failure("Validation failed", serializer.errors)

        validated_data = cast(dict[str, Any], serializer.validated_data)
        try:
            portfolio_instrument = add_portfolio_instrument(portfolio, validated_data)
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

        remove_portfolio_instrument(portfolio_instrument)
        return Response(status=status.HTTP_204_NO_CONTENT)
