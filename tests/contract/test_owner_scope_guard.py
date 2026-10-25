"""ADR-016: every repository and store method is owner-scoped; no unscoped access exists.

Discovers every class named ``*Repository`` or ``*Store`` in the application ports and in all
infrastructure modules, so a new adapter cannot skip the rule by accident.
"""

import importlib
import inspect
import pkgutil
import typing
from typing import Any

import pytest

import coach.application.ports
import coach.infrastructure
from coach.application.ownership import OwnerScope


def _guarded_classes() -> list[type[Any]]:
    modules = [coach.application.ports]
    for info in pkgutil.walk_packages(coach.infrastructure.__path__, "coach.infrastructure."):
        modules.append(importlib.import_module(info.name))
    classes: dict[str, type[Any]] = {}
    for module in modules:
        for name, value in vars(module).items():
            if (
                inspect.isclass(value)
                and value.__module__ == module.__name__
                and name.endswith(("Repository", "Store"))
            ):
                classes[f"{module.__name__}.{name}"] = value
    return [classes[key] for key in sorted(classes)]


GUARDED = _guarded_classes()


def test_guard_finds_the_known_adapters() -> None:
    names = {cls.__name__ for cls in GUARDED}
    assert {
        "OwnedRepository",
        "RawSourceRepository",
        "DerivedValueRepository",
        "BlobStore",
        "InMemoryRepository",
        "SqliteAggregateRepository",
        "SqliteRawSourceRepository",
        "SqliteDerivedValueRepository",
        "FileBlobStore",
        "ActivityRepository",
        "ObservationRepository",
        "OverrideRepository",
        "InMemoryActivityRepository",
        "InMemoryObservationRepository",
        "InMemoryOverrideRepository",
        "SqliteActivityRepository",
        "SqliteObservationRepository",
        "SqliteOverrideRepository",
        "IngestionRecordRepository",
        "InMemoryIngestionRecordRepository",
        "SqliteIngestionRecordRepository",
    } <= names


@pytest.mark.req("SEC-IDOR-001", "SEC-AUTHZ-001")
@pytest.mark.parametrize("cls", GUARDED, ids=lambda cls: cls.__name__)
def test_every_public_method_takes_the_owner_scope_first(cls: type[Any]) -> None:
    # PEP 695 type parameters (T) live in the class scope; expose them to the resolver.
    type_params = {param.__name__: param for param in getattr(cls, "__type_params__", ())}
    methods = inspect.getmembers(cls, predicate=inspect.isfunction)
    public = [(name, member) for name, member in methods if not name.startswith("_")]
    assert public, f"{cls.__name__} has no public methods"
    for name, member in public:
        parameters = list(inspect.signature(member).parameters)
        hints = typing.get_type_hints(member, localns=type_params)
        assert parameters[1:2] == ["scope"], f"{cls.__name__}.{name} lacks the owner scope"
        assert hints["scope"] is OwnerScope, f"{cls.__name__}.{name} scope is not an OwnerScope"
