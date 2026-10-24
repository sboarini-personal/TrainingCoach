# Specification

This directory is the **source of truth** for the Personal Multisport Performance Coach.
It materialises the master SDD bootstrap specification v1.1 (spec version `1.1.0`, see
`coach.SPEC_VERSION`).

## Precedence

When requirements conflict:

1. Security and safety
2. Master specification ([product.md](product.md) and this tree)
3. Domain contracts ([contracts/](contracts/))
4. Acceptance tests ([acceptance/](acceptance/))
5. Architectural decisions ([../docs/adr/](../docs/adr/))
6. Implementation

If implementation and specification conflict: **stop**, identify the conflict, and resolve
it intentionally (change the spec through review, or fix the code). Never silently alter
one to match the other.

## Conventions

- Normative keywords **MUST**, **MUST NOT**, **SHOULD**, **SHOULD NOT**, **MAY** follow RFC 2119.
- Requirement IDs follow `<CLASS>-<AREA>-<NNN>` (for example `FR-ACT-007`). IDs are stable:
  never reused, never renumbered. A withdrawn requirement keeps its row with status `RETIRED`.
- Every requirement has exactly one **owner** spec file (column "Owner" in
  [requirements-index.md](requirements-index.md)); other files reference it by ID.
- Acceptance tests use `AT-<AREA>-<NNN>` and are written as Given/When/Then in
  [acceptance/](acceptance/). Each lists the requirements it verifies.
- Requirement status: `ACCEPTED` (derived from the master specification), `PROPOSED` (added
  during materialisation; needs athlete/owner review), `RETIRED`.
- Unconfirmed external behaviour is marked **ASSUMPTION** with an ID (`A-xx`, or `G-x` for
  Garmin) and isolated behind an interface. External endpoints are never invented.
- In JSON contracts, `null` means **UNKNOWN** (applicable but not known); an absent property
  means **not applicable**.
- Tests declare covered requirements with `@pytest.mark.req("ID", ...)`. Unknown IDs fail the
  run; `tools/traceability.py` generates the matrix (see [traceability.md](traceability.md)).

## Layout

| Path | Content |
|---|---|
| [product.md](product.md) | Vision, feedback loop, principles, V1 scope and exclusions |
| [requirements-index.md](requirements-index.md) | Every requirement ID, owner, acceptance tests, status |
| [traceability.md](traceability.md) | How requirement -> acceptance -> test -> code traceability is produced |
| [glossary.md](glossary.md) | Domain vocabulary |
| [nfr.spec.md](nfr.spec.md) | Reliability, observability, explainability, traceability |
| [functional/](functional/) | Athlete, goals, activities, metrics, sports, strength, recovery, insights, planning, policy, reporting, agent |
| [integration/](integration/) | Canonical model, deduplication, files, provider capabilities, Garmin |
| [security/](security/) | Authentication, authorization, secrets, privacy, uploads, injection, audit |
| [safety/](safety/) | Sport safety boundaries and SAFETY_HOLD |
| [contracts/](contracts/) | Agent tools and JSON Schemas (with validated examples) |
| [acceptance/](acceptance/) | Acceptance tests (Given/When/Then) |

## Change process

1. Change the owning spec file and the row in `requirements-index.md`.
2. Update or add acceptance tests.
3. Update contracts; bump the schema version (minor = additive, major = breaking, with a
   migration strategy per NFR-REL-004).
4. Write or update tests, then implement.
5. `uv run pytest` verifies spec integrity (IDs, owners, acceptance references, ADRs, schemas).
