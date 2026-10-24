"""File ingestion pipeline and parsers for FIT, TCX, GPX and CSV (spec/integration/files.spec.md).

Parsers run sandboxed with resource limits (ADR-019); XML is parsed with DTDs and
external entities disabled (SEC-UPL-002).
"""
