"""API client error types."""

from __future__ import annotations


class ApiError(Exception):
    """Raised when the Django API returns an unsuccessful response."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class AuthExpiredError(ApiError):
    """Raised when JWT refresh fails or the session is no longer valid."""
