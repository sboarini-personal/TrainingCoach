# Upload and parser security

Owner of SEC-UPL-001 to SEC-UPL-006. Master spec §15, §16, §62. ADR-019. Pipeline order in
[../integration/files.spec.md](../integration/files.spec.md). Implemented in Phase P6.

All uploaded files and provider downloads are **untrusted**.

## 1. Type validation (SEC-UPL-001)

Extension allow-list: `.fit`, `.tcx`, `.gpx`, `.csv`, `.zip` (case-insensitive). The content
must match the extension:

| Type | Content check |
|---|---|
| FIT | header size byte 12 or 14; bytes 8 to 11 equal ASCII `.FIT`; header and file CRC valid |
| TCX | UTF-8 XML whose root element is `TrainingCenterDatabase` |
| GPX | UTF-8 XML whose root element is `gpx` |
| CSV | UTF-8 text without NUL bytes; header row equal to a supported template |
| ZIP | starts with `PK\x03\x04` |

The declared MIME type is recorded but never trusted on its own.

## 2. XML hardening (SEC-UPL-002)

- Parse TCX and GPX only with `defusedxml`. DTDs (`forbid_dtd=True`), entity declarations and
  external references are forbidden; any of them rejects the file.
- No network resolution of any kind (no schema fetching, no XInclude).
- Element depth and element count are limited (defaults: depth 64, elements 5,000,000).
- AT-XXE-001: files with an external entity, a billion-laughs DTD or a remote DTD are
  rejected, and no network or file access happens.

## 3. Archives (SEC-UPL-003)

- Members are read in memory or streamed; nothing is extracted to a path.
- Rejected: absolute paths, `..` segments, symlinks, encrypted members, nested archives,
  members with disallowed extensions, more members than the limit, any member or total
  uncompressed size above the limit, compression ratio above the limit (checked while
  streaming, not only from headers).
- AT-ZIP-001: a zip bomb and a path-traversal member are rejected without exhausting memory.

## 4. Parser sandbox (SEC-UPL-004)

- Parsers run in a separate process started by the ingestion service, with a wall-clock
  timeout and record limits, and a memory limit where the platform supports it.
- The subprocess receives the quarantined file path and returns structured JSON only. The
  parent validates that JSON against the parser output schema before using it.
- Timeouts, crashes and limit breaches end with a typed rejection and no partial
  persistence.

## 5. Storage paths (SEC-UPL-005)

- Stored files are named by SHA-256. The original filename is kept only as sanitised display
  metadata (control characters removed, maximum 255 characters) and treated as untrusted
  text.
- The blob store is outside the repository and outside synced folders (ADR-014).

## 6. Malware scanning (SEC-UPL-006)

- `MalwareScanner` is a port called after quarantine. A V1 adapter may call the operating
  system's scanner when available (assumption A-11).
- When no scanner is available, the import continues with a `NOT_SCANNED` flag recorded on the
  RawSource and shown to the athlete. It is never silently treated as scanned.
- Parsing is sandboxed regardless of scan results.

## 7. Upload limits

Size, record, activity, timeout and memory limits are in
[../integration/files.spec.md](../integration/files.spec.md) section 3. AT-UPL-001 covers
oversized files, too many records, content-type mismatch and filename path tricks.
