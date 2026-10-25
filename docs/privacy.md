# Privacy

Normative rules: `spec/security/privacy.spec.md`. This document records the data inventory and
context.

## 1. Data inventory

| Category | Examples | Source | Purpose | Consent | Location | Sent to LLM |
|---|---|---|---|---|---|---|
| Profile | name, birth date, height, weight, HR settings, devices | athlete | planning, metrics | onboarding | local database | minimised, with AI_PROCESSING |
| Health constraints, pain, red flags | declared constraints, pain reports | athlete | safety, planning | onboarding | local database | only what the current advice needs |
| Activities | summaries, laps, streams, per-source observations, corrections with the athlete's reason, data quality issues | files, Garmin, athlete | analytics, load, adaptation | ACTIVITIES | local database and blob store | derived summaries only; no GPS traces; correction reasons never |
| Recovery | resting HR, sleep, HRV, Body Battery, stress | Garmin, athlete | recovery assessment | RECOVERY, SLEEP, HRV, PROVIDER_RECOVERY_METRICS | local database | derived summaries |
| Check-ins | sleep quality, fatigue, soreness, stress, motivation | athlete | recovery | onboarding | local database | yes, with AI_PROCESSING |
| Plans, insights, decisions | plan versions, insights, AgentDecisions | system | coaching | none extra | local database | as needed |
| Credentials | Garmin key, tokens | athlete, system | integration | none | OS secret store or memory | **never** |
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

## 4. Athlete rights

Inspect and export, correct (DataOverride), withdraw consent, request deletion: see the
privacy spec. Deletion covers every store and is verified (AT-DEL-001).

## 5. Legal context (informational, not legal advice)

Health-related data is commonly treated as a special or sensitive category in data-protection
law (for example, Argentina's Personal Data Protection Law 25.326 defines health data as
sensitive data). The design follows consent, minimisation and deletion principles. If the
system ever serves anyone other than its owner, a proper legal review is required first.
