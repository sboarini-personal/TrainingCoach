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

## 4. Rotation and expiry (SEC-SEC-004)

- Every secret can be replaced by a CLI command without code changes.
- The Garmin key expiry date (entered by the athlete at storage time) is tracked; reminder 7
  days before; IntegrationStatus shows expiry.
- Recommended Garmin key expiry: 90 days.

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

AT-SEC-001 verifies all five steps.

## 6. Repository scanning (SEC-SEC-006)

- `detect-secrets` runs as a pre-commit hook and in CI against `.secrets.baseline`.
- A pre-commit hook blocks athlete data files (FIT, TCX, GPX, ZIP, databases) outside
  `tests/fixtures/`.
- Test fixtures use synthetic data only.
