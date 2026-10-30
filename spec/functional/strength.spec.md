# Strength training and interference

Owner of FR-STR-001 to FR-STR-008. Master spec §31 to §35. Implemented in Phase P10.

## 1. First-class discipline (FR-STR-001)

Every generated plan includes strength sessions unless strength is **contraindicated**
(active health constraint or SAFETY_HOLD) or **impossible** (no equipment and the athlete
rejects bodyweight work, or no availability). The omission and its reason are stated in the
plan (AT-STR-002). Strength is planned with the same care as endurance work, not as an
accessory (ADR-003).

## 2. Controlled vocabularies (FR-STR-002)

Movement patterns:

| Group | Patterns |
|---|---|
| Lower body | SQUAT, HIP_HINGE, LUNGE, CALF_RAISE |
| Upper body | HORIZONTAL_PUSH, VERTICAL_PUSH, HORIZONTAL_PULL, VERTICAL_PULL |
| Core | CORE_STABILIZATION, ANTI_ROTATION |
| Additional | LOADED_CARRY, SCAPULAR_STABILITY |

Muscle groups: QUADRICEPS, HAMSTRINGS, GLUTES, CALVES, SOLEUS, ADDUCTORS, ABDUCTORS,
FOOT_ANKLE, CORE, ERECTOR_SPINAE, LATS, RHOMBOIDS, TRAPEZIUS, PECTORALS, DELTOIDS,
ROTATOR_CUFF, BICEPS, TRICEPS, FOREARMS.

The enumerations are defined in [../contracts/workout.schema.json](../contracts/workout.schema.json).

Body regions (P10): what interference and regional load name. They are the muscle groups
plus four joint and tendon regions that muscle groups do not cover: ACHILLES_REGION, KNEES,
SHOULDERS and ELBOWS. The tennis regions of the tennis spec are body regions (calves,
soleus, Achilles region, knees, adductors, shoulders, elbows).

Regions an exercise loads (region rules 1.0.0, P10): the regions of its primary and secondary
muscle groups, plus KNEES for SQUAT and LUNGE, ACHILLES_REGION for CALF_RAISE, SHOULDERS and
ELBOWS for the push and pull patterns, SHOULDERS for SCAPULAR_STABILITY, KNEES and
ACHILLES_REGION for the HIGH_IMPACT tag and SHOULDERS for the OVERHEAD tag. Each region has
English and Spanish names (for example KNEES: knee, knees, rodilla, rodillas) that a pain
location must equal, normalised, to name it (`coach.domain.strength.regions`).

Contraindication tags (P10): AXIAL_LOAD (load through the spine), OVERHEAD, HIGH_IMPACT
(jumps, landings), DEEP_KNEE_FLEXION, SPINAL_FLEXION, GRIP_INTENSIVE. They let a health
constraint (P11, P12) exclude exercises by tag, never by name.

Equipment is the vocabulary of the athlete's resources (BARBELL, DUMBBELLS, KETTLEBELLS,
BANDS, MACHINES, PULL_UP_BAR, BENCH, BODYWEIGHT_ONLY, OTHER); an exercise lists the equipment
it needs, and an exercise needing none lists BODYWEIGHT_ONLY.

## 3. Exercise catalogue (FR-STR-003)

`Exercise`: `exerciseId` (pattern `ex.<snake_case>`, stable), `canonicalName`,
`movementPattern`, `primaryMuscles[]`, `secondaryMuscles[]`, `equipment[]`, `unilateral`,
`technicalDifficulty` (1 to 5), `contraindicationTags[]` (for example AXIAL_LOAD, OVERHEAD,
HIGH_IMPACT, DEEP_KNEE_FLEXION), `substitutions[]` (exercise IDs), `aliases[]`
(`{language, text}`).

- Identity is the `exerciseId`, never free text. Aliases (any language, user-defined) map to
  an ID and never change it.
- The catalogue is versioned reference data shipped with the application (seed list in P10).

Catalogue 1.0.0 (P10, contract [../contracts/exercise.schema.json](../contracts/exercise.schema.json)):

- About 50 exercises covering every movement pattern, with English and Spanish aliases, the
  muscles, equipment, difficulty, contraindication tags and substitutions of each. Each
  substitution names an exercise of the catalogue with the same movement pattern.
- Alias matching is on the text normalised: lower case, accents removed, and spaces,
  hyphens, underscores and slashes collapsed into one space, so "Sentadilla búlgara",
  "sentadilla bulgara" and "pull-up"/"pull up" match. An alias names one
  exercise only: two exercises sharing an alias is a catalogue error.
- The athlete's own aliases (`coach strength alias`) are stored per athlete and map a text
  to a catalogue ID. One that equals a catalogue name or alias is refused (checked on the
  text as stored, after cleaning); it can never rename an exercise. An alias cannot look like
  an exercise ID (`ex.`), and its text is screened for disclosed secrets before it is stored
  (SEC-SEC-005): one that looks like a credential is refused, never stored. Unknown text
  resolves to nothing: it is never guessed.
- A new catalogue version may add exercises and aliases; it never reuses or removes an ID,
  since recorded sets refer to it.

