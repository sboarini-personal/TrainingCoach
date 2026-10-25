"""Entity identifiers: UUIDv7 (RFC 9562), time-ordered and not guessable (SEC-IDOR-002).

Identifiers are defence in depth only; ownership checks are the access control.
"""

from __future__ import annotations

import secrets
import time
import uuid

_RANDOM_BYTES = 10


def uuid7(unix_ms: int, random_bytes: bytes) -> uuid.UUID:
    """Build a version-7 UUID from a millisecond timestamp and 10 random bytes."""
    if len(random_bytes) != _RANDOM_BYTES:
        raise ValueError(f"uuid7 needs exactly {_RANDOM_BYTES} random bytes")
    if not 0 <= unix_ms < 1 << 48:
        raise ValueError("timestamp out of range for UUIDv7")
    rand = int.from_bytes(random_bytes, "big")
    rand_a = (rand >> 68) & 0xFFF
    rand_b = rand & ((1 << 62) - 1)
    value = (unix_ms << 80) | (0x7 << 76) | (rand_a << 64) | (0b10 << 62) | rand_b
    return uuid.UUID(int=value)


def timestamp_ms(identifier: uuid.UUID) -> int:
    return identifier.int >> 80


def new_id() -> uuid.UUID:
    return uuid7(time.time_ns() // 1_000_000, secrets.token_bytes(_RANDOM_BYTES))
