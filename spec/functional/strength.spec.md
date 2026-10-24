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

## 3. Exercise catalogue (FR-STR-003)

`Exercise`: `exerciseId` (pattern `ex.<snake_case>`, stable), `canonicalName`,
`movementPattern`, `primaryMuscles[]`, `secondaryMuscles[]`, `equipment[]`, `unilateral`,
`technicalDifficulty` (1 to 5), `contraindicationTags[]` (for example AXIAL_LOAD, OVERHEAD,
HIGH_IMPACT, DEEP_KNEE_FLEXION), `substitutions[]` (exercise IDs), `aliases[]`
(`{language, text}`).

- Identity is the `exerciseId`, never free text. Aliases (any language, user-defined) map to
  an ID and never change it.
- The catalogue is versioned reference data shipped with the application (seed list in P10).

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

## 5. Execution capture (FR-STR-005)

`StrengthSetResult`: `workoutId`, `exerciseId`, `setNumber`, `targetReps`, `completedReps`,
`targetLoadKg`, `actualLoadKg`, `targetRpe`, `actualRpe`, `actualRir`, `recordedAt`, `source`
(ATHLETE, FIT). Sets recorded by a watch and sets entered by the athlete are reconciled per
field (FR-ACT-005); the athlete's entry wins for RPE and RIR.

## 6. Progression (FR-STR-006)

Progression and regression decisions use **execution history** (StrengthSetResult), not only
planned workouts. Initial rules (versioned, P10):

- Progress when the last two exposures to an exercise met target reps at or below target RPE.
- Hold when targets were met with RPE above target.
- Regress when reps were missed or RPE exceeded the target by more than 1.5 in two exposures,
  or pain was reported for that region.

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
