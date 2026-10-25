"""Logging with redaction (NFR-OBS-002).

The formatter redacts the complete formatted record, including arguments and exception
tracebacks, so no code path can log a secret by accident.
"""

from __future__ import annotations

import logging

from coach.application.redaction import redact


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))
