"""Supported sports (spec/integration/canonical-model.spec.md section 3)."""

from enum import StrEnum


class Sport(StrEnum):
    RUNNING = "RUNNING"
    CYCLING = "CYCLING"
    SWIMMING = "SWIMMING"
    STRENGTH = "STRENGTH"
    TENNIS = "TENNIS"
    OTHER = "OTHER"
