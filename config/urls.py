"""Project URL configuration."""

from __future__ import annotations

from django.conf import settings
from django.contrib import admin
from django.http import HttpResponse
from django.urls import include, path
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)

prefix = settings.API_PREFIX
urlpatterns = [
    path("admin/", admin.site.urls),
    path(prefix + "auth/", include("apps.accounts.urls")),
    path(prefix + "audit/", include("apps.audit.urls")),
    path(prefix + "exchanges/", include("apps.exchanges.urls")),
    path(prefix + "instruments/", include("apps.instruments.urls")),
    path(prefix + "ingestion/", include("apps.ingestion.urls")),
    path(prefix + "portfolios/", include("apps.portfolios.urls")),
    path(prefix + "analytics/", include("apps.analytics.urls")),
    path(prefix + "dashboard/", include("apps.dashboard.urls")),
    path(prefix + "market-data/", include("apps.market_data.urls")),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
    path("api/docs/redoc/", SpectacularRedocView.as_view(url_name="schema"), name="redoc"),
    path(prefix + "health/", include("apps.common.urls"), name="health"),
    path("health", lambda request: HttpResponse("OK", content_type="text/plain")),
]

if settings.ENABLE_VALIDATION_RULES:
    urlpatterns.insert(
        6,
        path(prefix + "rules/", include("apps.validation_rules.urls")),
    )
