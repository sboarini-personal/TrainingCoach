"""The daily check-in on its documented scale (recovery spec section 1; FR-REC-001).

Items are integers 1 to 5 whose direction is explicit (``HIGHER_IS_BETTER``): sleep quality
and motivation are better when higher, fatigue, soreness and stress worse. Pain is 0 to 10
with an untrusted free-text location. Red flags are the explicit answers to the check-in's
red-flag question (safety spec section 2); severe pain of sudden onset is one by itself.

A check-in is immutable: a correction is a later check-in of the same training day that
supersedes it. Every value is optional (UNKNOWN), but a check-in states at least one.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import date, datetime
from enum import StrEnum
from typing import Final, Self
from uuid import UUID

from coach.domain.common.errors import DomainValidationError
from coach.domain.common.text import clean_optional_text
from coach.domain.common.time import ensure_utc

SCALE_MIN: Final = 1
SCALE_MAX: Final = 5
MAX_PAIN: Final = 10
SEVERE_PAIN: Final = 7  # with sudden onset: RF_SEVERE_ACUTE_PAIN (safety spec 2)
MAX_LOCATION: Final = 100
MAX_NOTES: Final = 1000


class CheckInItem(StrEnum):
    SLEEP_QUALITY = "sleepQuality"
    FATIGUE = "fatigue"
    SORENESS = "soreness"
    STRESS = "stress"
    MOTIVATION = "motivation"


HIGHER_IS_BETTER: Final = {
    CheckInItem.SLEEP_QUALITY: True,
    CheckInItem.FATIGUE: False,
    CheckInItem.SORENESS: False,
    CheckInItem.STRESS: False,
    CheckInItem.MOTIVATION: True,
}


class RedFlag(StrEnum):
    """Safety spec section 2; detected, never interpreted (SAFE-MED-001)."""

    RF_CHEST_PAIN = "RF_CHEST_PAIN"
    RF_FAINTING = "RF_FAINTING"
    RF_BREATHING = "RF_BREATHING"
    RF_NEURO = "RF_NEURO"
    RF_STRENGTH_LOSS = "RF_STRENGTH_LOSS"
    RF_SEVERE_ACUTE_PAIN = "RF_SEVERE_ACUTE_PAIN"
    RF_PALPITATIONS = "RF_PALPITATIONS"


class PainOnset(StrEnum):
    SUDDEN = "SUDDEN"
    GRADUAL = "GRADUAL"


def _scale(value: int | None, name: str) -> None:
    if value is not None and (
        isinstance(value, bool) or not isinstance(value, int) or not SCALE_MIN <= value <= SCALE_MAX
    ):
        raise DomainValidationError(f"{name} is a whole number from {SCALE_MIN} to {SCALE_MAX}")


@dataclass(frozen=True, slots=True, kw_only=True)
class CheckInPain:
    intensity: int
    location: str | None = None  # the athlete's words: untrusted, never interpreted
    onset: PainOnset | None = None

    def __post_init__(self) -> None:
        value = self.intensity
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_PAIN:
            raise DomainValidationError(f"pain intensity is a whole number from 0 to {MAX_PAIN}")
        object.__setattr__(
            self,
            "location",
            clean_optional_text(self.location, max_length=MAX_LOCATION, field="pain location"),
        )

    @property
    def severe_and_sudden(self) -> bool:
        return self.intensity >= SEVERE_PAIN and self.onset is PainOnset.SUDDEN


@dataclass(frozen=True, slots=True, kw_only=True)
class DailyCheckIn:
    checkin_id: UUID
    athlete_id: UUID
    recorded_at: datetime
    training_day: date
    sleep_quality: int | None = None
    fatigue: int | None = None
    soreness: int | None = None
    stress: int | None = None
    motivation: int | None = None
    pain: CheckInPain | None = None  # None: no pain flagged (or not answered)
    pain_flag: bool | None = None  # None: the question was not answered
    red_flags: tuple[RedFlag, ...] = ()
    notes: str | None = None  # untrusted, screened for secrets before storage
    supersedes: UUID | None = None
    version: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "recorded_at", ensure_utc(self.recorded_at))
        for item in CheckInItem:
            _scale(self.item(item), item.value)
        if self.pain is not None and self.pain_flag is not True:
            raise DomainValidationError("pain details need the pain flag")
        if len(set(self.red_flags)) != len(self.red_flags):
            raise DomainValidationError("a red flag is listed once")
        object.__setattr__(
            self, "notes", clean_optional_text(self.notes, max_length=MAX_NOTES, field="notes")
        )
        stated = [self.item(item) for item in CheckInItem]
        if (
            all(value is None for value in stated)
            and self.pain_flag is None
            and not (self.red_flags or self.notes)
        ):
            raise DomainValidationError("a check-in states at least one answer")

    @property
    def entity_id(self) -> UUID:
        return self.checkin_id

    def with_version(self, version: int) -> Self:
        return replace(self, version=version)

    def item(self, item: CheckInItem) -> int | None:
        return {
            CheckInItem.SLEEP_QUALITY: self.sleep_quality,
            CheckInItem.FATIGUE: self.fatigue,
            CheckInItem.SORENESS: self.soreness,
            CheckInItem.STRESS: self.stress,
            CheckInItem.MOTIVATION: self.motivation,
        }[item]

    def red_flags_raised(self) -> tuple[RedFlag, ...]:
        """The explicit red flags plus severe pain of sudden onset (safety spec 2)."""
        flags = list(self.red_flags)
        if self.pain is not None and self.pain.severe_and_sudden:
            flags.append(RedFlag.RF_SEVERE_ACUTE_PAIN)
        return tuple(dict.fromkeys(flags))


def latest_per_day(checkins: Iterable[DailyCheckIn]) -> dict[date, DailyCheckIn]:
    """The check-in in force for each training day: the latest one not superseded."""
    superseded = {item.supersedes for item in checkins if item.supersedes is not None}
    found: dict[date, DailyCheckIn] = {}
    for item in sorted(checkins, key=lambda entry: (entry.recorded_at, str(entry.checkin_id))):
        if item.checkin_id not in superseded:
            found[item.training_day] = item
    return found
