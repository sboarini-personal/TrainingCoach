# Secrets

Owner of SEC-SEC-001 to SEC-SEC-006. Master spec §13, §14, §61. ADR-001, ADR-015.

## 1. Secret inventory

| Secret | Owner | Store |
|---|---|---|
| Garmin Personal API key | athlete | OS secret store (Windows Credential Manager via `keyring`) |
| Garmin access token (24 h) | integration layer | process memory only |
| Local API token | installation | OS secret store |
| LLM provider API key | installation | OS secret store |

## 2. CredentialProvider (SEC-SEC-001)

- Application port: `get(handle) -> SecretValue`, `put(handle, SecretValue)`,
  `delete(handle)`. V1 adapter: `keyring`. A managed store (for example Azure Key Vault) may
  be added later behind the same port.
- `SecretValue` wraps the value; its `repr`, `str` and serialisation produce `***`. The raw
  value is exposed only through an explicit method used by the HTTP client at request time.
- Secrets are never hardcoded, never in `.env` files inside the repository, never in source
  control.
- `keyring` adapter (P4): service name `CoachDeportivo`, user name = handle. The handle is
  `<kind>/<athleteId>` with kind `local_api_token`, `garmin_personal_api_key` or
  `llm_api_key`. Handles are validated before they reach the store.
- The database stores a `CredentialRecord` per secret (contract
  [../contracts/credential-record.schema.json](../contracts/credential-record.schema.json)):
  kind, handle, creation and rotation times and the expiry date. Never the value or a hash of
  it.
- Secrets are entered through `coach secret set <kind>` with hidden input; the value is
  never echoed, printed, logged or passed on the command line.

## 3. Never exposed (SEC-SEC-002, SEC-SEC-003)

Secrets MUST NOT appear in: prompts or LLM context, chat history, logs (any level), URLs or
query strings, database fields, error messages, telemetry, knowledge sources, test fixtures.

Controls:

- The agent cannot import integrations or infrastructure (import-linter contract) and has no
  secret-reading tool.
- A logging filter redacts known secret formats and high-entropy tokens (NFR-OBS-002).
- Exceptions raised by HTTP clients are re-wrapped so that request headers are not included.
- AT-SEC-002 scans logs, error responses, prompts sent to a fake LLM, and the database after a
  full test flow for planted secret values.

## 3.1 Redaction rules

The same deterministic detector serves log redaction (NFR-OBS-002) and chat secret detection
(SEC-SEC-005). It replaces matches with `[REDACTED-SECRET]`:

| Rule | Matches |
|---|---|
| JWT | three base64url segments starting with `eyJ` |
| PEM | `-----BEGIN ... PRIVATE KEY-----` blocks |
| Authorization scheme | values after `Bearer`, `ApiKey`, `Basic` |
| Named assignment | values after names such as `api_key`, `apikey`, `secret`, `client_secret`, `password`, `passwd`, `token`, `access_token`, `refresh_token`, `cookie`, `authorization`, followed by `=` or `:` |
| Known prefixes | `sk-ant-`, `ghp_`, `github_pat_`, `AKIA` followed by key material |
| High entropy | uninterrupted alphanumeric runs of 20 or more characters containing letters and digits, with Shannon entropy of at least 3.5 bits per character |

Entropy alone does not separate secrets from file paths or package names (both measure about
4 bits per character); the length of the uninterrupted alphanumeric run does, because paths and
names break into short words at `/`, `-`, `_` and `.`. UUIDs are not matched for the same
reason. Full 64-character hashes are redacted, so the logging convention is to log content
hashes as their first 12 hexadecimal characters (`sha256:1a2b3c4d5e6f`).

## 4. Rotation and expiry (SEC-SEC-004)

- Every secret can be replaced by a CLI command without code changes.
- The Garmin key expiry date (entered by the athlete at storage time) is tracked; reminder 7
  days before; IntegrationStatus shows expiry.
- Recommended Garmin key expiry: 90 days.
- Expiry status per credential: `NO_EXPIRY`, `VALID`, `EXPIRING` (expires within 7 days,
  inclusive) or `EXPIRED` (expiry date before today). `coach secret list` shows it.
- Storing a secret whose kind already has a record is a rotation: the record keeps its ID,
  gets `rotatedAt`, and the event is `credential.rotated` instead of `credential.stored`.

## 5. Accidental disclosure (SEC-SEC-005)

When an athlete message (or uploaded text) contains a probable credential, for example an
API key, client secret, password, token, cookie or recovery code:

1. It is detected **before persistence and before any LLM call** by a deterministic detector
   (known prefixes and formats such as JWTs, PEM blocks and `key=value` patterns with secret
   names, plus high-entropy strings).
2. The value is replaced by `[REDACTED-SECRET]` in everything stored or sent onwards.
3. The coach never repeats it.
4. The athlete is told it is potentially compromised and should be revoked or rotated (for
   Garmin: delete the key in Garmin Connect and create a new one), and is pointed to the
   secure storage command.
5. `security.secret_detected` is emitted (without the value).

The detector is the redaction rule set of section 3.1. Named assignments also cover the
Spanish names `contraseña` and `contrasena`. The athlete notice is fixed text in the athlete's
language (Spanish and English in V1), produced in code, not by the LLM.

AT-SEC-001 verifies all five steps (detector and notice in P4, agent behaviour in P13).

## 6. Repository scanning (SEC-SEC-006)

- `detect-secrets` runs as a pre-commit hook and in CI against `.secrets.baseline`.
- A pre-commit hook blocks athlete data files (FIT, TCX, GPX, ZIP, databases) outside
  `tests/fixtures/`.
- Test fixtures use synthetic data only.
