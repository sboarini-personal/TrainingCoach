# Acceptance: activities, data layers, time

### AT-ACT-001 — Garmin activity enriched by a later FIT upload
- Requirements: FR-ACT-005, FR-ACT-007, FR-ACT-008, NFR-REL-001, INT-GAR-007, INT-FIL-001
- Level: integration and property-based. First executable: P6 (two file sources), P7 (Garmin plus FIT).

**Given** an activity received from Garmin
**And** the matching FIT file uploaded later
**When** ingestion completes
**Then** exactly one logical Activity exists with two linked sources
**And** fields may be enriched from the FIT per the resolution rules, each with provenance
**And** distance, duration, training load and weekly volume are not duplicated
**And** uploading the same FIT again changes nothing
**And** the result is the same if the FIT arrives before the Garmin record.

### AT-RAW-001 — Corrections never change raw data
- Requirements: FR-ACT-002, FR-ACT-006, PRIV-ACC-002, INT-GAR-009
- Level: integration. First executable: P5.

**Given** imported raw activity data
**When** the athlete corrects the distance with a reason
**Then** the RAW blob and NORMALIZED value are byte-identical to before
**And** the effective distance is the corrected value
**And** a DataOverride records original value, corrected value, reason, time and author
**And** dependent derived metrics are recomputed as new values while old values remain for lineage.

### AT-TIM-001 — Time zones and training day
- Requirements: FR-ACT-003, FR-ACT-004
- Level: unit (property-based). First executable: P5.

**Given** an activity starting 23:30 local time (UTC-3) that ends after midnight
**Then** it is stored with a UTC start, the original local time, timezone and offset
**And** its training day is the local start date
**Given** an activity recorded while travelling in another timezone
**Then** its training day uses the activity's own timezone
**Given** a GPX file with UTC timestamps and no timezone
**Then** the training day uses the athlete's timezone with `trainingDayBasis = ATHLETE_TIMEZONE_ESTIMATED` and uncertainty ESTIMATED_VALUE.

### AT-UNP-001 — Unplanned tennis changes the next day
- Requirements: FR-ACT-011, FR-PLN-007, FR-ADP-001
- Level: unit. First executable: P12.

**Given** Wednesday is a planned rest day and Thursday has planned running intervals
**When** a 120-minute tennis activity with RPE 7 is ingested for Wednesday
**Then** it gets a WorkoutExecution with status UNPLANNED
**And** it contributes to Wednesday's load, interference and recovery
**And** Thursday's planned intervals are re-evaluated with the tennis load as evidence.
