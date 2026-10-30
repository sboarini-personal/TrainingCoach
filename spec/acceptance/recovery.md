# Acceptance: recovery

P11: AT-REC-001 and AT-REC-002 are executed by `tests/unit/test_recovery_assessment.py`
(assessment rules 1.0.0); recent performance is not a recovery signal in rules 1.0.0, so the
"normal recent performance" of AT-REC-001 holds by construction. AT-CON-001 is executed for
the recovery metrics by the same file and by `tests/unit/test_recovery_sync.py`.

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
