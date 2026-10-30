# ADR-031: Strength execution, volume, progression and interference

- Status: Accepted
- Date: 2026-10-30
- Requirements: FR-STR-002 to FR-STR-008, FR-ACT-005, FR-MET-002 to FR-MET-005
- Amends: ADR-003 (strength entities), ADR-027 (subject dimensions)

## Context

P10 makes strength usable as data. The spec fixed the vocabularies, the entities
(`Exercise`, `StrengthSetResult`), the baseline progression and interference rules, and that
watch and athlete sets are reconciled per field, but not how the two sources meet, how a
watch's exercise names become catalogue identities, what a hard set is, or how pain (free
text) can concern a region. Watches name exercises unreliably, rarely record effort, and
the athlete corrects them after the fact. Plans with targets arrive only in P12.

## Decision

- **Catalogue 1.0.0** (54 exercises, English and Spanish aliases) is versioned reference data
  in code. Identity is the `exerciseId`; text resolves by ID, catalogue name or the athlete's
  own alias, normalised, and never by similarity. Own aliases are refused when they equal a
  catalogue name, look like an ID or like a secret.
- **The watch's sets are a stream; the athlete's are an aggregate.** FIT `set` messages
  become the SETS stream (resolved like laps, `STREAMS_PREFER_FIT`); their exercises map to
  the catalogue through a FIT mapping table of exact equivalents, every name checked against
  the SDK profile by a test. The athlete's declarations are a `StrengthSession`, one per
  activity (its ID derived from the activity's): set entries, relabels and whether the watch
  is used. `StrengthSetResult`s are a pure function of both: sets match on (exercise, set
  number), a stated athlete value wins like a correction, RPE and RIR come only from the
  athlete, and nothing is counted twice. A relabel that would move existing entries is
  refused.
- **Volume** (`str.volume_load`, McBride et al. 2009) counts sets of known repetitions and
  load and names those left out; the week's total is qualifying-only. Hard sets follow the
  RIR-based RPE scale (Zourdos et al. 2016): RIR 4 or less, else RPE 6 or more; unknown
  effort stays unknown, so a weekly count per muscle group (new MUSCLE dimension,
  derived-metric-value 1.3.0) shows its known part and the sets of unknown effort.
- **Progression and interference are read models**, deterministic and versioned (rules
  1.0.0), with reason codes and evidence; nothing is stored until adaptation decisions exist
  (P12). Interference lives in `coach.planning` so the planner can use it on planned and
  completed sessions alike. A demand that rests on unknown data is *possible* and keeps its
  level with MISSING_DATA: missing data never dismisses interference. Context modifiers move
  a level only with stated evidence.
- **Regions**: an exercise loads its muscles' regions plus joint regions by pattern and tag
  (a versioned table). A pain location concerns a region only when, normalised, it equals one
  of the region's English or Spanish names; other text is never interpreted, and such pain
  can block a progression but never regress an unrelated exercise.
- **Prescription** (workout contract 1.1.0, additive): exercises are built from the
  catalogue; RPE and RIR targets need the athlete's familiarity with the scale, otherwise a
  conservative EASY or MODERATE effort cue; expected interference names body regions.

## Consequences

- Normalisation rules become 1.2.0 (parser output 1.2.0): `coach activity reprocess` adds the
  SETS stream to strength activities imported before P10.
- Strength sessions are keyed to activities: after a deduplication split the entries stay
  with the original activity and the athlete reviews them.
- Without targets the progression engine says INSUFFICIENT_DATA; it becomes useful with the
  athlete's targets (`coach strength log --target-reps --target-rpe`) and fully with plans.
- Every parameter (hard-set thresholds, 3 hard sets for heavy, 90-minute long run, session
  RPE 7 for hard, region tables, mapping table) is an initial value under A-18.
- The strength-sets CSV template is deferred to P12 (files spec 8.4).

## Verification

- Catalogue and aliases: `tests/unit/test_exercise_catalogue.py`.
- Sets stream and mapping: `tests/unit/test_file_sets.py`.
- Sessions and reconciliation: `tests/unit/test_strength_sessions.py`.
- Volume: `tests/unit/test_strength_volume.py`; progression:
  `tests/unit/test_strength_progression.py`; prescription:
  `tests/unit/test_strength_prescription.py`; interference (AT-STR-001):
  `tests/unit/test_interference.py`.
- End to end: `tests/integration/test_cli_strength.py`.
