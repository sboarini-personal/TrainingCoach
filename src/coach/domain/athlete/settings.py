"""What the per-activity analyses read from the athlete's profile (metrics spec 9.2, 10).

Heart-rate zone models with the birth date (profile spec 1.1), the power sensors the athlete
declares reliable for training (cycling spec 2), the FTP (cycling spec 3, 6.1) and the usual
pool length (swimming spec 5).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Final

from coach.domain.activity.fields import PowerSensor
from coach.domain.athlete.experience import FtpValue
from coach.domain.athlete.physiology import HeartRateZones, Sensor, SensorType

# The FTP freshness window of the onboarding spec (6 months), applied around its date.
FTP_WINDOW: Final = timedelta(days=183)
_POWER_SENSORS: Final = {
    SensorType.CYCLING_POWER_METER: PowerSensor.POWER_METER,
    SensorType.SMART_TRAINER: PowerSensor.SMART_TRAINER,
}


def reliable_power(sensors: tuple[Sensor, ...]) -> frozenset[PowerSensor]:
    """Power devices the athlete declares reliable for training."""
    return frozenset(
        _POWER_SENSORS[sensor.sensor_type]
        for sensor in sensors
        if sensor.reliable_for_training and sensor.sensor_type in _POWER_SENSORS
    )


@dataclass(frozen=True, slots=True)
class AnalysisSettings:
    zones: HeartRateZones = field(default_factory=HeartRateZones)
    reliable_power: frozenset[PowerSensor] = frozenset()
    ftp: FtpValue | None = None
    pool_length_m: float | None = None  # the athlete's usual pool (resources), P9

    def measures_power(self, sensor: object) -> bool:
        """Whether power named by the file is MEASURED: the athlete declares that kind of
        device reliable (cycling spec 2)."""
        return isinstance(sensor, PowerSensor) and sensor in self.reliable_power

    def ftp_on(self, day: date) -> FtpValue | None:
        """The FTP when the training day is within its freshness window, else ``None``."""
        ftp = self.ftp
        if ftp is None or abs(day - ftp.on) > FTP_WINDOW:
            return None
        return ftp
