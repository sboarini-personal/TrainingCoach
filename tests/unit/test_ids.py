import uuid

import pytest

from coach.application.ids import new_id, timestamp_ms, uuid7

pytestmark = pytest.mark.req("SEC-IDOR-002")


def test_new_ids_are_rfc9562_version_7() -> None:
    identifier = new_id()
    assert identifier.version == 7
    assert identifier.variant == uuid.RFC_4122


def test_uuid7_encodes_its_timestamp_and_sorts_by_time() -> None:
    earlier = uuid7(1_700_000_000_000, bytes(10))
    later = uuid7(1_700_000_000_001, bytes(10))
    assert earlier < later
    assert timestamp_ms(earlier) == 1_700_000_000_000


def test_ids_are_unique() -> None:
    assert len({new_id() for _ in range(10_000)}) == 10_000


def test_uuid7_requires_ten_random_bytes() -> None:
    with pytest.raises(ValueError, match="10 random bytes"):
        uuid7(1_700_000_000_000, b"short")
