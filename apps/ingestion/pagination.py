"""Pagination for ingestion collections."""

from __future__ import annotations

from rest_framework.pagination import PageNumberPagination


class IngestionPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100
