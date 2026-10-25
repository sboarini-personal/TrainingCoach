# ADR-013: Python stack

- Status: Accepted (athlete decision D-2, 2026-10-24); persistence row amended by ADR-022
- Date: 2026-10-24
- Requirements: INT-FIL-001, SEC-UPL-002, INT-GAR-005

## Context

The repository was empty. Python, .NET 8 and Node are installed. Garmin publishes an official
FIT Python SDK and an official Personal API Python client.

## Decision

Python >= 3.12 (development on 3.14), managed with uv. Planned libraries, each added only in
the phase that needs it:

| Need | Library |
|---|---|
| IANA time zones on Windows, which has no system tz database (added P2) | `tzdata` |
| FIT decoding | `garmin-fit-sdk` (official) |
| Garmin Personal API transport | `garmin-connect-personal-api-python` (official), data client only |
| Hardened XML | `defusedxml` |
| Typed models | Pydantic v2 |
| Numerics | NumPy |
| HTTP API | FastAPI |
| Persistence | SQLite via the standard `sqlite3` module and a small migration runner (ADR-022; originally SQLAlchemy 2 with Alembic) |
| Secrets | `keyring` |
| LLM | Anthropic SDK behind an `LLMProvider` port |

## Consequences

- Provider-maintained libraries reduce the need to guess formats or endpoints.
- `requires-python >= 3.12` follows the official Personal API client.

## Verification

- CI runs the suite on Python 3.12 and 3.14.
