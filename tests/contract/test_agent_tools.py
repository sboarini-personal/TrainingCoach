"""The tool contracts (spec/contracts/tools.spec.md): every tool's input schema is valid,
closed and names no athlete, URL, path or secret, and the catalogue in the spec and the code
agree (FR-AGT-001, FR-AGT-002, SEC-AUTHZ-002, SEC-AUTHZ-003)."""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any, cast

import pytest
from jsonschema import Draft202012Validator

from coach.agent.services import AgentServices
from coach.agent.tools import coach_tools, read_tools
from coach.agent.tools.base import FORBIDDEN_PARAMETERS, Tool, ToolKind

SPEC = Path(__file__).parents[2] / "spec" / "contracts" / "tools.spec.md"
NAME = re.compile(r"^[a-z][A-Za-z]{2,63}$")
PROHIBITED = (
    "executeSQL",
    "arbitraryHTTP",
    "callAnyURL",
    "executeShell",
    "readSecret",
    "readEntireDatabase",
    "arbitraryCode",
)


def _tools() -> list[Tool]:
    return coach_tools(cast(AgentServices, object()))  # building a tool calls no service


def _catalogue() -> dict[str, str]:
    """Tool name to kind, from the catalogue table of the spec."""
    rows = re.findall(
        r"^\| (\w+) \| (READ|WRITE_INTERNAL|PROPOSE|EXECUTE_EXTERNAL) \|",
        SPEC.read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    return dict(rows)


def _objects(node: object, path: str = "$") -> Iterator[tuple[str, Mapping[str, Any]]]:
    if isinstance(node, Mapping):
        if node.get("type") == "object" or "properties" in node:
            yield path, node
        for key, item in node.items():
            yield from _objects(item, f"{path}.{key}")
    elif isinstance(node, list):
        for index, item in enumerate(node):
            yield from _objects(item, f"{path}[{index}]")


@pytest.mark.req("FR-AGT-001", "FR-AGT-002", "SEC-AUTHZ-002", "SEC-AUTHZ-003")
@pytest.mark.acceptance("AT-IDOR-002")
@pytest.mark.parametrize("tool", _tools(), ids=lambda tool: tool.name)
def test_a_tool_input_is_a_closed_schema_without_athlete_url_path_or_secret(tool: Tool) -> None:
    assert NAME.match(tool.name)
    assert tool.description.strip()
    Draft202012Validator.check_schema(tool.input_schema)
    assert tool.input_schema["type"] == "object"
    forbidden = {name.casefold() for name in FORBIDDEN_PARAMETERS}
    for path, node in _objects(tool.input_schema):
        assert node.get("additionalProperties") is False, path
        for name in node.get("properties", {}):
            assert name.casefold() not in forbidden, f"{tool.name}{path}.{name}"
            assert "athlete" not in name.casefold(), f"{tool.name}{path}.{name}"


@pytest.mark.req("SEC-AUTHZ-002", "SEC-INJ-002")
def test_the_catalogue_in_the_spec_and_the_code_agree() -> None:
    tools = _tools()
    names = [tool.name for tool in tools]
    assert len(set(names)) == len(names)
    catalogue = _catalogue()
    for tool in tools:
        assert catalogue.get(tool.name) == tool.kind.value, tool.name
    assert set(catalogue) == set(names)  # every tool of the spec exists, and no other
    reads = {tool.name for tool in read_tools(cast(AgentServices, object()))}
    assert reads == {name for name, kind in catalogue.items() if kind == ToolKind.READ.value}
    assert not set(PROHIBITED) & set(names)
    assert not set(PROHIBITED) & set(catalogue)
