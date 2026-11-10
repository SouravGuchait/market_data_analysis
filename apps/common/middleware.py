"""Request correlation middleware."""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from uuid import uuid4

from django.http import HttpRequest, HttpResponse

from .logging import reset_request_id, set_request_id

_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

logger = logging.getLogger("apps.request")


class RequestIDMiddleware:
    """Accept or generate a correlation ID and echo it on the response."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        incoming = request.headers.get("X-Request-ID", "")
        request_id = incoming if _REQUEST_ID_PATTERN.fullmatch(incoming) else str(uuid4())
        request.__dict__["request_id"] = request_id

        token = set_request_id(request_id)
        started_at = time.perf_counter()

        try:
            response = self.get_response(request)
        except Exception:
            duration_ms = round((time.perf_counter() - started_at) * 1000, 2)
            logger.exception(
                "request_failed",
                extra={
                    "method": request.method,
                    "path": request.path,
                    "duration_ms": duration_ms,
                },
            )
            raise
        else:
            duration_ms = round((time.perf_counter() - started_at) * 1000, 2)
            logger.info(
                "request_completed",
                extra={
                    "method": request.method,
                    "path": request.path,
                    "status_code": response.status_code,
                    "duration_ms": duration_ms,
                },
            )
            response["X-Request-ID"] = request_id
            return response
        finally:
            reset_request_id(token)
