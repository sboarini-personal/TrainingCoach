# Policy engine

Owner of FR-POL-001 and FR-POL-002. Master spec §49 and §50. Core in Phase P4 (ownership,
consent), complete in Phase P12.

## 1. Interface (FR-POL-001)

```
evaluate(action: Action, context: PolicyContext) -> PolicyDecision

PolicyDecision = ALLOW
               | DENY(reasons: list[PolicyReason])
               | REQUIRE_CONFIRMATION(reason: PolicyReason, proposalId)
```

- Pure and deterministic: no I/O, no clock reads (time comes from the context), no LLM.
- The application layer assembles the context (profile, availability, consents, conditions,
  capabilities, ownership) and calls the engine before executing or persisting any action.
- Combination is **deny-overrides**: any DENY wins over REQUIRE_CONFIRMATION, which wins over
  ALLOW.
- Every decision records `policySetVersion` and the reasons (codes plus parameters), and
  security-relevant denials emit `security.authz.denied` (NFR-OBS-001).

## 2. Policy catalogue

| Policy | Denies or requires confirmation when |
|---|---|
| POL-OWNERSHIP | the resource does not belong to the acting athlete (SEC-AUTHZ-001) |
| POL-CONSENT | the action processes a data category without valid consent (PRIV-CON-002) |
| POL-SAFETY-HOLD | SAFETY_HOLD is active and the action recommends or activates intensive training (SAFE-MED-003) |
| POL-RECOVERY-BLOCK | RECOVERY_BLOCK is active and the proposal schedules intensity before `reevaluateAfter` |
| POL-AVAILABILITY | a workout falls on an unavailable day or outside time windows (FR-PRF-005) |
| POL-MAX-DURATION | a day's planned duration exceeds the effective maximum duration |
| POL-EQUIPMENT | a workout needs equipment or a resource the athlete lacks |
| POL-HEALTH-CONSTRAINT | a workout violates an active health constraint (FR-PRF-006) |
| POL-CAPABILITY | an action needs a provider capability that is not SUPPORTED (INT-GAR-003) |
| POL-EXTERNAL-WRITE | an external write lacks a valid human confirmation (ADR-005): REQUIRE_CONFIRMATION |
| POL-DESTRUCTIVE | a destructive operation (data deletion) lacks confirmation: REQUIRE_CONFIRMATION |

## 3. Core implemented in P4

`policySetVersion` 1.0.0. An `Action` states its kind (READ, WRITE_INTERNAL, PROPOSE,
EXECUTE_EXTERNAL, DELETE), the owner of the resource it touches, the consent types it needs,
whether it recommends or activates intensive training, and whether a verified human
confirmation is attached. The `PolicyContext` holds the acting athlete, the consent types
currently granted under the active privacy policy version, and the active coaching
conditions. Implemented policies and reason codes:

| Policy | Outcome | Reason code |
|---|---|---|
| POL-OWNERSHIP | DENY when the resource owner differs from the acting athlete | `OWNERSHIP_MISMATCH` |
| POL-CONSENT | DENY for each required consent type that is not granted | `CONSENT_REQUIRED` (parameter `consentType`) |
| POL-SAFETY-HOLD | DENY intensive training while SAFETY_HOLD is active | `SAFETY_HOLD_ACTIVE` |
| POL-EXTERNAL-WRITE | REQUIRE_CONFIRMATION for unconfirmed EXECUTE_EXTERNAL actions | `CONFIRMATION_REQUIRED` |
| POL-DESTRUCTIVE | REQUIRE_CONFIRMATION for unconfirmed DELETE actions | `CONFIRMATION_REQUIRED` |

The remaining policies need the planning model and arrive in P12. Confirmation tokens arrive
with the first external write or deletion workflow; until then nothing sets `confirmed`.

## 4. AI cannot bypass (FR-POL-002)

- AI proposals (plans, workouts, adaptations, tool calls) are data submitted to the engine.
  The AI has no tool that skips evaluation, and no tool that issues confirmations.
- A denied proposal is returned to the agent with reason codes so it can revise and explain;
  it is never persisted as PROPOSED.
- Confirmation tokens are issued only to a human UI or CLI action and are bound to one
  proposal, one athlete and an expiry (default 15 minutes). The agent never sees them.

AT-POL-001 verifies that a proposal violating availability, a health constraint or
SAFETY_HOLD is denied even when the agent insists.
