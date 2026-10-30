"""Integrity of the specification tree: IDs, owners, acceptance tests and ADRs."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from specindex import (
    ADR_DIR,
    ADR_ID,
    AT_ID,
    REPO_ROOT,
    REQ_ID,
    SPEC_DIR,
    load_acceptance_tests,
    load_requirements,
)

pytestmark = pytest.mark.req("NFR-TRC-002")

REQUIREMENTS = load_requirements()
ACCEPTANCE = load_acceptance_tests()
EXPECTED_ADRS = [f"ADR-{number:03d}" for number in range(1, 29)]

# Living documents whose references must resolve. The discovery report is a historical
# snapshot and is deliberately excluded.
REFERENCING_DOCS: list[Path] = [
    *sorted(SPEC_DIR.rglob("*.md")),
    *sorted(SPEC_DIR.rglob("*.json")),
    *sorted(ADR_DIR.glob("*.md")),
    REPO_ROOT / "docs" / "architecture.md",
    REPO_ROOT / "docs" / "threat-model.md",
    REPO_ROOT / "docs" / "privacy.md",
    REPO_ROOT / "CLAUDE.md",
    REPO_ROOT / "README.md",
]


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_index_covers_every_master_spec_category() -> None:
    prefixes = {req_id.rsplit("-", 1)[0] for req_id in REQUIREMENTS}
    required = {
        "FR-ONB", "FR-PRF", "FR-GOL", "FR-ACT", "FR-RUN", "FR-CYC", "FR-SWM", "FR-STR",
        "FR-TEN", "FR-REC", "FR-MET", "FR-PLN", "FR-ADP", "INT-GAR", "INT-FIL", "SEC-AUTH",
        "SEC-AUTHZ", "SEC-SEC", "SEC-IDOR", "SEC-INJ", "SEC-UPL", "PRIV-CON", "PRIV-DEL",
        "SAFE-MED", "NFR-REL", "NFR-OBS", "NFR-EXP", "NFR-TRC",
    }  # fmt: skip
    assert required <= prefixes, f"missing categories: {sorted(required - prefixes)}"


@pytest.mark.parametrize("req_id", sorted(REQUIREMENTS))
def test_owner_spec_exists_and_defines_requirement(req_id: str) -> None:
    owner = SPEC_DIR / REQUIREMENTS[req_id].owner
    assert owner.is_file(), f"{req_id}: owner file {owner} does not exist"
    assert req_id in _text(owner), f"{req_id} is not mentioned in its owner {owner.name}"


@pytest.mark.parametrize("req_id", sorted(REQUIREMENTS))
def test_acceptance_tests_referenced_by_requirement_exist(req_id: str) -> None:
    missing = [at for at in REQUIREMENTS[req_id].acceptance if at not in ACCEPTANCE]
    assert not missing, f"{req_id} references undefined acceptance tests {missing}"


@pytest.mark.parametrize("at_id", sorted(ACCEPTANCE))
def test_acceptance_test_lists_known_requirements(at_id: str) -> None:
    requirements = ACCEPTANCE[at_id].requirements
    assert requirements, f"{at_id} lists no requirements"
    unknown = [req for req in requirements if req not in REQUIREMENTS]
    assert not unknown, f"{at_id} lists unknown requirements {unknown}"


def test_master_spec_mandatory_acceptance_tests_are_defined() -> None:
    mandatory = {
        "AT-ONB-001", "AT-ACT-001", "AT-STR-001", "AT-TEN-001", "AT-CYC-001", "AT-ADP-001",
        "AT-SEC-001", "AT-SAFE-001", "AT-EXP-001", "AT-INJ-001", "AT-IDOR-001", "AT-GAR-001",
        "AT-PLN-001", "AT-RAW-001", "AT-MET-001",
    }  # fmt: skip
    assert mandatory <= ACCEPTANCE.keys(), sorted(mandatory - ACCEPTANCE.keys())


def _doc_id(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


@pytest.mark.parametrize("path", REFERENCING_DOCS, ids=_doc_id)
def test_referenced_ids_exist(path: Path) -> None:
    text = _text(path)
    assert not sorted(set(REQ_ID.findall(text)) - REQUIREMENTS.keys()), "unknown requirements"
    assert not sorted(set(AT_ID.findall(text)) - ACCEPTANCE.keys()), "unknown acceptance tests"
    assert not sorted(set(ADR_ID.findall(text)) - set(EXPECTED_ADRS)), "unknown ADRs"


@pytest.mark.parametrize("adr_id", EXPECTED_ADRS)
def test_adr_exists_with_status(adr_id: str) -> None:
    matches = sorted(ADR_DIR.glob(f"{adr_id}-*.md"))
    assert len(matches) == 1, f"expected exactly one file for {adr_id}, found {matches}"
    text = _text(matches[0])
    assert text.startswith(f"# {adr_id}:"), f"{matches[0].name} must start with '# {adr_id}:'"
    assert re.search(r"^- Status: (Accepted|Proposed|Superseded)", text, re.MULTILINE)
