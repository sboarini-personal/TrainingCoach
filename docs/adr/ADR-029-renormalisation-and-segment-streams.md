# ADR-029: Re-normalisation by observation revisions; laps, lengths and the power sensor

- Status: Accepted
- Date: 2026-10-30
- Requirements: FR-ACT-002, INT-FIL-001, FR-RUN-001, FR-CYC-002, FR-SWM-002
- Amends: ADR-024 (observation key), ADR-025 (parser output)

## Context

P9 analytics need laps (pacing, intervals), pool lengths (SWOLF, strokes) and evidence of
how cycling power was measured. P6 parsed none of them, and the athlete's history was
imported with normalisation rules 1.0.0. The data layers allow rebuilding NORMALIZED from
RAW with a newer normaliser, and ADR-024 expected normaliser versions to join the
observation key, but nothing implemented it: an observation is immutable (database trigger)
and a second one for the same source and part was a conflict.

## Decision

- **Segment streams.** Laps (FIT `lap`, TCX `Lap`) and pool lengths (FIT `length`) are
  stored like the time series: content-addressed blobs linked as the LAPS and LENGTHS
  streams, with contracts `laps` and `lengths` 1.0.0. Parser output 1.1.0 carries them,
  plus the device types of the file's sensors (never identifiers). An inconsistent list is
  not stored and the session is still imported.
- **Power sensor as evidence.** The normaliser records the power device a FIT file names
  (`performance.powerSensor`: POWER_METER or SMART_TRAINER; activity contract 1.1.0,
  correctable). It never sets the power source: MEASURED needs the athlete's declared
  reliable sensor too, decided in the EFFECTIVE view (cycling spec section 2).
- **Observation revisions.** Observations stay immutable. Re-normalising a source adds a
  revision (migration 5, its own immutability trigger): same source and part, same
  normaliser, a newer MAJOR.MINOR.PATCH version. Reads return the latest revision; earlier
  ones are kept for lineage. The migration is additive: nothing stored is rewritten.
- **`coach activity reprocess`.** Selects sources with an older normaliser version, parses
  the stored RAW bytes again in the sandbox, and per source (one unit of work) adds the
  revisions and rebuilds the activities holding them: identity, links, status and
  corrections unchanged, metrics recomputed. Deduplication is not run again; a source is
  skipped when it no longer parses, yields another number of sessions, or a session's
  sport or start time changed. Normalisation rules become 1.1.0 for every file format.

## Consequences

- History gains laps, lengths and the power sensor with one command; running it again
  changes nothing.
- A source keeps every revision, so the observation history grows by one document per
  normaliser version; stream blobs of older revisions stay until privacy deletion.
- Provider records (`garmin_activity`) follow the same rule when their normaliser changes.
- Re-parsing stored RAW uses the same sandbox and limits as an upload; RAW never changes.

## Verification

- `tests/unit/test_file_segments.py`, `tests/unit/test_segments.py` (parsing, mapping,
  validation, decoder), `tests/unit/test_reprocess.py` (history, idempotence, skips,
  consent), `tests/contract/test_activity_repository_contract.py` (revisions, owner scope,
  immutability in SQLite), `tests/integration/test_cli_activities.py` (command).
