"""Event sink writing one structured line per event to the ``coach.events`` logger."""

from __future__ import annotations

import logging
from collections.abc import Mapping

from coach.application.events import EventName, check_fields

_log = logging.getLogger("coach.events")


class LoggingEventSink:
    def emit(self, name: EventName, fields: Mapping[str, str]) -> None:
        checked = check_fields(fields)
        parts = [name.value, *(f"{key}={value}" for key, value in sorted(checked.items()))]
        _log.info("%s", " ".join(parts))
