# Privacy: consent, access, correction, deletion

Owner of PRIV-CON-001 to PRIV-CON-004, PRIV-ACC-001, PRIV-ACC-002, PRIV-DEL-001 to
PRIV-DEL-003. Master spec §56 to §58. Data inventory and context: `docs/privacy.md`.

## 1. Consent (PRIV-CON-001, PRIV-CON-002)

Contract: [../contracts/consent.schema.json](../contracts/consent.schema.json).

| consentType | Covers | Default |
|---|---|---|
| ACTIVITIES | activity data from files and providers | asked at onboarding |
| RECOVERY | resting heart rate and daily recovery summaries | asked |
| SLEEP | sleep data | asked |
| HRV | heart-rate variability | asked |
| PROVIDER_RECOVERY_METRICS | proprietary recovery metrics (Body Battery, stress) | asked |
| LONG_TERM_STORAGE | keeping data beyond the active coaching period | asked |
| AI_PROCESSING | sending personal data to the external LLM provider (PRIV-CON-003) | asked first, step 0 |
| WOMENS_HEALTH | women's health data (PRIV-CON-004) | not offered in V1 |

- Consent is an append-only ledger of `ConsentRecord`s. The current state of a type is its
  latest record.
- Each record states `policyVersion` and `purpose`. A new privacy policy version requires
  re-consent for affected types.
- Enforcement points (all deterministic): ingestion (a category without consent is not
  fetched or stored), read paths used by analytics, agent context assembly, and the policy
  engine (POL-CONSENT). AT-CON-001.
- Revocation takes effect immediately for new processing. The athlete is then offered
  deletion of existing data of that category. Revoking LONG_TERM_STORAGE starts deletion
  of data older than the active coaching period after confirmation (assumption A-20).

## 2. External AI processing (PRIV-CON-003, PROPOSED)

The LLM provider is a third-party processor. Without AI_PROCESSING consent no personal data
is sent to it; deterministic features keep working. With consent, context sent to the LLM is
minimised: derived summaries before raw data, no identifiers beyond what the task needs, no
credentials. The provider and its data-retention terms are documented in `docs/privacy.md`.

## 3. Minimisation (PRIV-CON-004, PROPOSED)

Only data needed for training planning, performance evaluation, sports recovery and product
security is collected. Not ingested by default: women's health data, nutrition logs, weight
goals, location traces beyond what the activity analysis needs (GPS streams are stored but
never sent to the LLM). The system does not infer unnecessary sensitive attributes.

## 4. Access and correction (PRIV-ACC-001, PRIV-ACC-002)

- **Inspect and export:** a command exports all of the athlete's data (profile, consents,
  activities, raw files, derived data, plans, insights, decisions, audit entries about them)
  as JSON plus original files.
- **Correct:** profile fields through allow-listed updates; activity data through
  DataOverride (FR-ACT-006), keeping the original.

## 5. Deletion (PRIV-DEL-001 to PRIV-DEL-003)

The deletion workflow covers, in order:

1. Provider connections: stop syncs; delete the Garmin key and handles from the secret store;
   instruct the athlete to delete the key in Garmin Connect (the Personal API offers no
   revocation endpoint) (PRIV-DEL-002).
2. Plans, workout executions, insights, agent decisions, recovery prescriptions.
3. Derived metric values.
4. Normalized data (activities, daily recovery, check-ins, strength sessions and sets,
   overrides, data-quality issues).
5. Raw sources and raw blobs (files and provider payloads), including quarantine.
6. Consent ledger, goals, events, availability, profile.
7. Local API tokens for that athlete.
8. Backups created by the application are listed so the athlete can delete them (the
   application cannot reach copies outside its control).

Rules:

- Deletion is a destructive operation: it requires explicit confirmation (POL-DESTRUCTIVE).
- It is verified: after completion, a check queries every store for the athlete ID and must
  find nothing (AT-DEL-001).
- Retained audit entries (PRIV-DEL-003) keep only event type, timestamp, outcome and a salted
  hash of the athlete ID, with no health data and no free text.
- RAW immutability yields to deletion (privacy outranks immutability, spec precedence).
