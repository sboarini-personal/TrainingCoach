"""Application error types. Messages never reveal other athletes' data."""


class ApplicationError(Exception):
    """An application-level operation failed."""


class NotFoundError(ApplicationError):
    """The resource does not exist for this athlete (also used for foreign resources)."""

    def __init__(self) -> None:
        super().__init__("resource not found")


class ConcurrencyConflictError(ApplicationError):
    """The resource changed since it was read (NFR-REL-003)."""


class OwnershipError(ApplicationError):
    """An entity's owner does not match the authenticated owner scope."""
