"""Parsers for the specification index files.

Single source of truth for tooling and tests that need requirement IDs, acceptance
tests or ADRs: ``tests/conftest.py``, ``tests/contract/test_spec_integrity.py`` and
``tools/traceability.py`` all use these functions (NFR-TRC-002).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SPEC_DIR = REPO_ROOT / "spec"
ADR_DIR = REPO_ROOT / "docs" / "adr"
REQUIREMENTS_INDEX = SPEC_DIR / "requirements-index.md"
ACCEPTANCE_DIR = SPEC_DIR / "acceptance"

_REQ = r"(?:FR|INT|SEC|PRIV|SAFE|NFR)-[A-Z]{2,5}-\d{3}"
REQ_ID = re.compile(rf"\b{_REQ}\b")
AT_ID = re.compile(r"\bAT-[A-Z]{2,5}-\d{3}\b")
ADR_ID = re.compile(r"\bADR-\d{3}\b")

_INDEX_ROW = re.compile(rf"^\|\s*(?P<id>{_REQ})\s*\|(?P<rest>.*)\|\s*$")
_AT_HEADING = re.compile(r"^###\s+(?P<id>AT-[A-Z]{2,5}-\d{3})\b(?P<title>.*)$")
_STATUSES = frozenset({"ACCEPTED", "PROPOSED", "RETIRED"})


@dataclass(frozen=True)
class Requirement:
    id: str
    title: str
    owner: str
    acceptance: tuple[str, ...]
    status: str


@dataclass(frozen=True)
class AcceptanceTest:
    id: str
    title: str
    file: str
    requirements: tuple[str, ...]


def load_requirements(path: Path = REQUIREMENTS_INDEX) -> dict[str, Requirement]:
    """Parse ``| ID | Title | Owner spec | Acceptance | Status |`` rows."""
    requirements: dict[str, Requirement] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = _INDEX_ROW.match(line)
        if match is None:
            continue
        cells = [cell.strip() for cell in match.group("rest").split("|")]
        if len(cells) != 4:
            raise ValueError(f"Malformed requirement row (expected 5 columns): {line}")
        title, owner, acceptance, status = cells
        req_id = match.group("id")
        if req_id in requirements:
            raise ValueError(f"Duplicate requirement ID: {req_id}")
        if status not in _STATUSES:
            raise ValueError(f"{req_id}: unknown status {status!r}")
        requirements[req_id] = Requirement(
            id=req_id,
            title=title,
            owner=owner.strip("`"),
            acceptance=tuple(AT_ID.findall(acceptance)),
            status=status,
        )
    return requirements


def load_acceptance_tests(directory: Path = ACCEPTANCE_DIR) -> dict[str, AcceptanceTest]:
    """Parse ``### AT-XXX-NNN title`` headings followed by a ``Requirements:`` line."""
    tests: dict[str, AcceptanceTest] = {}
    for path in sorted(directory.glob("*.md")):
        lines = path.read_text(encoding="utf-8").splitlines()
        for index, line in enumerate(lines):
            match = _AT_HEADING.match(line)
            if match is None:
                continue
            at_id = match.group("id")
            if at_id in tests:
                raise ValueError(f"Duplicate acceptance test ID: {at_id}")
            requirements_line = next(
                (
                    candidate
                    for candidate in lines[index + 1 : index + 4]
                    if "Requirements:" in candidate
                ),
                "",
            )
            tests[at_id] = AcceptanceTest(
                id=at_id,
                title=match.group("title").strip(" -—"),
                file=path.relative_to(SPEC_DIR).as_posix(),
                requirements=tuple(REQ_ID.findall(requirements_line)),
            )
    return tests
