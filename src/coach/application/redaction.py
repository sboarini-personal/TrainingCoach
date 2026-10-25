"""Deterministic secret detection and redaction (spec/security/secrets.spec.md section 3.1).

Used for log redaction and, from P4, for screening athlete messages before they are stored
or sent to the LLM. Rules are ordered: specific formats first, high entropy last.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Final

REDACTED: Final = "[REDACTED-SECRET]"

_PEM = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}")
_SCHEME = re.compile(r"(?P<keep>\b(?:Bearer|ApiKey|Basic)\s+)(?P<value>[A-Za-z0-9._~+/=-]{8,})")
_PREFIXED = re.compile(
    r"\b(?:sk-ant-[A-Za-z0-9_-]{10,}|ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}"
    r"|AKIA[0-9A-Z]{16})"
)
_NAMED = re.compile(
    r"(?i)(?P<keep>\b(?:api[_-]?key|client[_-]?secret|secret|password|passwd|pwd"
    r"|contrase(?:ñ|n)a|access[_-]?token|refresh[_-]?token|token|cookie|authorization)\b[\"']?\s*[:=]\s*[\"']?)"
    r"(?!(?:Bearer|ApiKey|Basic)\b|\[REDACTED)"
    r"(?P<value>[^\s\"',;}]+)"
)
_ALNUM_RUN = re.compile(r"[A-Za-z0-9]{20,}")
_MIN_ENTROPY_BITS: Final = 3.5


def _entropy(text: str) -> float:
    counts = Counter(text)
    length = len(text)
    return -sum(count / length * math.log2(count / length) for count in counts.values())


def _is_high_entropy(run: str) -> bool:
    has_letter = any(char.isalpha() for char in run)
    has_digit = any(char.isdigit() for char in run)
    return has_letter and has_digit and _entropy(run) >= _MIN_ENTROPY_BITS


def redact(text: str) -> str:
    """Replace probable secrets in ``text`` with ``REDACTED``."""
    text = _PEM.sub(REDACTED, text)
    text = _JWT.sub(REDACTED, text)
    text = _SCHEME.sub(lambda match: match["keep"] + REDACTED, text)
    text = _PREFIXED.sub(REDACTED, text)
    text = _NAMED.sub(lambda match: match["keep"] + REDACTED, text)
    return _ALNUM_RUN.sub(lambda match: REDACTED if _is_high_entropy(match[0]) else match[0], text)


def contains_secret(text: str) -> bool:
    return redact(text) != text
