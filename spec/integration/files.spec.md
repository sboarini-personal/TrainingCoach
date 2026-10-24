# File ingestion

Owner of INT-FIL-001 to INT-FIL-004. Master spec §15 and §16. Security controls in
[../security/uploads.spec.md](../security/uploads.spec.md). Implemented in Phase P6.

## 1. Formats (INT-FIL-001)

| Format | Parser | Notes |
|---|---|---|
| FIT | official Garmin FIT Python SDK (`garmin-fit-sdk`), CRC check enabled | messages used: file_id, session, lap, record, length, activity, set, sport |
| TCX | hardened XML parser (`defusedxml`) | Garmin TrainingCenterDatabase v2 root element required |
| GPX | hardened XML parser (`defusedxml`) | GPX 1.1 (1.0 accepted); heart rate and cadence only from recognised extension namespaces |
| CSV | standard library `csv` with limits | only the application's own documented templates (activity summary, strength sets) |
| ZIP | standard library `zipfile` with limits | only as a container for the formats above (for example Garmin activity downloads) |

Third-party CSV exports are not supported in V1: their formats are not specified here and are
not guessed. A template can be added once a real sample is reviewed.

## 2. Pipeline (INT-FIL-002)

Stages run in this order; each failure stops the pipeline with a typed reason code.

| # | Stage | Rejection code |
|---|---|---|
| 1 | Upload received (size streamed and counted) | `TOO_LARGE` |
| 2 | Extension allow-list | `EXTENSION_NOT_ALLOWED` |
| 3 | Content signature (magic bytes, root element) | `CONTENT_MISMATCH` |
| 4 | Size limits per format | `TOO_LARGE` |
| 5 | Malware scan hook (SEC-UPL-006) | `MALWARE_SUSPECTED` |
| 6 | Quarantine: stored under a random name outside the blob store | none |
| 7 | SHA-256 computed; duplicate check (tier 1) | `DUPLICATE` (not an error) |
| 8 | Safe parser in a sandboxed subprocess with timeout and limits | `PARSE_FAILED`, `PARSE_TIMEOUT`, `LIMIT_EXCEEDED` |
| 9 | Schema validation of the parser output | `SCHEMA_INVALID` |
| 10 | Semantic validation (plausibility, time ordering, units) | `SEMANTIC_INVALID` (or quality anomalies) |
| 11 | Normalisation to canonical model | `NORMALIZATION_FAILED` |
| 12 | Deduplication (tiers 2 to 4) | none |
| 13 | Persistence in one transaction: RawSource + blob + Activity + links | `PERSISTENCE_FAILED` |

## 3. Limits (INT-FIL-003)

Defaults, all configurable. There is never an unlimited mode.

| Limit | Default |
|---|---|
| Maximum file size: FIT | 32 MiB |
| Maximum file size: TCX, GPX | 64 MiB |
| Maximum file size: CSV | 16 MiB |
| Maximum ZIP size (compressed) | 64 MiB |
| Maximum ZIP total uncompressed size | 256 MiB |
| Maximum ZIP compression ratio per member | 100 |
| Maximum ZIP members | 50 |
| Maximum records per file | 2,000,000 |
| Maximum activities per import | 500 |
| Parser timeout | 60 s |
| Parser memory | 1 GiB where the platform supports enforcement; record limits apply everywhere |

## 4. Failure handling (INT-FIL-004)

- A failed import persists nothing except an ingestion record (hash, size, detected type,
  reason code, timestamp) and a DataQualityIssue the athlete can see.
- Rejected file bytes are deleted from quarantine immediately (retention configurable,
  default 0 days).
- Partial activities are never persisted.
- Every outcome emits an `ingestion.*` event (NFR-OBS-001).

## 5. Idempotency

Re-uploading identical bytes returns the existing records (NFR-REL-001). An upload request may
also carry an `Idempotency-Key`.
