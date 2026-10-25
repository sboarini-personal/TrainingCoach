"""The application's own CSV templates (spec/integration/files.spec.md section 8.4).

Only these templates are imported; third-party CSV exports are never guessed. The activity
template is also the export format (spec/security/prompt-injection.spec.md section 4).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

# Text a spreadsheet could read as a formula (SEC-INJ-004): quoted on export, unquoted on import.
FORMULA_PREFIXES: Final = ("=", "+", "-", "@", "\t", "\r")


@dataclass(frozen=True, slots=True)
class CsvColumn:
    name: str
    path: str  # canonical field path


@dataclass(frozen=True, slots=True)
class CsvTemplate:
    name: str
    version: str
    columns: tuple[CsvColumn, ...]

    @property
    def header(self) -> str:
        return ",".join(column.name for column in self.columns)


ACTIVITY_TEMPLATE: Final = CsvTemplate(
    name="csv_activity",
    version="1.0.0",
    columns=(
        CsvColumn("start_time_utc", "timing.startTimeUtc"),
        CsvColumn("utc_offset", "timing.utcOffset"),
        CsvColumn("sport", "classification.sport"),
        CsvColumn("subtype", "classification.subtype"),
        CsvColumn("elapsed_seconds", "timing.elapsedSeconds"),
        CsvColumn("moving_seconds", "timing.movingSeconds"),
        CsvColumn("distance_meters", "distance.meters"),
        CsvColumn("elevation_gain_meters", "elevation.gainMeters"),
        CsvColumn("average_hr", "cardiovascular.averageHr"),
        CsvColumn("max_hr", "cardiovascular.maxHr"),
        CsvColumn("rpe", "subjective.rpe"),
        CsvColumn("name", "metadata.name"),
        CsvColumn("comments", "subjective.comments"),
    ),
)
TEMPLATES: Final = (ACTIVITY_TEMPLATE,)
