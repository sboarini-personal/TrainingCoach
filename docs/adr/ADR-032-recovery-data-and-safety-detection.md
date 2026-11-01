# ADR-032: Recovery data: minimised provider payloads and fail-safe red-flag capture

- Status: Accepted (athlete's review of 2026-10-31)
- Date: 2026-10-30
- Requirements: FR-REC-001, FR-REC-005, PRIV-CON-002, PRIV-CON-004, INT-GAR-009, SAFE-MED-002,
  SAFE-MED-003
- Amends: INT-GAR-009 for daily recovery payloads (garmin spec sections 6 and 12)

## Context

P11 brings daily recovery data from Garmin, each metric only with its consent category
(recovery spec 5). The endpoints chosen return one metric each, but their payloads carry
more, and part of it belongs to other consent categories. The Swagger document
(re-checked on 2026-10-30) shows that:

- the sleep summary (`DailySleepDTO`) also holds sleep stress, average heart rate, an HRV
  adjustment, SpO2, breathing disruption and a free-text note;
- the daily heart rate (`DailyHeartRateDTO`) holds abnormal heart rate values;
- the Body Battery report holds sleep and stress events with their times.

INT-GAR-009 keeps every provider response byte-exact as RAW. Doing that for these payloads
would store data of categories the athlete did not grant. That breaks the consent rule ("a
category without consent is not fetched or stored", privacy spec 1) and minimisation
(PRIV-CON-004). Security and privacy take precedence over the master spec.

The independent review of P11.1 and P11.2 also found that a red flag could be lost:

- a check-in with a red flag and any invalid answer was refused whole, with no hold;
- warning signs written in the notes, or a pain located in the chest, raised nothing;
- a severe pain without a stated onset raised nothing.

## Decision

- **Daily recovery payloads are minimised at the boundary.** The provider's response is
  decoded in memory by the provider's daily normaliser and never persisted. What is stored as
  RAW is a canonical JSON document of the values read for that metric, with the request
  window: contract `recovery-payload`. Its RAW category is the endpoint's with the suffix
  `_MINIMISED`. The same values for the same request are the same content-addressed RAW. An
  unreadable response stores nothing. INT-GAR-009 keeps applying to activity payloads and
  files.
- **Observations record when a value was received.** A sync stores a new observation only
  when a day's value differs from the one in force. The latest received is in force, so a
  value that returns to an earlier one is in force again.
- **The integration status keeps, per daily metric, the last day requested.** Days without
  data are not asked again. A recovery failure is recorded in that status like an activity
  failure, the postponement after a rate limit included. A response that is unreadable, too
  large or another HTTP 4xx is counted and skipped. The activity and recovery syncs of one run
  share the provider adapter: one token and one request budget.
- **Red flags are captured fail-safe.** The hold is activated before anything else can fail.
  If the other answers are invalid, a check-in holding only the red flags is recorded. The
  check-in keeps the flags the system detected in the athlete's text (`detectedFlags`):
  - red-flag phrases 1.0.0 (Spanish and English, normalised, whole words, no negation
    handling);
  - a pain whose location names the chest;
  - a pain of 7 or more whose onset is not GRADUAL.

  The safety notice names the warning signs registered. Every red flag gets the emergency
  sentence.

## Consequences

- RAW of daily recovery payloads cannot be re-normalised from Garmin's bytes. A new
  normaliser needs a new sync of the provider's history, which reaches back as far as the
  provider allows.
- Values the coach does not read (sleep stages, scores, SpO2) are not kept. Using one later
  needs its own consent mapping and a normaliser change.
- Phrase detection gives false positives (a sentence that denies a sign still matches). The
  athlete releases the hold (safety spec 4). The lists are initial values under review.
