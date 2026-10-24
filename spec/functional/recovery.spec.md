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

## 4. Recovery prescription (FR-REC-004)

`RecoveryPrescription`: `type` (REST, ACTIVE_RECOVERY, EASY_SESSION, REDUCED_VOLUME),
`reason`, `evidence[]`, `durationDays`, `reevaluateAfter` (date), `createdAt`.

It is a training-workload decision, not medical treatment. REST and ACTIVE_RECOVERY activate
the RECOVERY_BLOCK condition until `reevaluateAfter`.

## 5. Consent (FR-REC-005)

| Input | Consent type |
|---|---|
| Sleep | SLEEP |
| HRV | HRV |
| Resting HR, recovery summaries | RECOVERY |
| Body Battery, stress, other provider recovery metrics | PROVIDER_RECOVERY_METRICS |

Without the consent the input is neither ingested nor used. The assessment continues with
the remaining signals and lists `MISSING_DATA` (AT-CON-001).
