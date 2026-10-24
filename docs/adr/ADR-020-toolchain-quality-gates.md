# ADR-020: Toolchain, quality gates and GitHub CI

- Status: Accepted (athlete decision D-3, 2026-10-24)
- Date: 2026-10-24
- Requirements: NFR-TRC-002, SEC-SEC-006, SEC-INJ-003

## Context

The repository will be pushed to GitHub. Quality and security properties must be checked on
every change, not by memory.

## Decision

| Gate | Tool | Where |
|---|---|---|
| Lint, including security rules and naive-datetime rules | ruff (`S`, `DTZ` and others) | pre-commit, CI |
| Formatting | ruff format | pre-commit, CI |
| Types | mypy strict | CI |
| Architecture | import-linter contracts | CI |
| Tests and spec integrity | pytest with requirement markers | CI |
| Traceability | `tools/traceability.py` | CI artifact |
| Secrets | detect-secrets with baseline | pre-commit, CI |
| Athlete data | pre-commit hook blocking data files | pre-commit |
| Dependencies | pip-audit; `uv.lock` committed | CI |

- CI: GitHub Actions on push to `main` and pull requests, Python 3.12 and 3.14, read-only
  `contents` permission.
- The repository on GitHub should be **private**: it describes a personal health-data system
  and its threat model.
- Hooks run tools through `uv run` so versions come from `uv.lock`.

## Consequences

- Action versions are pinned to major tags; pinning to commit SHAs is a follow-up hardening
  task once the remote exists (threat model).

## Verification

- CI workflow `.github/workflows/ci.yml`.
