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

## Reading the matrix

COVERED means at least one passing test is marked with the requirement. It does not mean the
requirement is complete: many requirements are implemented across several phases (for
example lineage resolves activities only from P5). The acceptance files record which parts of
each acceptance test are executed so far.

## Current state

After P8: 100 of 152 requirements have passing tests (99 after P7); the rest are UNTESTED
until their implementation phases (see `docs/architecture.md`, section "Delivery phases").
