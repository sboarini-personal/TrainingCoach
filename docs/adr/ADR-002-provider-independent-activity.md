# ADR-002: Canonical Activity model is provider-independent

- Status: Accepted
- Date: 2026-10-24
- Requirements: FR-ACT-001, FR-MET-007, INT-GAR-001

## Context

Garmin is the first source, but files (FIT, TCX, GPX, CSV) and possibly other providers must
feed the same analytics. Coupling the domain to Garmin field names would make every analysis
provider-specific.

## Decision

The domain uses one canonical Activity (`spec/contracts/activity.schema.json`) with canonical
units and enumerations. Provider-specific values stay in RAW; provider metrics enter the
canonical model only as labelled provider metrics `{value, provider, metricName, origin}`.

## Consequences

- Each source needs a mapper to the canonical model.
- Analytics and planning never branch on provider identity.

## Verification

- Contract tests validate canonical examples and reject provider-specific additions
  (`additionalProperties: false`).
- The domain package has no Garmin imports (layering contract).
