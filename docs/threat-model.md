# Threat model (STRIDE), version 1

Status: v1, P4 (security architecture). v0 was written in P1 from the specification and
extended in P3 with storage threats; v1 records the implemented authentication, credential,
audit, consent and policy controls. Reviewed at the end of every phase that adds an attack
surface (P6 uploads, P7 provider, P13 agent).

## 1. Assets

| Asset | Sensitivity |
|---|---|
| Athlete health and training data (HR, HRV, sleep, health constraints, pain reports, GPS) | High: personal health data |
| Garmin Personal API key and access tokens | High: grant access to all of the athlete's Garmin data |
| LLM provider API key, local API token | High |
| Training plans, insights, agent decisions | Medium |
| Audit log | Medium (integrity matters) |
| Source code and specifications | Low (no secrets or personal data) |

## 2. Trust boundaries

```
[Browser / other local processes] --B1--> [Local API (loopback)] --> [Application]
[Uploaded files] --B2--> [Ingestion + sandboxed parsers]
[Garmin Connect] --B3--> [Garmin adapter]           (outbound pull only)
[Application] --B4--> [LLM provider]                (outbound, consent-gated)
[Application] --B5--> [OS secret store]
[Repository folder] --B6--> [OneDrive organisational tenant, GitHub]
```

## 3. STRIDE analysis

| Component | Threat (STRIDE) | Mitigation | Requirements |
|---|---|---|---|
| Local API (B1) | S: unauthenticated local process or web page calls the API; CSRF; DNS rebinding | loopback bind, Host allow-list, bearer token or session with CSRF token, no CORS | SEC-AUTH-001, SEC-AUTH-002 |
| Local API | E: authorization bypass; I: IDOR | owner-scoped repositories, NOT_FOUND for foreign IDs | SEC-AUTHZ-001, SEC-IDOR-001 |
| Local API | T: mass assignment | allow-listed command models | SEC-AUTHZ-003 |
| Local API | D: request flooding | rate limits | SEC-AUTH-003 |
| Garmin authentication (B3, B5) | I: key or token theft through logs, `.env`, synced folders, token cache, chat | keyring storage, in-memory token, redaction, secret scanning, chat secret detector | SEC-SEC-001 to SEC-SEC-005, INT-GAR-006 |
| Garmin authentication | S: replay of a stolen key | short key expiry (90 days recommended), rotation, deletion instructions | SEC-SEC-004, PRIV-DEL-002 |
| OAuth/PKCE | Not applicable in V1 (API key to token). Business tier: PKCE S256, state, exact redirect URIs, token storage in the secret store | documented for later | none |
| Push / webhooks | Not applicable in V1 (pull-only, no inbound endpoint). Business tier: signature verification, timestamp window against replay, idempotent handling, spoofing defence | documented for later | none |
| Garmin responses (B3) | T: malformed or oversized payloads; D: large ZIP downloads | schema validation before normalisation, archive limits, size caps in transport | SEC-UPL-003, INT-GAR-009 |
| File upload (B2) | T/D: malicious files, polyglots, oversized files, path traversal through filenames | ordered pipeline, signature checks, limits, hash-named storage, quarantine | INT-FIL-002, INT-FIL-003, SEC-UPL-001, SEC-UPL-005 |
| FIT parser | D: pathological files exhaust CPU or memory; crash | sandboxed subprocess, timeout, record limits | SEC-UPL-004, ADR-019 |
| XML parser (TCX/GPX) | I/D: XXE, SSRF through external entities, entity expansion | defusedxml, DTD forbidden, no network resolution | SEC-UPL-002 |
| CSV | T: formula injection on export; D: huge rows | export escaping, size and row limits | SEC-INJ-004 |
| Archives | D: zip bomb; T: traversal, symlinks, nested archives | streaming caps, no extraction to paths | SEC-UPL-003 |
| Malware | T: infected files stored | scan hook; unscanned imports flagged | SEC-UPL-006 |
| Agent tools | E: prompt injection drives tool misuse; I: exfiltration through tool arguments; T: injected "publish" or "delete" | narrow tools, no URL/HTTP/SQL/shell/secret tools, no athlete ID parameter, policy engine, human confirmation tokens the agent never sees | FR-AGT-001, FR-AGT-002, SEC-INJ-001, SEC-INJ-002, FR-POL-002 |
| LLM provider (B4) | I: personal data disclosed to a third party; secrets in prompts | AI_PROCESSING consent, minimisation, pre-LLM secret redaction, no GPS traces | PRIV-CON-003, SEC-SEC-002, SEC-SEC-005 |
| Repositories | I/E: SQL injection | parameterised queries only (no formatted SQL), ruff S608; tested with SQL metacharacters (P3) | SEC-INJ-003 |
| Storage integrity | T: RAW data or derived values silently rewritten by a bug or a direct database edit | database triggers reject updates to RAW sources and any change to a derived value except the one-time supersession link; blobs read-only and verified against their hash on every read (P3) | FR-ACT-002, FR-MET-004 |
| Blob store | I: one athlete learns that another stored the same file (existence oracle); D/T: path tricks | blobs partitioned per athlete; paths built only from the athlete UUID and a validated SHA-256 (P3) | SEC-IDOR-001, SEC-UPL-005 |
| Data directory | I: health data synchronised to an organisational cloud tenant | synced-folder guard refuses OneDrive/Dropbox/Google Drive paths; explicit, logged override only (P3) | PRIV-STO-001 |
| Local authentication (P4) | S: guessing or replaying the local API token; brute force | 256-bit token in the OS secret store, constant-time digest comparison, read from the store on every attempt (rotation is immediate), one generic failure message, lockout after 5 failures in 60 s doubling to 1 h, failures audited and emitted | SEC-AUTH-001, SEC-AUTH-003 |
| Authorization chain (P4) | E: a principal without an athlete link acting on data; a caller choosing the athlete | owner scope derived only from the authenticated principal through the identity directory; no service takes an athlete ID from input; denials audited | SEC-AUTHZ-001 |
| Credentials (P4) | I: secret in the database, logs, CLI arguments or output; arbitrary secret-store access | values only in the OS secret store; database keeps metadata without value or hash; derived handles validated before reaching the store; hidden input; `SecretValue` never renders; leakage sweep test plants secrets and scans output, logs, files and errors | SEC-SEC-001 to SEC-SEC-004, AT-SEC-002 |
| Chat and pasted text (P4) | I: athlete pastes a key; it is stored or sent to the LLM | deterministic screening before storage and LLM; fixed notice text recommending rotation; event without the value | SEC-SEC-005, AT-SEC-001 |
| Audit log (P4) | T/R: editing, deleting or reordering events; reassigning events to another athlete; I: identifiers surviving deletion | hash chain with gapless sequence, verification command; triggers allow only nulling the plain athlete column; all identifiers inside events are salted hashes (ADR-023) | SEC-AUD-001, PRIV-DEL-003, AT-AUD-001 |
| Observability events (P4) | I: free text or secrets in events | event fields restricted to code-like values; redacting log formatter on the file log | NFR-OBS-001, NFR-OBS-002 |
| Consent and policy (P4) | E: processing without consent; AI or interface loosening the policy context | consent ledger enforced by the policy engine with context assembled from stored state; deny-overrides; denials audited | PRIV-CON-002, FR-POL-001, FR-POL-002 |
| Schema evolution | T/D: an automatic migration destroys data | migration runner refuses unapproved destructive migrations, downgrades and edited migrations (P3) | NFR-REL-004 |
| External workout publication | T/R: unconfirmed or replayed writes | capability gate (unsupported in V1), confirmation tokens, idempotency per proposal, audit | INT-GAR-002, INT-GAR-003, SEC-AUD-001 |
| Administrator / host | I: other OS users or malware read the database and blobs | data outside synced folders, per-user ACL, OS full-disk encryption, no secrets in the database; no admin API | ADR-014, A-10 |
| Audit | R: actions denied by athlete or agent; tampering | append-only, hash-chained events | SEC-AUD-001 |
| Replay | duplicate commands, imports or syncs | Idempotency-Key, content hashes, provider IDs | NFR-REL-001 |
| Repository (B6) | I: athlete data or secrets committed and synced or pushed | `.gitignore`, athlete-data pre-commit hook, detect-secrets, private GitHub repository | SEC-SEC-006 |
| Supply chain | T: compromised dependency or CI action | `uv.lock`, pip-audit, minimal dependencies, read-only CI token; pin actions to SHAs (follow-up) | ADR-020 |

