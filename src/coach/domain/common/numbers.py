"""Numeric guards. NaN and infinity are never valid; UNKNOWN is ``None`` (FR-PRF-002)."""

from __future__ import annotations

import math

from coach.domain.common.errors import DomainValidationError


def require_finite(value: float, name: str) -> float:
    """Return ``value``; reject NaN and infinity, which slip through ``<``/``>`` range checks."""
    if not math.isfinite(value):
        raise DomainValidationError(f"{name} must be a finite number")
    return value
