# Traceability

Chain maintained for every requirement:

```
Requirement (requirements-index.md)
  -> owner spec file (rules and details)
  -> acceptance test (acceptance/*.md, Given/When/Then)
  -> automated tests (@pytest.mark.req)
  -> implementation (module named in the owner spec / ADR enforcement section)
```

## How it is produced

1. `uv run pytest` runs all tests. The plugin in `tests/conftest.py`:
   - rejects any `@pytest.mark.req` ID not present in `requirements-index.md`;
   - writes `reports/test-requirements.json` (test, requirement IDs, outcome).
2. `uv run python tools/traceability.py` joins the index, the acceptance specs and the test
   results into `reports/traceability.md`, classifying every requirement as COVERED,
   FAILING or UNTESTED.
3. CI uploads `reports/` as a build artifact on every run.

## Spec integrity checks (always executed)

`tests/contract/test_spec_integrity.py` and `tests/contract/test_schemas.py` verify:

- requirement IDs are unique, well-formed and have a valid status;
- each owner spec file exists and mentions its requirement;
- every acceptance test referenced by a requirement is defined, and every acceptance test
  lists at least one known requirement;
- every requirement ID mentioned anywhere in `spec/` or `docs/adr/` exists in the index;
- ADR files exist and declare a status;
- every JSON Schema is valid Draft 2020-12, cross-schema references resolve, and the examples
  in `contracts/examples/` validate.

## Current state

Phase P1 (specification). Only the spec-integrity and contract tests exist; functional
requirements are UNTESTED until their implementation phases (see `docs/architecture.md`,
section "Delivery phases").