## 4. Residual risks

| ID | Risk | Owner decision |
|---|---|---|
| R-01 | Repository folder syncs to an organisational OneDrive tenant | Accepted for code and docs only (ADR-014); athlete data and secrets stay outside |
| R-11 | Garmin Personal API terms on third-party AI processing unknown (G-5) | Athlete review before P13 uses Garmin data |
| R-12 | Memory limits for parsers are best effort on Windows | Record limits and timeouts apply everywhere |
| R-13 | CI actions pinned to tags, not SHAs | Follow-up after the GitHub remote exists |
| R-14 | Any process running as the same OS user can read the Windows Credential Manager entries and the data directory | Accepted for V1 single-user local use; OS account security and full-disk encryption are the boundary (A-10) |
| R-15 | `coach token rotate` issues a token without the old one when it is missing from the store | Accepted: whoever can run commands as that OS user can already read the store (authentication.spec.md 1.1) |
| R-16 | The audit salt is stored with the data; pseudonymity rests on the random part of UUIDv7 identifiers | Accepted (ADR-023) |
| R-17 | Lockout and rate-limit state is in memory and resets on restart | Accepted for a local single process; a hosted deployment needs shared state |

## 5. Explicitly analysed threat classes

Prompt injection, IDOR, SSRF, XXE, malicious files, oversized files, replay, webhook spoofing
(not applicable in V1), token theft, authorization bypass, mass assignment, secret leakage:
all appear in section 3 with mitigations and verifying acceptance tests (spec/acceptance).
