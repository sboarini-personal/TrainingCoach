# Acceptance: recovery

### AT-REC-001 — One low reading is not a major decision
- Requirements: FR-REC-003
- Level: unit. First executable: P11.

**Given** one HRV reading well below baseline
**And** normal sleep, resting heart rate, check-in and recent performance
**When** recovery is assessed
**Then** no REDUCE, REST or RECOVERY_BLOCK decision is produced
**And** the reading is reported as an observation with its uncertainty.

### AT-REC-002 — Wearables do not override the athlete
- Requirements: FR-REC-002
- Level: unit. First executable: P11.

**Given** a provider recovery metric indicating good recovery
**And** a check-in with fatigue 5 and soreness 4
**When** the day's intensity is decided
**Then** the decision follows the more conservative signal
**And** the conflict is shown to the athlete as an observation.
