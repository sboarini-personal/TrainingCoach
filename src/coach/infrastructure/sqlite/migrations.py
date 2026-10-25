"""Ordered schema migrations (NFR-REL-004). Applied migrations are never edited.

Any migration that drops or deletes must set ``destructive=True``; it then needs explicit
approval to run (a test enforces the flag).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    name: str
    sql: str
    destructive: bool = False

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.sql.encode("utf-8")).hexdigest()


_INITIAL_SCHEMA: Final = """
CREATE TABLE aggregates (
    kind TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    athlete_id TEXT NOT NULL,
    version INTEGER NOT NULL CHECK (version >= 1),
    schema_version TEXT NOT NULL,
    document TEXT NOT NULL CHECK (json_valid(document)),
    PRIMARY KEY (kind, entity_id)
) STRICT;
CREATE INDEX aggregates_by_owner ON aggregates (athlete_id, kind);

CREATE TABLE raw_sources (
    raw_source_id TEXT PRIMARY KEY,
    athlete_id TEXT NOT NULL,
    sha256 TEXT NOT NULL CHECK (length(sha256) = 64),
    schema_version TEXT NOT NULL,
    document TEXT NOT NULL CHECK (json_valid(document)),
    UNIQUE (athlete_id, sha256)
) STRICT;
CREATE TRIGGER raw_sources_are_immutable BEFORE UPDATE ON raw_sources
BEGIN
    SELECT RAISE(ABORT, 'raw sources are immutable');
END;

CREATE TABLE derived_metric_values (
    value_id TEXT PRIMARY KEY,
    athlete_id TEXT NOT NULL,
    metric_id TEXT NOT NULL,
    subject_kind TEXT NOT NULL,
    subject_key TEXT NOT NULL,
    superseded_by TEXT REFERENCES derived_metric_values (value_id) DEFERRABLE INITIALLY DEFERRED,
    schema_version TEXT NOT NULL,
    document TEXT NOT NULL CHECK (json_valid(document))
) STRICT;
CREATE INDEX derived_by_subject
    ON derived_metric_values (athlete_id, metric_id, subject_kind, subject_key);
CREATE UNIQUE INDEX derived_one_current
    ON derived_metric_values (athlete_id, metric_id, subject_kind, subject_key)
    WHERE superseded_by IS NULL;
CREATE TRIGGER derived_values_are_append_only BEFORE UPDATE ON derived_metric_values
WHEN OLD.superseded_by IS NOT NULL
    OR NEW.superseded_by IS NULL
    OR NEW.value_id IS NOT OLD.value_id
    OR NEW.athlete_id IS NOT OLD.athlete_id
    OR NEW.metric_id IS NOT OLD.metric_id
    OR NEW.subject_kind IS NOT OLD.subject_kind
    OR NEW.subject_key IS NOT OLD.subject_key
    OR NEW.schema_version IS NOT OLD.schema_version
    OR NEW.document IS NOT OLD.document
BEGIN
    SELECT RAISE(ABORT, 'derived metric values are append-only');
END;
"""

_SECURITY_SCHEMA: Final = """
CREATE TABLE identities (
    user_id TEXT PRIMARY KEY,
    athlete_id TEXT NOT NULL UNIQUE,
    is_owner INTEGER NOT NULL CHECK (is_owner IN (0, 1)),
    created_at TEXT NOT NULL
) STRICT;
CREATE UNIQUE INDEX identities_single_owner ON identities (is_owner) WHERE is_owner = 1;

CREATE TABLE audit_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
) STRICT;
CREATE TRIGGER audit_settings_immutable BEFORE UPDATE ON audit_settings
BEGIN
    SELECT RAISE(ABORT, 'audit settings are immutable');
END;
CREATE TRIGGER audit_settings_no_delete BEFORE DELETE ON audit_settings
BEGIN
    SELECT RAISE(ABORT, 'audit settings are immutable');
END;

CREATE TABLE audit_log (
    sequence INTEGER PRIMARY KEY CHECK (sequence >= 1),
    event_id TEXT NOT NULL UNIQUE,
    athlete_id TEXT,
    document TEXT NOT NULL CHECK (json_valid(document)),
    hash TEXT NOT NULL CHECK (length(hash) = 64)
) STRICT;
CREATE INDEX audit_log_by_athlete ON audit_log (athlete_id) WHERE athlete_id IS NOT NULL;
CREATE TRIGGER audit_log_no_delete BEFORE DELETE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'audit log is append-only');
END;
CREATE TRIGGER audit_log_only_forget BEFORE UPDATE ON audit_log
WHEN OLD.athlete_id IS NULL
    OR NEW.athlete_id IS NOT NULL
    OR NEW.sequence IS NOT OLD.sequence
    OR NEW.event_id IS NOT OLD.event_id
    OR NEW.document IS NOT OLD.document
    OR NEW.hash IS NOT OLD.hash
BEGIN
    SELECT RAISE(ABORT, 'audit log is append-only');
END;
"""

MIGRATIONS: Final[tuple[Migration, ...]] = (
    Migration(1, "aggregates, raw sources and derived metric values", _INITIAL_SCHEMA),
    Migration(2, "identities and hash-chained audit log", _SECURITY_SCHEMA),
)
