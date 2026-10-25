"""Untrusted free text: control characters removed, length capped (SEC-INJ-001)."""

from __future__ import annotations

import re

from coach.domain.common.errors import DomainValidationError

_CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def clean_text(value: str, *, max_length: int, field: str) -> str:
    """Return ``value`` without control characters; reject empty or over-long text."""
    cleaned = _CONTROL_CHARACTERS.sub("", value).strip()
    if not cleaned:
        raise DomainValidationError(f"{field} must not be empty")
    if len(cleaned) > max_length:
        raise DomainValidationError(f"{field} exceeds {max_length} characters")
    return cleaned


def clean_optional_text(value: str | None, *, max_length: int, field: str) -> str | None:
    """Like ``clean_text`` but ``None`` and blank text become ``None``."""
    if value is None or not _CONTROL_CHARACTERS.sub("", value).strip():
        return None
    return clean_text(value, max_length=max_length, field=field)
