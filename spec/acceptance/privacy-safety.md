# Acceptance: privacy and safety


### AT-CON-001 — Missing consent blocks processing
- Requirements: PRIV-CON-001, PRIV-CON-002, PRIV-CON-003, FR-REC-005
- Level: unit and integration. First executable: P4 (ledger and policy), P7 (sync), P13 (agent).

**Given** the athlete has not granted HRV consent and has revoked AI_PROCESSING
**When** a sync, a recovery assessment and a chat message occur
**Then** no HRV data is fetched or stored and the assessment lists MISSING_DATA for HRV
**And** no personal data is sent to the LLM provider
**And** deterministic features keep working.

### AT-STO-001 — Synced folders are refused as data directory
- Requirements: PRIV-STO-001
- Level: unit. First executable: P3.

**Given** `COACH_DATA_DIR` points inside the folder named by the `OneDrive` variable, or to any path with a `OneDrive...` or `Dropbox` component
**When** the data directory is resolved
**Then** it is refused with an explanation
**And** without `COACH_DATA_DIR` the default is the per-user local application data folder
**And** only `COACH_ALLOW_SYNCED_DATA_DIR=1` overrides the refusal.

### AT-DEL-001 — Deletion covers every store
- Requirements: PRIV-DEL-001, PRIV-DEL-002, PRIV-DEL-003, SEC-AUD-001
- Level: integration. First executable: P15.

**Given** an athlete with profile, consents, activities, raw files, derived data, plans, insights, decisions and a stored Garmin key
**When** deletion is confirmed
**Then** a post-deletion check finds no record or blob for the athlete in any store
**And** the Garmin key is removed from the secret store and the athlete is told how to delete it in Garmin Connect
**And** remaining audit entries contain only event type, time, outcome and a salted hash.

### AT-SAFE-001 — Red flags activate SAFETY_HOLD
- Requirements: SAFE-MED-001, SAFE-MED-002, SAFE-MED-003, SAFE-MED-004, SAFE-MED-005
- Level: unit (policy, detector) and agent evaluation. First executable: P12 (policy), P13 (agent).

**Given** the athlete reports chest pain during yesterday's run
**When** training advice is requested
**Then** SAFETY_HOLD is activated before the LLM call
**And** the policy engine denies any intensive recommendation or plan activation
**And** the response states the safety reason, recommends healthcare evaluation and gives no diagnosis or reassurance
**And** only an explicit, audited athlete action releases the hold.
