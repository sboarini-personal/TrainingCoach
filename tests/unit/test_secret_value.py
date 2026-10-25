import json
import pickle

import pytest

from coach.application.secrets import SecretValue

pytestmark = pytest.mark.req("SEC-SEC-001", "SEC-SEC-003")

RAW = "synthetic-secret-value-123"  # pragma: allowlist secret


def test_secret_value_never_renders_its_content() -> None:
    secret = SecretValue(RAW)
    for rendered in (str(secret), repr(secret), f"{secret}", f"{secret!r}", format(secret, ">40")):
        assert RAW not in rendered


def test_secret_value_reveals_only_on_explicit_request() -> None:
    assert SecretValue(RAW).reveal() == RAW


def test_secret_value_cannot_be_serialised() -> None:
    with pytest.raises(TypeError):
        json.dumps({"key": SecretValue(RAW)})
    with pytest.raises(TypeError):
        pickle.dumps(SecretValue(RAW))


def test_empty_secret_is_rejected() -> None:
    with pytest.raises(ValueError, match="empty"):
        SecretValue("")


def test_secret_values_compare_by_content() -> None:
    assert SecretValue(RAW) == SecretValue(RAW)
    assert SecretValue(RAW) != SecretValue(RAW + "x")
