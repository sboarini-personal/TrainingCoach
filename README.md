# Personal Multisport Performance Coach

Spec-driven, data-driven coaching for running, cycling, swimming, strength and tennis.

> Status: specification phase complete (P1). No application features yet. See
> [docs/architecture.md](docs/architecture.md#8-delivery-phases).

## What it is

A secure local data platform with deterministic sports metrics, strength, recovery,
planning and policy engines, an adaptive training loop, and an AI coach that interprets and
explains, but never calculates, decides policy or touches credentials.

- **Source of truth:** [spec/](spec/README.md) (requirements index, functional, integration,
  security and safety specs, JSON contracts, acceptance tests).
- **Decisions:** [docs/adr/](docs/adr/README.md).
- **Architecture:** [docs/architecture.md](docs/architecture.md).
- **Threat model:** [docs/threat-model.md](docs/threat-model.md).
- **Privacy:** [docs/privacy.md](docs/privacy.md).
- **Discovery (Phase 0):** [docs/discovery/00-discovery-report.md](docs/discovery/00-discovery-report.md).

## Development

Requirements: Python 3.12 or later and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run pre-commit install
uv run pytest
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run lint-imports
uv run python tools/traceability.py
```

Tests declare the requirements they verify with `@pytest.mark.req("FR-ACT-007")`; unknown IDs
fail the run. The traceability matrix is written to `reports/traceability.md`.

## Data and secrets

- Athlete data is **never** stored in this repository. At runtime it lives in `COACH_DATA_DIR`
  (default `%LOCALAPPDATA%\CoachDeportivo`), outside synced folders.
- Secrets (Garmin Personal API key, LLM key, local API token) live in the OS secret store.
  Never put them in `.env` files, chat, issues or commits.
- Pre-commit hooks scan for secrets and block activity files (`.fit`, `.tcx`, `.gpx`, `.zip`,
  databases) outside `tests/fixtures/`.

## Publishing to GitHub

Create an **empty, private** repository on GitHub (no README, license or .gitignore), then:

```bash
git remote add origin https://github.com/<account>/<repository>.git
git push -u origin main
```

Authentication uses Git Credential Manager (bundled with Git for Windows) or the GitHub CLI.
CI (`.github/workflows/ci.yml`) runs on every push to `main` and on pull requests.
