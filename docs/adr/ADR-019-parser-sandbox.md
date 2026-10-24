# ADR-019: Sandboxed file parsers

- Status: Accepted
- Date: 2026-10-24
- Requirements: SEC-UPL-002, SEC-UPL-003, SEC-UPL-004, INT-FIL-003

## Context

FIT is a binary format; TCX and GPX are XML; archives can be bombs. A parser bug or a
pathological file can exhaust CPU or memory or crash the main process.

## Decision

Parsers run in a separate process with a wall-clock timeout, record limits and a memory limit
where the platform supports it. The subprocess returns structured JSON only; the parent
validates it against a schema before use. XML uses `defusedxml` with DTDs forbidden; archives
are read without extraction and with streaming size and ratio checks.

## Consequences

- Parsing has process start-up overhead; acceptable for personal volumes.
- Parser output schemas become internal contracts with tests.

## Verification

- AT-UPL-001, AT-XXE-001, AT-ZIP-001.
