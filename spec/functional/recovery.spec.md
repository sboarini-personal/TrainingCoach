# Daily check-in and recovery

Owner of FR-REC-001 to FR-REC-005. Master spec §37 to §39. Implemented in Phase P11.

## 1. DailyCheckIn (FR-REC-001)

Fields: `athleteId`, `timestamp` (UTC), `trainingDay`, `sleepQuality`, `fatigue`, `soreness`,
`stress`, `motivation`, `painFlag`, `pain` (`{intensity, location}` when flagged), `notes`
(untrusted text).

Documented scale (integers 1 to 5; the direction is explicit per item):

| Item | 1 | 3 | 5 | Higher is |
|---|---|---|---|---|
| sleepQuality | very poor | normal | excellent | better |
| fatigue | none | moderate | extreme | worse |
| soreness | none | moderate, noticeable | severe, limits movement | worse |
| stress | none | moderate | extreme | worse |
| motivation | none | normal | very high | better |

Pain intensity uses a 0 to 10 numeric rating. Pain rules are in
[../safety/sport-safety.spec.md](../safety/sport-safety.spec.md).

Rules (P11, precision of this section; contract
[../contracts/daily-checkin.schema.json](../contracts/daily-checkin.schema.json)):

- Every answer is optional (UNKNOWN when not given), but a check-in states at least one.
  `painFlag` is null when the pain question was not answered; `pain` (intensity, the
  athlete's location in their own words, onset SUDDEN or GRADUAL) needs the flag.
- `redFlags` are the explicit answers to the check-in's red-flag question (the codes of the
  safety spec), listed once. `detectedFlags` are the ones the system found in what the
  athlete wrote or rated (safety spec 2): red-flag phrases in the notes, a pain location
  naming the chest whatever the intensity answered (RF_CHEST_PAIN), pain of intensity 7 or
  more whose onset is not GRADUAL
  (RF_SEVERE_ACUTE_PAIN; an unknown onset counts as sudden, fail-safe). Any red flag
  activates SAFETY_HOLD at once and the athlete is shown a fixed safety notice naming the
  warning signs (safety spec 3).
- Fail-safe: the hold is activated before the check-in is validated or stored, and before
  its training day is worked out. If the other answers are invalid, a check-in with the red
  flags only is recorded and the athlete is told the rest was not (`coach checkin add`
  exits 1). If the day cannot be known (no timezone, an invalid `--day`), nothing is
  recorded but the hold stands and the notice is shown.
- Pain intensity 0 is no pain (`painFlag` false). A location or onset needs a pain above 0.
  The location is at most 100 characters, the notes 1000.
- A check-in is immutable: recording another for the same training day supersedes it (the
  latest is in force; the earlier stays for lineage). The training day defaults to today in
  the athlete's timezone.
- A check-in is the athlete's own declaration, like the profile: it needs no category
  consent (the consent types cover provider data, section 5). Its notes and pain location
  are untrusted text, screened for disclosed secrets before they are stored (SEC-SEC-005),
  and never interpreted: a location names a region only by the exact rule of strength spec
  section 2.
- Commands: `coach checkin add` (one option per item, `--pain`, `--pain-location`,
  `--pain-onset`, `--red-flag`, `--notes`) and `coach checkin list`.

## 2. Subjective vs wearable (FR-REC-002)

- Subjective feedback and wearable metrics coexist; both are evidence.
- Wearable metrics never automatically override athlete-reported condition. When they
  conflict, the conflict is surfaced as an observation, and intensity decisions follow the
  more conservative of the two (AT-REC-002).
- Wearable recovery metrics are training signals, not medical diagnoses.

## 3. Recovery assessment (FR-REC-003)

Inputs (each only with its consent, FR-REC-005): sleep, HRV, resting heart rate, provider
recovery metrics (for example Body Battery), stress, subjective fatigue, soreness,
motivation, pain, recent performance.

- Each signal is compared against the athlete's own rolling baseline (proposed: 7-day vs
  28-day windows; versioned, assumption A-18).
- A **major decision** (REDUCE, REST, RECOVERY_BLOCK) requires at least two independent
  deviating signals, or one signal deviating on three consecutive days, unless a pain or
  red-flag rule applies.
- One isolated low reading with otherwise normal signals never triggers a major decision
  (AT-REC-001).
- The output is a `RecoveryAssessment {status, signals[], evidence[], confidence,
  uncertaintyReasons[]}`.

Assessment rules 1.0.0 (P11, precision of this section; parameters are initial values under
A-18, pending review; contract
[../contracts/recovery-assessment.schema.json](../contracts/recovery-assessment.schema.json);
`coach recovery status`):

- **Baseline (precision of "7-day vs 28-day").** A provider metric compares the day's reading
  with the 28 days before it, usable from 7 readings (NO_BASELINE otherwise:
  INSUFFICIENT_HISTORY). It deviates when worse than the baseline mean by more than one
  standard deviation, and is FAVOURABLE when better by as much. The standard deviation has a
  floor per metric (resting HR 2 bpm, HRV 3 ms, sleep 30 min, Body Battery 5), so a very
  steady baseline does not turn noise into a deviation. A 7-day rolling mean is not used for
  the decision: one low reading would keep it low for up to seven days and would count as
  persistent, against AT-REC-001.
