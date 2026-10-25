"""CredentialProvider over the OS secret store via ``keyring`` (SEC-SEC-001, ADR-014).

On Windows the default backend is the Windows Credential Manager. Only derived credential
handles reach the store; values never leave this adapter except as ``SecretValue``.
"""

from __future__ import annotations

from typing import Final

import keyring
from keyring.backend import KeyringBackend
from keyring.errors import PasswordDeleteError

from coach.application.credentials import check_handle
from coach.application.secrets import SecretValue

SERVICE_NAME: Final = "CoachDeportivo"


class KeyringCredentialProvider:
    def __init__(
        self, backend: KeyringBackend | None = None, *, service: str = SERVICE_NAME
    ) -> None:
        self._backend = backend if backend is not None else keyring.get_keyring()
        self._service = service

    def get(self, handle: str) -> SecretValue | None:
        value = self._backend.get_password(self._service, check_handle(handle))
        return SecretValue(value) if value else None

    def put(self, handle: str, value: SecretValue) -> None:
        self._backend.set_password(self._service, check_handle(handle), value.reveal())

    def delete(self, handle: str) -> None:
        try:
            self._backend.delete_password(self._service, check_handle(handle))
        except PasswordDeleteError:
            return
