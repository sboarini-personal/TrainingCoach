# ADR-025: File ingestion mechanism: sandbox process, FIT SDK, archives and streams

- Status: Accepted
- Date: 2026-10-25
- Requirements: INT-FIL-001 to INT-FIL-004, SEC-UPL-001 to SEC-UPL-006, SEC-SEC-005
- Implements: ADR-019 (sandboxed parsers)

## Context

ADR-019 requires parsers in a separate process with a timeout, record limits and a memory
limit where the platform supports it. Implementing it on the athlete's Windows PC raised
concrete questions: how to start the process, how to enforce memory on Windows, how to keep
the worker from seeing application state, which FIT decoder to trust, how to store archive
members and time series, and what to do without an antivirus integration.

## Decision

- **Worker process.** The base interpreter (`sys._base_executable`) runs the worker in
  isolated mode (`-I -X utf8`) with the application's source and site-packages passed as
  arguments, a minimal environment (only `SYSTEMROOT` on Windows) and the quarantine
  directory as working directory. On Windows a venv's `python.exe` is a launcher that starts
  the real interpreter as a child, which would race with, and be blocked by, the
  one-process limit.
- **Limits before input.** The worker reads its request (format, quarantined path, limits)
  from standard input only after the parent has placed it in a Windows Job Object (process
  memory limit, one active process, kill on close, die on unhandled exception). On POSIX the
  worker sets `RLIMIT_AS` itself before opening the file. The response is one JSON document
  read under an output-size limit; standard error is discarded.
- **FIT decoder.** The official Garmin FIT Python SDK (`garmin-fit-sdk`) decodes FIT with the
  CRC check enabled and a message listener enforcing the record limit; tests generate FIT
  files with the same SDK's encoder. Its licence (FIT Protocol License Agreement) allows use
  in software the licensee creates; the SDK is a declared dependency, not vendored or
  redistributed.
- **XML.** `defusedxml` streaming parser with DTDs, entities and external references
  forbidden; depth and element counts limited; finished track points removed from the tree.
- **Archives.** The entry count is read from the end-of-central-directory record before
  `zipfile` parses the directory; sizes and ratios are checked from headers and again while
  reading. Each member becomes its own RAW source (byte-exact), so the same FIT uploaded
  inside or outside a ZIP is one RAW source; the archive is not stored.
- **Time series.** Each session's samples are stored as a canonical JSON blob (contract
  `time-series`) in the athlete's content-addressed blob store and linked as the TIME_SERIES
  stream. Positions are not copied (data minimisation); they remain only in RAW.
- **Malware scanning.** V1 ships no scanner adapter; every upload is NOT_SCANNED and shown
  as such (A-11). Calling an antivirus on a managed corporate PC needs the athlete's review.
- **CSV template rows** are the athlete's own declarations: ATHLETE_ENTRY observations of a
  FILE_UPLOAD source (files spec 8.4).

## Consequences

- A parser exploit would still run with the OS user's file and network access; isolation,
  the minimal environment, the one-process limit and memory limits reduce, but do not
  remove, that exposure (threat model R-21).
- Job Object creation can fail (for example under unusual host policies); the sandbox then
  continues without the Windows memory limit and record limits still apply
  (`ParserSandbox.memory_limit_enforced` reports it).
- Process start-up costs about 0.3 s per file, acceptable for personal volumes.

## Verification

- `tests/integration/test_parser_sandbox.py` (timeout, crash, output limit, memory limit with
  a positive control, child process blocked with a positive control, minimal environment).
- AT-UPL-001, AT-XXE-001, AT-ZIP-001 (`tests/security/test_upload_security.py`).
- Mutation check: 31 of 31 mutants of the P6 controls make a test fail.
