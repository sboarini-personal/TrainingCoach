"""keyring adapter, exercised against an in-memory backend: the real OS store is never touched."""

import pytest
from keyring.backend import KeyringBackend
from keyring.errors import PasswordDeleteError

from coach.application.credentials import CredentialKind, credential_handle
from coach.application.secrets import SecretValue
from coach.infrastructure.keyring_credentials import SERVICE_NAME, KeyringCredentialProvider
from samples import ATHLETE

pytestmark = pytest.mark.req("SEC-SEC-001")

HANDLE = credential_handle(CredentialKind.GARMIN_PERSONAL_API_KEY, ATHLETE)
VALUE = SecretValue("synthetic-keyring-value-for-tests-0001")  # pragma: allowlist secret


class MemoryKeyring(KeyringBackend):
    priority = 1

    def __init__(self) -> None:
        self.items: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.items.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.items[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        if (service, username) not in self.items:
            raise PasswordDeleteError(username)
        del self.items[(service, username)]


@pytest.fixture
def backend() -> MemoryKeyring:
    return MemoryKeyring()


def test_values_round_trip_under_the_service_name(backend: MemoryKeyring) -> None:
    provider = KeyringCredentialProvider(backend)
    provider.put(HANDLE, VALUE)
    assert backend.items == {(SERVICE_NAME, HANDLE): VALUE.reveal()}
    assert provider.get(HANDLE) == VALUE
    provider.delete(HANDLE)
    assert provider.get(HANDLE) is None
    provider.delete(HANDLE)


@pytest.mark.parametrize(
    "handle", ["", "garmin", "../garmin_personal_api_key/x", f"other/{ATHLETE}"]
)
def test_only_derived_handles_reach_the_store(backend: MemoryKeyring, handle: str) -> None:
    provider = KeyringCredentialProvider(backend)
    with pytest.raises(ValueError, match="handle"):
        provider.get(handle)
    assert backend.items == {}


def test_empty_stored_value_is_treated_as_missing(backend: MemoryKeyring) -> None:
    backend.items[(SERVICE_NAME, HANDLE)] = ""
    assert KeyringCredentialProvider(backend).get(HANDLE) is None
