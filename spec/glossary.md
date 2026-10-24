# Glossary

| Term | Meaning |
|---|---|
| Athlete | The person being coached. Owns all athlete data (ownership root). |
| User | An authenticated principal. Mapped to exactly one athlete in V1. |
| Activity | A **logical** training session, independent of how many sources describe it. |
| Activity source | One raw description of an activity (a provider record or an uploaded file). Several sources may enrich one Activity. |
| RAW | Data exactly as received from a provider or file. Immutable. |
| NORMALIZED | Canonical units and domain representation, with per-field provenance. |
| EFFECTIVE | Normalized values with DataOverride corrections applied at read time. |
| DERIVED | Deterministically calculated metrics with method, version and inputs. |
| Provenance | Where a value came from: source, path within the source, resolution rule, override. |
| Lineage | The chain recommendation -> insight -> derived metric -> normalized data -> raw data -> source. |
| UNKNOWN | An applicable value that is not known. Represented as `null` in contracts. Never replaced by a guess. |
| Data quality | Completeness, reliability, anomalies and confidence (HIGH, MEDIUM, LOW) of a record. |
| Uncertainty reason | Why a conclusion is uncertain: MISSING_DATA, SENSOR_QUALITY, NON_COMPARABLE_SESSION, ENVIRONMENT, INSUFFICIENT_HISTORY, ESTIMATED_VALUE. |
| Training day | The local calendar date at activity start (FR-ACT-004). |
| External load | What was done: duration, distance, elevation, power, repetitions, sets, lifted load. |
| Internal load | How the body responded: heart-rate response, session RPE, fatigue, recovery response. |
| RPE | Rating of perceived exertion, CR-10 scale (0 to 10). |
| Session RPE (sRPE) | RPE x duration in minutes, in arbitrary units (AU). |
| RIR | Repetitions in reserve: reps the athlete could still have done at the end of a set. |
| Interference | Negative interaction between training sessions of different modalities (for example heavy legs before running intervals). |
| Plan version | An immutable snapshot of a training plan. Changes create a new version. |
| Planned workout | A workout inside a plan version (endurance, strength, tennis, recovery, rest, evaluation). |
| Workout execution | The link between a planned workout and the activities that fulfilled it, with status and compliance. |
| Unplanned activity | A completed activity with no matching planned workout. Still counts as load. |
| Policy engine | Deterministic component that allows, denies or requires confirmation for an action. AI cannot bypass it. |
| Capability | A provider feature (for example `trainingPublish`) with a basis: CONFIGURED, OBSERVED or UNKNOWN. |
| Access tier | Provider access level: PERSONAL, STARTER_BUSINESS, ENTERPRISE, UNKNOWN. |
| Red flag | A declared symptom that requires stopping intensive training and seeking healthcare evaluation. |
| SAFETY_HOLD | Coaching condition activated by a red flag; blocks intensive recommendations. |
| Insight | A structured finding: observation, interpretation, hypothesis, recommendation, evidence, confidence. |
| AgentDecision | Audit record of a significant AI decision: versions, model, evidence, action, explanation. No chain-of-thought. |
| Tool | A narrow, typed application operation exposed to the AI coach. |
| Consent | An athlete's recorded permission for a data category and purpose. |
| Owner scope | The authenticated athlete identity that every repository query is restricted to. |
| Idempotency key | A deterministic key that makes a repeated command or import produce the same result once. |