- **Check-in items** deviate at the documented anchors: fatigue, soreness or stress 4 or more;
  sleep quality or motivation 2 or less. **Pain:** any pain reported is a deviating signal.
- **Independent signals** are groups. One group is autonomic: resting heart rate, overnight
  HRV and Body Battery, which is computed from them. One is sleep: measured sleep and sleep
  quality. Fatigue, soreness, stress, motivation and pain are one group each.
- **Status**, first match:
  1. SAFETY_HOLD active: REST, and the hold's own rules apply.
  2. Three or more deviating groups: REST.
  3. Pain of 7 or more (a red flag unless GRADUAL): REDUCE.
  4. Two deviating groups: REDUCE.
  5. One signal deviating on three consecutive days with readings: REDUCE.
  6. One deviating group: MONITOR, an observation (AT-REC-001).
  7. Otherwise NORMAL, or INSUFFICIENT_DATA when no signal could be assessed.
- **Observations**, reported with any status:
  - a wearable and the athlete disagree (one deviates, the other does not; AT-REC-002);
  - pain reported;
  - pain reported for more than 7 days, or growing since its episode began: professional
    evaluation is suggested (safety spec 5). A check-in saying there is no pain ends the
    episode.
- **Missing data:** a metric without consent is NO_CONSENT and a missing answer is MISSING.
  Either lists MISSING_DATA (AT-CON-001).
- **Confidence:** HIGH with three or more assessed groups from both wearables and the
  athlete; MEDIUM with two or more groups; LOW otherwise.
- Recent performance is not a signal in rules 1.0.0: no deterministic per-session
  performance deviation exists yet. Provider stress is not fetched (section 5).
- **Storage:** an assessment equal to the day's one in force is not stored again. Another
  result is a new immutable document, and the latest is in force.

## 4. Recovery prescription (FR-REC-004)

`RecoveryPrescription`: `type` (REST, ACTIVE_RECOVERY, EASY_SESSION, REDUCED_VOLUME),
`reason`, `evidence[]`, `durationDays`, `reevaluateAfter` (date), `createdAt`.

It is a training-workload decision, not medical treatment. REST and ACTIVE_RECOVERY activate
the RECOVERY_BLOCK condition until `reevaluateAfter`.

Prescription rules 1.0.0 (P11, precision; contract
[../contracts/recovery-prescription.schema.json](../contracts/recovery-prescription.schema.json)):

| Decisive reason | Type | Days |
|---|---|---|
| three deviating groups | REST | 1 |
| pain of 7 or more | ACTIVE_RECOVERY | 1 |
| two deviating groups | EASY_SESSION | 2 |
| one signal on three consecutive days | REDUCED_VOLUME | 2 |

- `reevaluateAfter` is the day plus the days.
- Evidence is the assessment, then the sources of its deviating signals.
- A safety hold has no prescription: its own rules apply.
- A prescription is in force on the days it covers while its assessment is still the one in
  force for its day; a re-assessment of that day withdraws it.
- RECOVERY_BLOCK follows the prescriptions in force. It is activated with a REST or
  ACTIVE_RECOVERY in force (reason: the type). It is cleared by the re-evaluation, an
  assessment once none is in force (reason `REEVALUATED`).
- Planning reads the prescriptions in force (POL-RECOVERY-BLOCK, P12).
- Events: `recovery.assessed` (status and confidence) and `recovery.prescription.created`
  (type).

## 5. Consent (FR-REC-005)

| Input | Consent type |
|---|---|
| Sleep | SLEEP |
| HRV | HRV |
| Resting HR, recovery summaries | RECOVERY |
| Body Battery, stress, other provider recovery metrics | PROVIDER_RECOVERY_METRICS |

Without the consent the input is neither ingested nor used. The assessment continues with
the remaining signals and lists `MISSING_DATA` (AT-CON-001).

Provider recovery data (P11; contract
[../contracts/recovery-observation.schema.json](../contracts/recovery-observation.schema.json)):

- Metrics: RESTING_HR (bpm, RECOVERY), HRV_NIGHTLY (average overnight HRV in ms, HRV),
  SLEEP_DURATION (measured sleep in seconds, naps excluded, SLEEP) and BODY_BATTERY_CHARGED
  (a proprietary 0 to 100 recovery signal, PROVIDER_DERIVED, PROVIDER_RECOVERY_METRICS).
- `coach garmin sync` synchronises them after the activities, each metric only with its
  consent (a metric without it is neither requested nor stored), through the provider's
  documented endpoint (garmin spec section 12; capability `healthRead`), even without the
  ACTIVITIES consent. The first sync reaches back 28 days (the baseline window); later ones
  start at the last day requested (saved after every request window, so an interrupted
  backfill resumes), less two days whose values may still settle. A provider
  payload also carries other consent categories, so it is never stored: RAW keeps a
  minimised document of the values read (ADR-032). A value becomes an immutable observation
  when it differs from the one in force for its day and metric, so a repeated sync stores
  nothing new; the latest received is in force.
- Reads (`coach recovery data`, the assessment) use the metrics whose consent is granted
  now: a revocation hides stored values at once. Deleting them is offered by the deletion
  workflow (privacy spec 1; P15); until then they stay hidden.
