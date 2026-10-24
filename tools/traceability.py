"""Generate the requirement -> acceptance -> test traceability matrix (NFR-TRC-002).

Usage::

    uv run pytest                      # writes reports/test-requirements.json
    uv run python tools/traceability.py  # writes reports/traceability.md

A requirement is COVERED when at least one test marked with its ID passed, FAILING when
any such test failed, and UNTESTED otherwise.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict

from specindex import REPO_ROOT, load_acceptance_tests, load_requirements

RESULTS = REPO_ROOT / "reports" / "test-requirements.json"
OUTPUT = REPO_ROOT / "reports" / "traceability.md"


def main() -> int:
    requirements = load_requirements()
    acceptance = load_acceptance_tests()
    results: list[dict[str, object]] = (
        json.loads(RESULTS.read_text(encoding="utf-8")) if RESULTS.exists() else []
    )

    tests_by_req: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for result in results:
        reqs = result["requirements"]
        if not isinstance(reqs, list):
            raise TypeError(f"Malformed results file {RESULTS}: {result!r}")
        for req_id in reqs:
            tests_by_req[str(req_id)].append((str(result["test"]), str(result["outcome"])))

    ats_by_req: dict[str, set[str]] = defaultdict(set)
    for req in requirements.values():
        ats_by_req[req.id].update(req.acceptance)
    for test in acceptance.values():
        for req_id in test.requirements:
            ats_by_req[req_id].add(test.id)

    rows = [
        "| Requirement | Status | Acceptance tests | Tests (passed/total) | Coverage |",
        "|---|---|---|---|---|",
    ]
    counts: dict[str, int] = defaultdict(int)
    for req in requirements.values():
        tests = tests_by_req.get(req.id, [])
        passed = sum(1 for _, outcome in tests if outcome == "passed")
        if any(outcome == "failed" for _, outcome in tests):
            coverage = "FAILING"
        elif passed:
            coverage = "COVERED"
        else:
            coverage = "UNTESTED"
        counts[coverage] += 1
        ats = ", ".join(sorted(ats_by_req[req.id])) or "-"
        rows.append(f"| {req.id} | {req.status} | {ats} | {passed}/{len(tests)} | {coverage} |")

    summary = ", ".join(f"{key}: {value}" for key, value in sorted(counts.items()))
    OUTPUT.parent.mkdir(exist_ok=True)
    OUTPUT.write_text(
        "# Traceability matrix (generated)\n\n"
        f"Requirements: {len(requirements)}. {summary}.\n\n" + "\n".join(rows) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {OUTPUT.relative_to(REPO_ROOT)} ({summary})")
    return 1 if counts.get("FAILING") else 0


if __name__ == "__main__":
    sys.exit(main())
