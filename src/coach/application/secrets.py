"""Secret value wrapper (spec/security/secrets.spec.md section 2).

A ``SecretValue`` never renders its content: ``str``, ``repr``, formatting and logging show
``***``. Serialisation is refused. The raw value is available only through ``reveal()``,
called by the component that sends it to its destination.
"""

from __future__ import annotations

import hmac
from typing import Final, NoReturn, final

_MASK: Final = "***"


@final
class SecretValue:
    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        if not value:
            raise ValueError("secret value must not be empty")
        self._value = value

    def reveal(self) -> str:
        return self._value

    def __str__(self) -> str:
        return _MASK

    def __repr__(self) -> str:
        return f"SecretValue({_MASK!r})"

    def __format__(self, format_spec: str) -> str:
        return format(_MASK, format_spec)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, SecretValue):
            return NotImplemented
        return hmac.compare_digest(self._value.encode(), other._value.encode())

    def __hash__(self) -> int:
        return hash(SecretValue)

    def __reduce__(self) -> NoReturn:
        raise TypeError("SecretValue cannot be serialised")
