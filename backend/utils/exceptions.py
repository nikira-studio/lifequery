"""Custom exceptions for LifeQuery.

This module provides a hierarchy of exceptions for consistent error handling
across the application. All exceptions include both user-facing messages
and detailed internal information.
"""

from typing import Any, Optional


class LifeQueryError(Exception):
    """Base exception for LifeQuery application errors.

    Attributes:
        message: User-facing error message
        detail: Optional detailed internal information
        status_code: HTTP status code (for API responses)
    """

    def __init__(
        self,
        message: str,
        detail: Optional[str] = None,
        status_code: int = 500,
    ):
        self.message = message
        self.detail = detail
        self.status_code = status_code
        super().__init__(message)

    def to_dict(self) -> dict[str, Any]:
        """Convert exception to dictionary for JSON responses."""
        result = {"error": self.message}
        if self.detail:
            result["detail"] = self.detail
        return result


class ConfigurationError(LifeQueryError):
    """Raised when there's a configuration issue."""

    def __init__(self, message: str, detail: Optional[str] = None):
        super().__init__(message, detail, status_code=500)


class SettingsError(ConfigurationError):
    """Raised when settings are invalid or missing."""

    def __init__(self, message: str, detail: Optional[str] = None):
        super().__init__(message, detail)


class DatabaseError(LifeQueryError):
    """Raised when there's a database operation error."""

    def __init__(self, message: str, detail: Optional[str] = None):
        super().__init__(message, detail, status_code=500)


class NotFoundError(LifeQueryError):
    """Raised when a requested resource is not found."""

    def __init__(self, resource: str, identifier: Any = None):
        msg = f"{resource} not found"
        if identifier is not None:
            msg += f": {identifier}"
        super().__init__(msg, status_code=404)


class ValidationError(LifeQueryError):
    """Raised when input validation fails."""

    def __init__(self, message: str, field: Optional[str] = None):
        detail = f"Validation failed for field: {field}" if field else None
        super().__init__(message, detail, status_code=400)


class AuthenticationError(LifeQueryError):
    """Raised when authentication fails."""

    def __init__(self, message: str = "Authentication failed"):
        super().__init__(message, status_code=401)


class RateLimitError(LifeQueryError):
    """Raised when rate limit is exceeded."""

    def __init__(
        self, message: str = "Rate limit exceeded", retry_after: Optional[int] = None
    ):
        detail = f"Retry after {retry_after} seconds" if retry_after else None
        super().__init__(message, detail, status_code=429)


class EmbeddingError(LifeQueryError):
    """Raised when embedding operation fails."""

    def __init__(self, message: str, detail: Optional[str] = None):
        super().__init__(message, detail, status_code=500)


class VectorStoreError(LifeQueryError):
    """Raised when vector store operation fails."""

    def __init__(self, message: str, detail: Optional[str] = None):
        super().__init__(message, detail, status_code=500)


class LLMError(LifeQueryError):
    """Raised when LLM operation fails."""

    def __init__(self, message: str, detail: Optional[str] = None):
        super().__init__(message, detail, status_code=500)


class TelegramError(LifeQueryError):
    """Raised when Telegram API operation fails."""

    def __init__(self, message: str, detail: Optional[str] = None):
        super().__init__(message, detail, status_code=500)


class SyncError(LifeQueryError):
    """Raised when sync operation fails."""

    def __init__(self, message: str, detail: Optional[str] = None):
        super().__init__(message, detail, status_code=500)


# Exception to HTTP status code mapping
EXCEPTION_STATUS_CODES: dict[type[LifeQueryError], int] = {
    ConfigurationError: 500,
    SettingsError: 500,
    DatabaseError: 500,
    NotFoundError: 404,
    ValidationError: 400,
    AuthenticationError: 401,
    RateLimitError: 429,
    EmbeddingError: 500,
    VectorStoreError: 500,
    LLMError: 500,
    TelegramError: 500,
    SyncError: 500,
}


def get_status_code(exception: LifeQueryError) -> int:
    """Get HTTP status code for an exception.

    Args:
        exception: A LifeQueryError instance

    Returns:
        HTTP status code
    """
    for exc_type, code in EXCEPTION_STATUS_CODES.items():
        if isinstance(exception, exc_type):
            return code
    return 500
