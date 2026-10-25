"""Load the JSON Schemas in spec/contracts and validate instances against them.

Shared by the contract tests so every test resolves cross-schema references the same way.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from specindex import SPEC_DIR

CONTRACTS_DIR = SPEC_DIR / "contracts"
EXAMPLES_DIR = CONTRACTS_DIR / "examples"


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


SCHEMAS: dict[str, dict[str, Any]] = {
    path.name.removesuffix(".schema.json"): load_json(path)
    for path in sorted(CONTRACTS_DIR.glob("*.schema.json"))
}
REGISTRY: Registry[Any] = Registry().with_resources(
    (schema["$id"], Resource.from_contents(schema)) for schema in SCHEMAS.values()
)


def validation_errors(schema_name: str, instance: Any) -> list[str]:
    validator = Draft202012Validator(
        SCHEMAS[schema_name],
        registry=REGISTRY,
        format_checker=Draft202012Validator.FORMAT_CHECKER,
    )
    return [
        f"{list(error.absolute_path)}: {error.message}" for error in validator.iter_errors(instance)
    ]


def enum_at(schema_name: str, *path: str) -> set[str]:
    """Non-null enum values at ``path`` inside a schema, following local ``$ref``s."""
    node: Any = SCHEMAS[schema_name]
    for key in path:
        node = node[key]
        while isinstance(node, dict) and "$ref" in node and "enum" not in node:
            ref: str = node["$ref"]
            target_schema, _, pointer = ref.partition("#")
            root = (
                SCHEMAS[schema_name]
                if not target_schema
                else next(s for s in SCHEMAS.values() if s["$id"] == target_schema)
            )
            node = root
            for part in pointer.strip("/").split("/"):
                node = node[part]
    return {value for value in node["enum"] if value is not None}
