# ADR-035: Athlete data may live in a synchronised folder by the athlete's choice

- Status: Accepted (athlete decision D-5, athlete's review of 2026-10-31)
- Date: 2026-10-31
- Requirements: PRIV-STO-001, SEC-SEC-001, PRIV-DEL-001
- Related: ADR-014 (local-first deployment; its data-location rule is amended here)

## Context

ADR-014 kept the data directory out of synchronised folders: the application refused
OneDrive, Dropbox and Google Drive paths unless an override variable was set. In the review of
2026-10-31 the athlete chose to allow any synchronised folder, including the organisational
OneDrive, after these risks were explained:

- an organisational tenant's retention and eDiscovery policies may reach the health data and
  keep it after the athlete deletes it;
- SQLite in a synchronised folder can be damaged, or duplicated as conflict copies, when two
  computers use it at the same time.

## Decision

- The default data directory stays local and unsynchronised (`%LOCALAPPDATA%\CoachDeportivo`).
- A synchronised folder is accepted without an override. `coach init` names the risks once in
  a notice, and the choice is logged as the security event `security.synced_data_dir`.
  `COACH_ALLOW_SYNCED_DATA_DIR` is retired.
- A data directory inside a git working tree is always refused: athlete data never goes in a
  repository. The synchronised-folder refusal used to cover this incidentally, because the
  repository lives in OneDrive.
- Secrets stay in the OS secret store, never in the data directory (SEC-SEC-001).

## Consequences

- The athlete can use the data from more than one computer through the cloud folder, but
  only from one at a time.
- Deletion (PRIV-DEL-001, P15) cannot guarantee removal from a tenant's retention; the
  deletion workflow must say so when the data directory is synchronised.
- Threat model R-01 changes from "code and docs only" to "the athlete's choice for data".
