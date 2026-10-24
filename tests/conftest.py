"""Pytest plugin for requirement traceability (NFR-TRC-002).

Tests declare the requirements they verify with ``@pytest.mark.req("FR-ACT-007", ...)``.
Unknown requirement IDs abort the run, so a test can never claim coverage of a
requirement that is not in ``spec/requirements-index.md``. After the run the mapping
test -> requirements -> outcome is written to ``reports/test-requirements.json``.
"""

from __future__ import annotations

import json

import pytest

from specindex import REPO_ROOT, REQUIREMENTS_INDEX, load_requirements

_REQUIREMENTS_BY_TEST: dict[str, list[str]] = {}
_OUTCOMES: dict[str, str] = {}


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        ids = sorted({str(arg) for mark in item.iter_markers(name="req") for arg in mark.args})
        if ids:
            _REQUIREMENTS_BY_TEST[item.nodeid] = ids
    if not _REQUIREMENTS_BY_TEST:
        return
    if not REQUIREMENTS_INDEX.exists():
        raise pytest.UsageError("req markers used but spec/requirements-index.md is missing")
    known = load_requirements()
    unknown = sorted({req for ids in _REQUIREMENTS_BY_TEST.values() for req in ids} - known.keys())
    if unknown:
        _REQUIREMENTS_BY_TEST.clear()  # an aborted run must not overwrite the last report
        raise pytest.UsageError(f"Tests reference unknown requirement IDs: {unknown}")


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    if report.nodeid not in _REQUIREMENTS_BY_TEST:
        return
    relevant = report.when == "call" or report.outcome != "passed"
    if relevant and _OUTCOMES.get(report.nodeid) != "failed":
        _OUTCOMES[report.nodeid] = report.outcome


def pytest_sessionfinish(session: pytest.Session) -> None:
    if not _REQUIREMENTS_BY_TEST:
        return
    output = REPO_ROOT / "reports" / "test-requirements.json"
    output.parent.mkdir(exist_ok=True)
    records = [
        {"test": nodeid, "requirements": reqs, "outcome": _OUTCOMES.get(nodeid, "not-run")}
        for nodeid, reqs in sorted(_REQUIREMENTS_BY_TEST.items())
    ]
    output.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
