"""Domain error types."""


class DomainError(Exception):
    """A domain rule was violated."""


class DomainValidationError(DomainError, ValueError):
    """A value violates a domain invariant."""


class InvalidTransitionError(DomainError):
    """A state transition is not allowed."""
