# Product: Personal Multisport Performance Coach

Spec version 1.1.0, materialised from the master SDD bootstrap specification v1.1.

## 1. Vision

A data-driven personal multisport coaching system that helps one athlete improve
performance by combining:

**Data platform + sport analytics engine + training planning engine + strength engine +
adaptive coach engine + AI coach.**

It is explicitly **not** "LLM + Garmin + big prompt". Software owns identity, security,
secrets, calculation, validation, storage, provenance, lineage, schema consistency,
provider communication, idempotency, concurrency and policy enforcement. The AI owns
interaction, interpretation, planning trade-offs, pattern explanation and coaching
communication.

## 2. Disciplines

| Discipline | Role |
|---|---|
| Running | Primary |
| Cycling | Primary |
| Swimming | Primary |
| Strength training | Primary and **first-class**: never an optional accessory (ADR-003) |
| Tennis | Recreational, but **always counts as training load** (FR-TEN-001) |

## 3. Core feedback loop (mandatory)

```
ATHLETE PROFILE -> GOALS -> BASELINE / HISTORY -> ANALYSIS -> TRAINING PLAN
      -> WORKOUT EXECUTION -> GARMIN / FILE / ATHLETE DATA -> PERFORMANCE EVALUATION
      -> INSIGHTS -> PLAN ADAPTATION -> (new TRAINING PLAN version)
```

## 4. Principles

- **Calculate in code, interpret with AI** (ADR-008). The LLM never performs authentication,
  authorization, secret handling, deterministic calculation, unit conversion, deduplication,
  persistence integrity, ownership checks, schema validation, consent enforcement or provider
  security.
- Security, privacy and safety by design; least privilege; human control over consequential
  external actions (ADR-005).
- Evidence-based, explainable decisions with provenance and lineage (NFR-TRC-001).
- Explicit uncertainty; unknown stays UNKNOWN; nothing is fabricated (FR-PRF-002).
- Idempotent ingestion, deterministic validation, graceful degradation, observability.
- Provider abstraction and cloud-neutral domain logic (ADR-007, ADR-014).

## 5. Users

V1 serves **one athlete** (the owner of the installation). The model supports several
athletes with strict ownership (SEC-AUTHZ-001) but no multi-tenant infrastructure is built
(assumption A-01). V1 is for **adults**: athletes under 18 are out of scope (assumption A-17).

## 6. V1 scope (Definition of Done, master spec §82)

V1 demonstrates: onboarding; profile; goals and events; availability; consent; canonical
Activity; RAW/NORMALIZED/DERIVED separation; FIT, TCX, GPX and CSV ingestion; deduplication;
provenance; data quality; provider abstraction; Garmin synchronisation **according to the
actual capabilities**; running, cycling and swimming analytics; tennis workload; exercise
catalogue; strength planning and execution; interference rules; daily check-in; recovery
analysis; insights; training plans with immutable versioning; planned-vs-completed
comparison; unplanned activities; plan adaptation; evidence-based explanations; privacy
controls; ownership; secret isolation; security testing; safety boundaries; degraded
operation without Garmin.

**V1 constraint from verified provider capabilities:** the Garmin Personal tier is pull-only
and read-only, without Training or Courses APIs (see
[integration/garmin.spec.md](integration/garmin.spec.md)). Garmin workout publication is
therefore **not** in V1; workouts are generated locally (AT-GAR-001). Publication becomes
in scope only if a tier with `trainingPublish` is configured.

## 7. Out of scope for V1

Clinical nutrition, meal planning, supplement prescriptions, clinical rehabilitation,
medical diagnosis, social or community features, unrelated wellness diagnostics, athletes
under 18. V1 must not expand into these domains automatically.

## 8. Glossary and conventions

See [glossary.md](glossary.md) and [README.md](README.md).
