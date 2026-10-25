"""Logging with redaction (NFR-OBS-002).

The formatter redacts the complete formatted record, including arguments and exception
tracebacks, so no code path can log a secret by accident.
"""

from __future__ import annotations

import logging
from pathlib import Path

from coach.application.redaction import redact


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


def attach_file_log(path: Path) -> logging.Handler:
    """Send ``coach.*`` records at INFO and above to ``path``, redacted."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setLevel(logging.INFO)
    handler.setFormatter(RedactingFormatter(_LOG_FORMAT))
    logger = logging.getLogger("coach")
    if logger.level == logging.NOTSET:
        logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    return handler


def detach_file_log(handler: logging.Handler) -> None:
    logging.getLogger("coach").removeHandler(handler)
    handler.close()
