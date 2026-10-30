# Personal Multisport Performance Coach

Spec-driven, data-driven coaching for running, cycling, swimming, strength and tennis.

> Status: P6 complete (domain core, storage, security, canonical activities, file import).
> No training features yet. See [docs/architecture.md](docs/architecture.md#8-delivery-phases).

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

## Command line

```bash
uv run coach init                                   # create the installation and its local token
uv run coach secret set garmin-api-key --expires-on 2027-01-23   # hidden input
uv run coach secret list                            # credentials and expiry state
uv run coach token rotate                           # replace the local API token
uv run coach audit verify                           # check the audit hash chain
uv run coach consent grant activities               # allow activity data to be processed
uv run coach profile timezone America/Argentina/Buenos_Aires
uv run coach import run.fit ride.tcx export.zip     # FIT, TCX, GPX, CSV template, ZIP
uv run coach activity list                          # most recent activities
uv run coach activity export activities.csv         # CSV activity template
uv run coach activity reprocess                     # re-read imported files with newer rules
uv run coach activity correct <activity-id> subjective.rpe 7 --reason "felt hard"
uv run coach issue list                             # data problems and failed imports
uv run coach garmin sync                            # pull new activities (Personal API)
uv run coach garmin sync --since 2026-09-01         # re-scan from a date
uv run coach garmin status                          # freshness, errors, capabilities, key expiry
uv run coach load week                              # this week by sport, and across sports
uv run coach load week --week 2026-W44              # a given ISO week
uv run coach load weeks --count 12                  # one line per week
uv run coach activity show <activity-id>            # an activity with speed, pace and zones
uv run coach profile hr-zones 100 130 150 165 180 --valid-from 2020-01-01
uv run coach profile birth-date 1980-01-31          # synthetic example date
uv run coach profile hr-zones 51 59 71 81 90 100 --age-predicted --valid-from 2020-01-01
uv run coach metrics recompute                      # after upgrading, or a zone change
uv run coach profile sensor CYCLING_POWER_METER --reliable   # power becomes MEASURED
uv run coach profile ftp 250 --on 2026-10-01 --measured
uv run coach analysis trend run.efficiency_hr --weeks 12
uv run coach analysis trend swim.swolf_avg --stroke FREESTYLE
uv run coach strength exercises "sentadilla bulgara"   # the catalogue, by ID, name or alias
uv run coach strength alias "Búlgaras" ex.bulgarian_split_squat
uv run coach strength sets <activity-id>                # executed sets of a strength session
uv run coach strength log <activity-id> "back squat" --set 2 --reps 7 --rpe 8.5
uv run coach strength relabel <activity-id> fit:squat ex.goblet_squat
uv run coach strength week --week 2026-W44              # hard sets per muscle, volume load
uv run coach strength progress                          # progress, hold or regress per exercise
uv run coach strength interference --days 7
```

Files imported before an upgrade of the normalisation rules (P9 added laps, pool lengths
and the power sensor, P10 strength sets) are read again with `coach activity reprocess`: about 0.7 s per file,
once; it adds a revision of each observation and never changes the stored files.

Imports run each file through a sandboxed parser; rejected files leave only an ingestion
record and an issue (`coach issue list`). The CSV template header is documented in
`spec/integration/files.spec.md` section 8.4.

Metrics are calculated in code (`spec/functional/metrics.spec.md` section 9). A weekly
total with activities that lack the value is shown as `>= known (n unknown)`, never as
the total. Distances are never added across sports. Time in heart-rate zones needs a zone
model (`coach profile hr-zones`, which recomputes the metrics); activities imported before
a metric existed get it with `coach metrics recompute`. With `--age-predicted` the zones
are percentages of 220 minus your age on each activity's day (from `coach profile
birth-date`): an estimate, shown as such, and never used to prescribe intensity
(ADR-028).

Sport analytics (P9) come from each session's time series, laps and pool lengths: running
heart-rate drift, pace variability, interval degradation and distance per heartbeat;
cycling work, power zones, fade and interval consistency, only when the ride's power is
measured by a device you declared reliable; pool swimming SWOLF, distance per stroke and
pace variability per stroke, and pace and stroke fatigue within sets; tennis regional load
from duration, RPE and match. A session's RPE is recorded with `coach activity correct`.
`coach analysis trend` compares only sessions of the same kind (surface and terrain, indoor
or outdoor, pool length and stroke) and never mixes groups.

Strength (P10): a watch's strength sets are imported with the activity; an exercise the
watch names without an exact catalogue equivalent stays unknown until you name it with
`coach strength relabel`. Your entries (`coach strength log`) correct the watch set of the
same exercise and set number, or add a set; RPE and RIR come only from you, and you can
ignore the watch's sets for a session (`coach strength watch-sets <id> ignore`). Volume
load counts sets with known repetitions and load (a session without any is left out of the
week's total and counted); a hard set is RIR 4 or less, else RPE 6
or more, so sets without either show as unknown effort in `coach strength week`.
`coach strength progress` needs targets (`--target-reps`, `--target-rpe`) to decide;
`coach strength interference` flags combinations such as heavy legs the day before hard
intervals. Pain counts for a region only when its location is the region's name (for
example "rodilla"). These are load-management rules, not medical advice.

`coach garmin sync` uses the Garmin Personal API key stored with `coach secret set
garmin-api-key` (create it in Garmin Connect, under Personal API key). It pulls the last 8
weeks the first time, then what is new; it never writes to Garmin. When Garmin is
unavailable the command says so, keeps what was already synced and leaves local history and
file import working.

Data goes to `COACH_DATA_DIR` (default `%LOCALAPPDATA%\CoachDeportivo`); the command refuses
OneDrive, Dropbox and Google Drive folders. Secrets go to the Windows Credential Manager.

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
