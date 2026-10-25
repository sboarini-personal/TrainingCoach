"""Hardened, streaming XML reading (SEC-UPL-002, AT-XXE-001).

``defusedxml`` with DTDs, entities and external references forbidden; element depth and
count are limited while streaming, and finished elements are cleared to bound memory.
"""

from __future__ import annotations

import xml.etree.ElementTree as ElementTree
from collections.abc import Iterator
from pathlib import Path
from typing import Final

from defusedxml import DTDForbidden, EntitiesForbidden, ExternalReferenceForbidden
from defusedxml.ElementTree import iterparse

from coach.integrations.files.parsing import ParserLimits, limit_exceeded, parse_failed

type Element = ElementTree.Element

_FORBIDDEN: Final = {
    DTDForbidden: "XML_DTD_FORBIDDEN",
    EntitiesForbidden: "XML_ENTITY_FORBIDDEN",
    ExternalReferenceForbidden: "XML_EXTERNAL_FORBIDDEN",
}


def local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def namespace(tag: str) -> str:
    return tag[1:].split("}", 1)[0] if tag.startswith("{") else ""


def events(path: Path, limits: ParserLimits) -> Iterator[tuple[str, Element, int]]:
    """``(event, element, depth)`` for start and end events, within the XML limits."""
    depth = 0
    count = 0
    try:
        for event, element in iterparse(
            str(path),
            events=("start", "end"),
            forbid_dtd=True,
            forbid_entities=True,
            forbid_external=True,
        ):
            if event == "start":
                depth += 1
                count += 1
                if depth > limits.xml_depth:
                    raise limit_exceeded("XML_DEPTH")
                if count > limits.xml_elements:
                    raise limit_exceeded("XML_ELEMENTS")
                yield event, element, depth
            else:
                yield event, element, depth
                depth -= 1
    except (DTDForbidden, EntitiesForbidden, ExternalReferenceForbidden) as error:
        raise parse_failed(_FORBIDDEN[type(error)]) from None
    except ElementTree.ParseError:
        raise parse_failed("XML_MALFORMED") from None


def child_text(element: Element, name: str) -> str | None:
    """Text of the first direct child with local name ``name``."""
    for child in element:
        if local(child.tag) == name:
            return child.text
    return None


def descendant_text(element: Element, name: str) -> str | None:
    for child in element.iter():
        if child is not element and local(child.tag) == name:
            return child.text
    return None