## 4. Strength workout prescription (FR-STR-004)

Fields per [../contracts/workout.schema.json](../contracts/workout.schema.json): purpose,
estimated duration, warm-up, exercises (`exerciseId`, pattern, primary and secondary muscles,
sets, reps, load, target RPE, target RIR, rest, tempo, alternatives), progression rule,
regression rule, expected interference.

Every prescription explains: **what** (exercises), **why** (purpose in the current phase and
goal), **sets**, **reps**, **load or RPE or RIR**, **rest**, **target muscles**,
**progression**, **interference**. RPE and RIR targets are used only if the athlete is
familiar with them (profile); otherwise loads are prescribed conservatively with plain-language
effort cues.

Rules 1.0.0 (P10, precision of this section; `coach.domain.strength.prescription`, workout
contract 1.1.0):

- An exercise's movement pattern and muscles are the catalogue's (a prescription is built
  from the catalogue exercise and checked against it); its alternatives are catalogue
  exercises of the same movement pattern, never itself.
- Sets 1 to 20, a repetition range within 1 to 100, rest 0 to 900 s, tempo as four digits or
  X; a workout lasts 1 minute to 3 hours and has at least one exercise.
- Load: ABSOLUTE_KG (above 0, at most 1000), PERCENT_1RM (above 0, at most 100), RPE_BASED
  (the load that gives the target RPE or RIR, so it needs one) or BODYWEIGHT; the last two
  state no value.
- Effort: a target RPE only for an athlete whose profile says they are familiar with RPE, a
  target RIR only for one familiar with RIR; unknown familiarity counts as not familiar.
  Without RPE or RIR the exercise states an `effortCue` (contract 1.1.0): EASY or MODERATE,
  never HARD, with a stated load or bodyweight: that is the conservative prescription.
- Expected interference names body regions (section 2; contract 1.1.0, which widened
  `affectedRegions` from muscle groups to body regions, an additive change).

## 5. Execution capture (FR-STR-005)

`StrengthSetResult`: `workoutId`, `exerciseId`, `setNumber`, `targetReps`, `completedReps`,
`targetLoadKg`, `actualLoadKg`, `targetRpe`, `actualRpe`, `actualRir`, `recordedAt`, `source`
(ATHLETE, FIT). Sets recorded by a watch and sets entered by the athlete are reconciled per
field (FR-ACT-005); the athlete's entry wins for RPE and RIR.

Reconciliation (P10, precision of this section):

- The watch's sets are the SETS stream of the activity (files spec 8.7). The athlete's
  declarations about one strength activity are a `StrengthSession` (contract
  [../contracts/strength-session.schema.json](../contracts/strength-session.schema.json)),
  one per activity, its ID derived from the activity's: set entries (exercise, set number,
  any of completed repetitions, load, RPE, RIR and the targets), relabels and whether the
  watch's sets are used. Its ID is the activity's `strength.strengthSessionId` in the
  EFFECTIVE view. Sets are entered only on ACTIVE strength activities, and only for
  catalogue exercises (an ID, a catalogue name or an own alias; never guessed).
- `StrengthSetResult`s are a pure function of the SETS stream and the session. The watch
  numbers its ACTIVE sets per exercise in time order (REST sets are the rests between sets
  and are not results; a set the file gives no type is taken as active); an entry matches the
  watch set of the same exercise and set number.
- A field the athlete states wins: the entry corrects that set, as a DataOverride corrects
  an activity field (FR-ACT-006). RPE, RIR and targets come only from the athlete; duration
  and time only from the watch. `source` says who recorded the set (FIT when the watch did);
  each value keeps who supplied it. `recordedAt` is when the set was done (watch), else when
  the athlete entered it.
- A watch set no entry names stays as recorded; an entry no watch set matches is a set of its
  own. So a set is never counted twice.
- The watch may name an exercise wrongly or not at all (its own code without a catalogue
  equivalent: the exercise is UNKNOWN). A relabel names it for the whole session (only names
  the watch used in that session); the athlete can also ignore the watch's sets for a session,
  and then only the entries count. A relabel (or forgetting one) that would change which
  watch set an existing entry corrects is refused: the athlete removes those entries first, so
  an entry never silently moves to another set nor turns into an extra one.
- An entry without completed repetitions describes a set already done, so it names its set
  number (`--set`); with repetitions and no set number it is the exercise's next set.
- A session belongs to its activity: after a deduplication split moves the watch's file to
  another activity, the entries stay with the original one and the athlete reviews them.
- `workoutId` is UNKNOWN until planned workouts exist (P12). Commands: `coach strength sets`,
  `log`, `relabel` and `watch-sets`. Changes are audited (`strength.session_changed`) with
  identifiers only.

## 6. Progression (FR-STR-006)

Progression and regression decisions use **execution history** (StrengthSetResult), not only
planned workouts. Initial rules (versioned, P10):

- Progress when the last two exposures to an exercise met target reps at or below target RPE.
- Hold when targets were met with RPE above target.
- Regress when reps were missed or RPE exceeded the target by more than 1.5 in two exposures,
  or pain was reported for that region.

Rules 1.0.0 (P10, precision of this section):

