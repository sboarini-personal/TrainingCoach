# ADR-014: Local-first deployment; data and secrets outside synced folders

- Status: Accepted (athlete decision D-1, 2026-10-24)
- Date: 2026-10-24
- Requirements: PRIV-DEL-001, SEC-SEC-001, SEC-SEC-003, SEC-AUTH-002

## Context

V1 serves one athlete on a personal Windows PC. The athlete decided to keep the **repository**
in its current folder, which is synchronised by OneDrive to an organisational Microsoft 365
tenant. Health data in an organisational tenant could be retained or accessed outside the
athlete's control and could defeat deletion (threat model R-01).

## Decision

- Runtime: one local process (API on loopback, background sync, CLI). No inbound internet
  surface; outbound HTTPS only to Garmin and the LLM provider.
- Storage: SQLite (WAL) plus a content-addressed blob store in a **data directory outside any
  synced folder**, default `%LOCALAPPDATA%\CoachDeportivo`, configurable through
  `COACH_DATA_DIR`. The application refuses a data directory inside a known synced path unless
  an explicit override flag is set.
- Secrets: OS secret store (Windows Credential Manager) only.
- Repository: code, specs and synthetic fixtures only. `.gitignore` and a pre-commit hook block
  athlete data files.
- Encryption at rest relies on OS full-disk encryption in V1 (assumption A-10).
- Cloud hosting (for example Azure) stays possible by swapping infrastructure adapters; no cloud
  service is introduced without a stated need.

## Consequences

- Accepted residual risk: source code and documentation sync to the organisational tenant.
  They contain no athlete data or secrets.
- `.git` inside a synced folder can occasionally conflict; pushing to a remote (ADR-020) keeps
  a safe copy.

## Verification

- Data-directory guard unit test (P3).
- Secret scanning and athlete-data hooks on every commit (ADR-020).
