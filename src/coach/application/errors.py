"""Application error types. Messages never reveal other athletes' data."""


class ApplicationError(Exception):
    """An application-level operation failed."""


class NotFoundError(ApplicationError):
    """The resource does not exist for this athlete (also used for foreign resources)."""

    def __init__(self) -> None:
        super().__init__("resource not found")


class ConcurrencyConflictError(ApplicationError):
    """The resource changed since it was read (NFR-REL-003)."""


class InvalidOperationError(ApplicationError):
    """The operation does not apply to the resource in its current state."""


class OwnershipError(ApplicationError):
    """An entity's owner does not match the authenticated owner scope."""


class RateLimitedError(ApplicationError):
    """Too many requests or failed authentications; retry later (SEC-AUTH-003)."""

    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__(f"rate limited; retry after {retry_after_seconds} s")
        self.retry_after_seconds = retry_after_seconds