- An exposure is an ACTIVE strength activity in which the exercise has executed sets
  (section 5). The decision reads the last two exposures within the weeks it covers (12 by
  default) and each one's sets against their own targets (the athlete's targets until planned
  workouts exist, P12): repetitions met when every set with a target reached it; the RPE gap
  is the actual RPE minus the target. An actual RIR without an RPE reads as RPE 10 - RIR
  (section 8). An exposure is MET (repetitions met at or below the target RPE), MET_ABOVE_RPE
  (met, at most 1.5 above), MISSED (repetitions missed or more than 1.5 above, whatever else
  is unknown), REPS_MET (no target RPE), NO_TARGETS or UNKNOWN (a value its targets need is
  unknown).
- Decision, in this order: REGRESS when pain was reported for a region the exercise loads
  since the first of the two exposures (any ACTIVE activity's pain; regions of section 2);
  INSUFFICIENT_DATA with fewer than two exposures; REGRESS when both were MISSED;
  INSUFFICIENT_DATA when one has no targets or an unknown value; HOLD when one was MISSED;
  INSUFFICIENT_DATA without a target RPE; PROGRESS when both were MET, except HOLD when pain
  was reported whose location names no region; otherwise HOLD (targets met above the target
  RPE, or not two exposures in a row met).
- A pain location is the athlete's free text: it names a region only when, normalised like an
  alias, it is exactly one of the region names (English and Spanish, for example "rodilla"
  for KNEES); anything else names no region and is never interpreted. Such pain can block a
  progression, never regress an exercise it may not concern. This is load management, not a
  diagnosis (SAFE-MED-001); red flags follow the safety spec.
- Each decision lists its reasons (codes), the exposures and their outcomes, and its evidence
  (the executed sets of each exposure and the pain reports used); it is a read model
  (`coach strength progress [exercise] [--weeks]`), not a stored decision.

## 7. Interference engine (FR-STR-007)

Inputs: planned and completed sessions within 48 hours before and after (including
unplanned activities and tennis), session intensity, muscle groups and body regions loaded,
athlete experience, recent load, recovery state, current phase, goal priority, execution
history.

Baseline rules (initial values, versioned, P10):

| Situation | Level |
|---|---|
| Heavy lower-body strength within 24 h before hard running intervals or a long run | HIGH |
| Heavy lower-body strength 24 to 48 h before hard running intervals | MODERATE |
| Heavy shoulder/upper-body strength within 24 h before a hard swim | MODERATE (possible interference) |
| Demanding tennis plus running intervals plus heavy lower body within 48 h | VERY_HIGH neuromuscular load |
| Tennis or plyometrics plus calf/Achilles-intensive work within 24 h | HIGH for calves, soleus, Achilles region |

Context modifiers may raise or lower a level only with explicit evidence (for example long
strength history and good recovery), and the evidence is stated. The output is an
`InterferenceAssessment {level, affectedRegions, sessions, evidence[], rulesVersion}`. The
planner avoids HIGH and VERY_HIGH combinations unless the evidence justifies them
(AT-STR-001).

## 8. Volume by muscle group (FR-STR-008)

Weekly hard sets per muscle group (`str.weekly_sets_by_muscle`) and volume load
(`str.volume_load`) are computed from execution data and reported weekly.

Rules (P10, version 1.0.0 of both metrics):

- Both read the executed sets (`StrengthSetResult`, section 5), never the plan.
- Volume load (`str.volume_load`, per activity, STANDARD_CALCULATION: McBride et al. 2009,
  J Strength Cond Res 23(1):106-110) is the sum of repetitions x external load (kg) over the
  sets whose repetitions and load are both known. A bodyweight set adds 0 kg. Sets with an
  unknown value are left out, counted in the value's inputs and flagged MISSING_DATA; with no
  such set, the value is UNKNOWN (MISSING_INPUT). It is recomputed whenever the session or the
  activity changes. Volume load does not compare protocols of different intensities (the
  reference's own caution). The week's volume load of STRENGTH is `str.volume_load_total`,
  shown with the other period totals (`coach load week`).
- A hard set is a set taken close to failure: at most 4 repetitions in reserve when the RIR
  is known, else RPE 6 or more (the RIR-based RPE scale, RPE 10 = 0 RIR: Zourdos et al. 2016,
  J Strength Cond Res 30(1):267-275). A set without RIR and RPE has an unknown effort: it is
  never assumed hard or easy.
- `str.weekly_sets_by_muscle` (WEEK subject, MUSCLE dimension, APPLICATION_DERIVED) adds up,
  per muscle group, one contribution per set of a known exercise: the muscle's share (primary
  1, secondary 0.5) when the set was hard, 0 when it was not, UNKNOWN when its effort is. Like
  every total (metrics spec 4), one UNKNOWN contribution makes the value UNKNOWN; the report
  still shows the known part and the number of sets of unknown effort. Sets of an exercise the
  watch did not name have no muscle group: they are counted apart. A muscle group no set worked
  in the week has no value (a former one is retired).
- `coach strength week [--week]` shows the week's sessions, sets, volume load, hard sets per
  muscle group and the unattributed sets.
