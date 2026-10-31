# Privacy

Normative rules: `spec/security/privacy.spec.md`. This document records the data inventory and
context.

## 1. Data inventory

| Category | Examples | Source | Purpose | Consent | Location | Sent to LLM |
|---|---|---|---|---|---|---|
| Profile | name, birth date, height, weight, HR settings, devices, sensors and their reliability, FTP, usual pool length | athlete | planning, metrics | onboarding | local database | minimised, with AI_PROCESSING |
| Health constraints, pain, red flags | declared constraints (description screened for secrets; body regions by exact name, never interpreted; restrictions such as no impact or a maximum RPE) and when they were confirmed, pain reports; from P12 a recent pain report restricts load on the region it names | athlete | safety, planning | onboarding | local database | only what the current advice needs |
| Activities | summaries, time series, laps and pool lengths (no positions, no device identifiers), per-source observations and their revisions, corrections with the athlete's reason, data quality issues, ingestion records (hashes, codes, sanitised filenames) | files, Garmin, athlete | analytics, load, adaptation | ACTIVITIES | local database and blob store; uploads wait in quarantine only while processed | derived summaries only; no GPS traces; correction reasons never |
| Derived metrics (P8) | speed, pace, time in heart-rate zones, session RPE load, daily and weekly totals, each with its input snapshot | computed from activities and the zone model | analytics, load | ACTIVITIES | local database | derived summaries only, with AI_PROCESSING (P13) |
| Strength (P10) | strength sets recorded by a watch (SETS stream), the athlete's set entries (repetitions, loads, RPE, RIR, targets), relabels, own exercise aliases (profile); derived volume load and weekly hard sets per muscle group | files, Garmin, athlete | analytics, load, progression, interference | ACTIVITIES | local database and blob store | derived summaries only, with AI_PROCESSING (P13) |
| Recovery | per day: resting HR (RECOVERY), overnight HRV (HRV), measured sleep (SLEEP), Body Battery charged (PROVIDER_RECOVERY_METRICS), each with when it was received; Garmin stress is not fetched. Garmin's responses are never stored: they carry other categories (sleep stress, heart rate, SpO2 and a note in the sleep summary; abnormal heart rate values; sleep and stress events in the Body Battery report), so RAW keeps only a minimised document of the values read (ADR-032) | Garmin | recovery assessment | one consent per metric: neither fetched, stored nor read without it | RAW blob store (minimised) and local database | derived summaries |
| Check-ins | sleep quality, fatigue, soreness, stress, motivation, pain (intensity, location, onset), red-flag answers, red flags detected in the notes and the pain answers, notes (screened for secrets) | athlete | recovery, safety | none extra: the athlete's own declaration (recovery spec 1) | local database | yes, with AI_PROCESSING |
| Recovery assessments (P11) | per day: status, the signals compared with the athlete's baseline, pain intensity; prescriptions (REST, ACTIVE_RECOVERY, EASY_SESSION, REDUCED_VOLUME) | computed from check-ins and consented recovery metrics | recovery, load decisions | through their inputs: a metric without consent is never read | local database | derived summaries, with AI_PROCESSING (P13) |
| Insights (P11) | pattern ID, the days covered, an observation written in code, evidence references; associations with sample size | computed from the data above | coaching | through their inputs: a pattern whose data has no consent is not evaluated, and an insight is read only while its data's consents are granted | local database | yes, with AI_PROCESSING (P13) |
| Reports (P11) | the data part of post-activity, weekly and monthly reports | computed on request | coaching | through their inputs | not stored (read models) | yes, with AI_PROCESSING (P13) |
| Planning inputs (P12) | weekly availability, exceptions and restrictions (screened for secrets), resources and equipment, per-sport experience, sport events (name, place, conditions screened for secrets), goals with their baseline snapshot, readiness answers | athlete | planning, feasibility | none extra: the athlete's own declaration | local database | minimised, with AI_PROCESSING (P13) |
| Plans, decisions (P12) | plan headers and immutable plan versions (content and its hash); the athlete's execution links; adaptation decisions (reason codes, evidence references, an interpretation written in code, never athlete text); AgentDecisions (P13) | system, athlete | coaching | none extra; executions and adaptation read activities only under ACTIVITIES | local database | as needed, with AI_PROCESSING (P13) |
| Strength sets files (P12) | a CSV file of set entries the athlete imports; the export the athlete asks for | athlete | strength capture | ACTIVITIES | the imported file is read and not kept (its rows become set entries); an export is written only where the athlete names it | never |
| Garmin payloads (P7) | activity details records, original activity files, activity list pages, sync status (codes, dates, activity IDs) | Garmin Personal API | activity synchronisation | ACTIVITIES | RAW blob store and local database | never as RAW; activities as above |
| Credentials | Garmin key (OS secret store, with the expiry date in the database), access tokens (memory only, never written) | athlete, system | integration | none | OS secret store or memory | **never** |
| Audit | event codes, IDs | system | security | none | local database | never |
| Not collected by default | women's health, nutrition logs, weight goals | none | none | WOMENS_HEALTH not offered in V1 | none | none |

## 2. Storage location

All athlete data lives in the data directory outside synced folders (default
`%LOCALAPPDATA%\CoachDeportivo`) and in the OS secret store (ADR-014). The repository contains
no athlete data. Backups are created only on request and go where the athlete chooses.

## 3. Third parties

| Party | Role | Data | Basis |
|---|---|---|---|
| Garmin | data source (pull) | the athlete's own Garmin data | athlete's Garmin account and Personal API terms |
| LLM provider (default Anthropic) | processor for coaching conversations | minimised context | AI_PROCESSING consent; provider data-retention settings to be reviewed by the athlete |

Open question G-5: whether Garmin's Personal API terms allow processing Garmin data with a
third-party AI. The athlete reviews the terms they accepted when creating the key before
Garmin-sourced data is sent to the LLM.

Garmin data minimisation (P7). The sync calls only the activity search, details and download
endpoints; no wellness, sleep, Women's Health, nutrition or weight endpoint is called.
Payloads are kept byte-exact as RAW (INT-GAR-009), so they hold everything Garmin puts in
them: an activity record also carries proprietary metrics (stress, Body Battery change,
SpO2), and a list page may carry social data (names of people who liked or commented). Those
fields are never normalised, sent to the AI or exported; only the mapped activity fields
(garmin spec section 10) reach the canonical activity. The deletion workflow removes RAW
payloads with everything else.

## 4. Athlete rights

Inspect and export, correct (DataOverride), withdraw consent, request deletion: see the
privacy spec. Deletion covers every store and is verified (AT-DEL-001).

## 5. Legal context (informational, not legal advice)

Health-related data is commonly treated as a special or sensitive category in data-protection
law (for example, Argentina's Personal Data Protection Law 25.326 defines health data as
sensitive data). The design follows consent, minimisation and deletion principles. If the
system ever serves anyone other than its owner, a proper legal review is required first.
