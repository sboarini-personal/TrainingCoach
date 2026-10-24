# Threat model (STRIDE), version 0

Status: v0 written in P1 from the specification. Version 1 is due in P4 (security
architecture) and is reviewed at the end of every phase that adds an attack surface (P6
uploads, P7 provider, P13 agent).

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
| Repositories | I/E: SQL injection | parameterised queries, ruff S608 | SEC-INJ-003 |
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

## 5. Explicitly analysed threat classes

Prompt injection, IDOR, SSRF, XXE, malicious files, oversized files, replay, webhook spoofing
(not applicable in V1), token theft, authorization bypass, mass assignment, secret leakage:
all appear in section 3 with mitigations and verifying acceptance tests (spec/acceptance).
