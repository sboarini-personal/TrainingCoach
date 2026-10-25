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

Precisions (P6):

- FIT: stage 3 checks the header size, the `.FIT` marker, the header CRC when it is not zero
  (the FIT protocol allows 0 for "not computed") and that the header and data size fit in the
  file. The file CRC is verified by the FIT decoder inside the sandbox (stage 8): a mismatch
  is `PARSE_FAILED` (`CRC_ERROR`).
- TCX and GPX: stage 3 finds the root element with a scanner that reads only the start of the
  file and skips the XML declaration, comments, processing instructions and a document type
  declaration, without parsing it; the DTD itself is rejected by the hardened parser
  (`PARSE_FAILED`, AT-XXE-001). The parser checks the root namespace (TCX v2; GPX 1.1 or 1.0).
- CSV: stage 3 compares the first line with the supported template headers.

## 2. XML hardening (SEC-UPL-002)

- Parse TCX and GPX only with `defusedxml`. DTDs (`forbid_dtd=True`), entity declarations and
  external references are forbidden; any of them rejects the file.
- No network resolution of any kind (no schema fetching, no XInclude).
- Element depth and element count are limited (defaults: depth 64, elements 5,000,000).
- AT-XXE-001: files with an external entity, a billion-laughs DTD or a remote DTD are
  rejected, and no network or file access happens.

## 3. Archives (SEC-UPL-003)

- Members are read in memory or streamed; nothing is extracted to a path. Validated member
  bytes are quarantined under random names for the sandboxed parser; archive paths never
  become file system paths.
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

Mechanism (P6):

- The subprocess is the same Python interpreter in isolated mode (`-I`: no user site, no
  `PYTHON*` variables, neither the script directory nor the working directory on the import
  path), with a minimal environment (no application variables) and the quarantine directory
  as working directory. It loads the application's own parser module by path.
- The request (format, quarantined path, limits) is sent on standard input only after the
  limits are in place, so no file byte is read before they apply:
  - Windows: a Job Object with a per-process memory limit, a limit of one active process
    (parsers cannot start children) and kill-on-close;
  - POSIX: the worker sets `RLIMIT_AS` before reading the request's file.
- The response is one JSON document on standard output, read with the output-size limit;
  standard error is discarded and never logged (it could echo file content).
- A crash (non-zero exit without a response) is `PARSE_FAILED` (`PARSER_CRASHED`); invalid
  JSON or an oversized response is `SCHEMA_INVALID` or `LIMIT_EXCEEDED`.

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
- V1 ships no scanner adapter (assumption A-11, decided in P6): invoking an antivirus on a
  managed corporate PC needs the athlete's review first. Every upload is therefore
  `NOT_SCANNED` and shown as such.

## 7. Upload limits

Size, record, activity, timeout and memory limits are in
[../integration/files.spec.md](../integration/files.spec.md) section 3. AT-UPL-001 covers
oversized files, too many records, content-type mismatch and filename path tricks.
