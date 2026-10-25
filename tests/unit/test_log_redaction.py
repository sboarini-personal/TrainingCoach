import io
import logging

import pytest

from coach.application.secrets import SecretValue
from coach.infrastructure.logs import RedactingFormatter

pytestmark = pytest.mark.req("NFR-OBS-002", "SEC-SEC-003")

TOKEN = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJzeW50aGV0aWMifQ.c3ludGhldGljc2lnbmF0dXJl"  # pragma: allowlist secret
BEARER_VALUE = "Zm9vYmFyYmF6cXV4c3ludGhldGlj"  # pragma: allowlist secret


def _logger() -> tuple[logging.Logger, io.StringIO]:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(RedactingFormatter("%(levelname)s %(message)s"))
    logger = logging.getLogger(f"test-redaction-{id(stream)}")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    return logger, stream


def test_secrets_in_messages_and_arguments_are_redacted() -> None:
    logger, stream = _logger()
    logger.debug("provider answered with token %s", TOKEN)
    logger.info("Authorization: Bearer %s", BEARER_VALUE)
    output = stream.getvalue()
    assert TOKEN not in output
    assert BEARER_VALUE not in output


def test_secrets_in_exception_tracebacks_are_redacted() -> None:
    logger, stream = _logger()
    try:
        raise RuntimeError(f"request failed for api_key={TOKEN}")
    except RuntimeError:
        logger.exception("sync failed")
    output = stream.getvalue()
    assert "Traceback" in output
    assert TOKEN not in output


def test_secret_values_are_masked_in_log_arguments() -> None:
    logger, stream = _logger()
    logger.info("using key %s", SecretValue("short-synthetic"))
    assert "short-synthetic" not in stream.getvalue()
